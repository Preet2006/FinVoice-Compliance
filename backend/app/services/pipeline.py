"""
Orchestrates the full per-call AI pipeline (steps 0-4 of
MASTER_IMPLEMENTATION_PLAN §5.1). This is the *only* place that sequences
transcription -> speaker labelling -> compliance/sentiment -> scoring, and
it is idempotent: re-running it for a call clears that call's prior
TranscriptSegment/Violation rows first, so retries never duplicate rows or
collide on the (call_id, seq) unique constraint.

Never fabricates results: everything here re-derives from the call's
actual stored audio file. A metadata-only call (has_audio=False) is
rejected outright, never silently "processed" with fake data (§5.6).
"""

import logging
import threading
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.models import Call, Review, TranscriptSegment, Violation
from app.rules import check_r1_violation, get_rule_by_code
from app.services import llm_service, reviewer_service, scoring_service, transcription_service, unified_analysis_service

# Statuses from which (re)processing is allowed to start.
logger = logging.getLogger(__name__)

PROCESSABLE_STATUSES = {"queued", "ready", "failed"}
IN_PROGRESS_STATUSES = {"transcribing", "analyzing"}


class PipelineError(Exception):
    """Base class for pipeline-level errors the API layer maps to HTTP codes."""


class CallNotEligibleError(PipelineError):
    """has_audio=False, or the call doesn't exist — never processed."""


class ReviewerWorkExistsError(PipelineError):
    """The call carries reviewer work (corrected speakers, manual findings or
    review decisions). Reprocessing re-transcribes the audio and replaces the
    stored segments, so that work cannot be preserved; it is refused instead of
    being erased."""


class AlreadyProcessingError(PipelineError):
    """A concurrent run already claimed this call; caller should not retry
    immediately or start a second concurrent run."""


def _clear_prior_results(db: Session, call_id: str) -> None:
    # SQLite does not enforce ON DELETE CASCADE by default, so Review rows
    # (FK'd to violation.id) must be deleted explicitly before their parent
    # Violation rows, or a reprocess hits a UNIQUE constraint failure when
    # new Violation/Review pairs are inserted with reused ids.
    violation_ids = [
        v_id for (v_id,) in db.query(Violation.id).filter(Violation.call_id == call_id).all()
    ]
    if violation_ids:
        db.query(Review).filter(Review.violation_id.in_(violation_ids)).delete(
            synchronize_session=False
        )
    db.query(Violation).filter(Violation.call_id == call_id).delete()
    db.query(TranscriptSegment).filter(TranscriptSegment.call_id == call_id).delete()
    db.flush()


def _add_violation(db: Session, call_id: str, idx: int, rule_code: str, segment_id, severity, start_time_seconds, quote, explanation, confidence, needs_speaker_review: bool = False, evidence_status: str = "verified", evidence_reason=None) -> None:
    violation_id = f"v-{call_id}-{idx:04d}"
    db.add(
        Violation(
            id=violation_id,
            call_id=call_id,
            rule_code=rule_code,
            segment_id=segment_id,
            severity=severity,
            start_time_seconds=start_time_seconds,
            quote=quote,
            explanation=explanation,
            confidence=confidence,
            needs_speaker_review=needs_speaker_review,
            evidence_status=evidence_status,
            evidence_reason=evidence_reason,
        )
    )
    db.add(Review(id=f"r-{violation_id}", violation_id=violation_id, decision="pending"))


# Calls this process is working on right now. A call whose status says "in
# progress" but which is NOT in this set was abandoned by a worker that
# crashed or restarted, so it is safe to recover. This assumes a single
# server process (as in docker-compose); several processes would need a
# shared lease instead.
_ACTIVE_CALLS: set = set()
_ACTIVE_LOCK = threading.Lock()


def _acquire(call_id: str) -> None:
    with _ACTIVE_LOCK:
        if call_id in _ACTIVE_CALLS:
            raise AlreadyProcessingError(f"Call {call_id} is already being processed.")
        _ACTIVE_CALLS.add(call_id)


def _release(call_id: str) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE_CALLS.discard(call_id)


NO_EVIDENCE_TEXT = "(no verifiable evidence)"
MAX_EVIDENCE_REASON_CHARS = 1000


def _unverified_reason(reason: str, entry: dict, has_segment: bool) -> str:
    """Validation failure plus, where there is one, the model's proposal."""
    text = (reason[:1].upper() + reason[1:] + ".") if reason else "The evidence failed validation."
    cited = entry.get("segment_index")
    if not has_segment and isinstance(cited, int) and not isinstance(cited, bool):
        text += f" The analysis cited segment {cited}."
    proposed = entry.get("quote")
    if isinstance(proposed, str) and proposed.strip():
        text += f" Proposed quote: “{proposed.strip()}”"
    return text[:MAX_EVIDENCE_REASON_CHARS]


def _store_unverified_findings(db: Session, call_id: str, compliance, labelled, segment_id_by_seq, first_idx: int) -> None:
    """
    Keeps every finding whose evidence failed validation, with an explicit
    'unverified' status, instead of discarding it. The displayed quote is
    always text from the stored transcript - never the model's unverified
    quote, which goes only into evidence_reason. A finding with no usable
    segment is stored with no segment and no invented evidence.
    """
    text_by_seq = {s.seq: s.text for s in labelled}
    seen = set()
    idx = first_idx
    for rejection in getattr(compliance, "rejected_findings", None) or []:
        if rejection.get("kind") != llm_service.KIND_INVALID_EVIDENCE:
            continue
        entry = rejection.get("entry")
        if not isinstance(entry, dict):
            continue
        rule = get_rule_by_code(entry.get("rule_code"))
        if rule is None:
            continue
        seq = entry.get("segment_index")
        segment_id = None
        if isinstance(seq, int) and not isinstance(seq, bool):
            segment_id = segment_id_by_seq.get(seq)
        proposed = entry.get("quote") if isinstance(entry.get("quote"), str) else ""
        key = (rule["code"], seq if segment_id else None, " ".join(proposed.lower().split()))
        if key in seen:
            continue
        seen.add(key)

        confidence = entry.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0.0 <= float(confidence) <= 1.0:
            confidence = None
        _add_violation(
            db,
            call_id,
            idx=idx,
            rule_code=rule["code"],
            segment_id=segment_id,
            severity=rule["default_severity"],
            start_time_seconds=_segment_start(labelled, seq) if segment_id else None,
            quote=text_by_seq[seq] if segment_id else NO_EVIDENCE_TEXT,
            explanation=(str(entry.get("explanation") or "")[:500] or None),
            confidence=float(confidence) if confidence is not None else None,
            evidence_status="unverified",
            evidence_reason=_unverified_reason(rejection.get("reason") or "", entry, segment_id is not None),
        )
        idx += 1


def process_call(call_id: str, db: Session) -> Call:
    """
    Reprocess a call. New results replace the old ones only after the whole
    analysis has succeeded; any failure leaves the previous transcript,
    violations and manager review decisions exactly as they were.
    """
    _acquire(call_id)  # a second concurrent run is rejected before touching the DB
    try:
        return _process_call(call_id, db)
    finally:
        _release(call_id)


def _fail(db: Session, call: Call, restore_status: str, had_results: bool, reason: str) -> Call:
    """Abandons the attempt without touching stored results. With earlier
    results the call returns to its previous status (they are still valid)
    and the reason records the failed attempt; without any it is 'failed'."""
    db.rollback()
    call.status = restore_status
    call.failure_reason = (
        f"Reprocessing failed; the previous results were kept. {reason}" if had_results else reason
    )
    db.commit()
    return call


def _blocked_message(work: list) -> str:
    return (
        "This call has reviewer work that reprocessing would erase (" + ", ".join(work) + "). "
        "Reprocessing re-transcribes the audio and replaces the transcript, so it is blocked to protect that work."
    )


def _process_call(call_id: str, db: Session) -> Call:
    call = db.query(Call).filter(Call.id == call_id).first()
    if call is None:
        raise CallNotEligibleError(f"Call {call_id} does not exist.")
    if not call.has_audio:
        raise CallNotEligibleError(
            f"Call {call_id} has no audio (has_audio=False) and cannot be processed."
        )
    if call.status in IN_PROGRESS_STATUSES:
        # We hold the in-process lock, so no worker here is running it: the
        # previous run was abandoned (crash, restart). Recover it.
        logger.warning("Recovering call %s abandoned in status '%s'.", call_id, call.status)
    elif call.status not in PROCESSABLE_STATUSES:
        raise CallNotEligibleError(f"Call {call_id} has unexpected status '{call.status}'.")

    had_results = (
        db.query(TranscriptSegment.id).filter(TranscriptSegment.call_id == call_id).first() is not None
    )
    restore_status = "ready" if had_results else "failed"

    if had_results:
        work = reviewer_service.reviewer_work(db, call_id)
        if work:
            if call.status in IN_PROGRESS_STATUSES:
                # An abandoned run (crash, restart). Replacement is atomic, so the stored results are
                # intact: un-stick the call so a reviewer can work on it, instead of leaving it
                # unprocessable and uneditable at once.
                call.status = restore_status
                db.commit()
            raise ReviewerWorkExistsError(_blocked_message(work))

    call.status = "transcribing"
    call.failure_reason = None
    db.commit()

    if had_results:
        # A reviewer action may have committed between the check above and the status change.
        # Look again now that reviewer actions are refused, before any provider call is spent.
        work = reviewer_service.reviewer_work(db, call_id)
        if work:
            call.status = restore_status
            db.commit()
            raise ReviewerWorkExistsError(_blocked_message(work))

    try:
        return _run_analysis(db, call, call_id, had_results, restore_status)
    except Exception:
        logger.exception("Unexpected error while processing call %s", call_id)
        return _fail(
            db, call, restore_status, had_results,
            "Unexpected processing error; see the server log.",
        )


def _run_analysis(db: Session, call: Call, call_id: str, had_results: bool, restore_status: str) -> Call:
    def fail(reason: str) -> Call:
        return _fail(db, call, restore_status, had_results, reason)

    # --- Step 0: deterministic R1 check (metadata only, no LLM). ---
    r1 = check_r1_violation(call.start_time)

    # --- Step 1: transcription (Groq Whisper). ---
    audio_filename = Path(call.audio_path).name
    audio_full_path = Path(settings.audio_dir) / audio_filename
    try:
        audio_bytes = audio_full_path.read_bytes()
    except OSError:
        return fail(f"Audio file not found or unreadable: {audio_filename}")

    try:
        raw_segments = transcription_service.transcribe(audio_bytes, audio_filename)
    except transcription_service.TranscriptionFailedError as exc:
        return fail(str(exc))

    call.status = "analyzing"
    db.commit()

    # --- Steps 2-3: speaker labelling + compliance + sentiment, in ONE whole-conversation analysis
    # (unified_analysis_service; Groq LLM). It is the only analysis path: there is no fallback to another
    # one, so a failure is recorded as a failure. Nothing is written to the results tables until all
    # of it has succeeded. ---
    try:
        labelled, compliance = unified_analysis_service.analyse_call(raw_segments, call)
    except (llm_service.LLMProviderError, llm_service.LLMResponseValidationError) as exc:
        return fail(str(exc))

    # --- Step 4: replace the old results with the new ones in ONE
    # transaction. An error anywhere in here is rolled back by the caller,
    # leaving the previous results intact. ---
    try:
        _replace_results(db, call, call_id, r1, labelled, compliance)
    except ReviewerWorkExistsError:
        # Reviewer work was committed while the analysis ran. Nothing was replaced; the new
        # results are discarded and the reviewer's work stays.
        return fail(
            "Reviewer work was added while the analysis was running, so the new results were discarded "
            "and that work was kept."
        )
    return call


def _replace_results(db: Session, call: Call, call_id: str, r1, labelled, compliance) -> None:
    # Take the write lock first (a no-op update of the call row), THEN look for reviewer work, in the
    # same transaction that deletes and rewrites the results. A reviewer action that committed while
    # the analysis ran is seen here; one that arrives later waits for this transaction and then
    # finds the call's new state. Either way nothing a reviewer did can be deleted unseen.
    db.query(Call).filter(Call.id == call_id).update({Call.status: Call.status}, synchronize_session=False)
    work = reviewer_service.reviewer_work(db, call_id)
    if work:
        raise ReviewerWorkExistsError(_blocked_message(work))
    _clear_prior_results(db, call_id)

    if r1 is not None:
        _add_violation(
            db,
            call_id,
            idx=0,
            rule_code="R1",
            segment_id=None,
            severity=r1["severity"],
            start_time_seconds=r1["start_time_seconds"],
            quote=r1["quote"],
            explanation=r1["explanation"],
            confidence=r1["confidence"],
        )
        db.flush()

    segment_rows = []
    for s in labelled:
        segment_id = f"s-{call_id}-{s.seq:04d}"
        row = TranscriptSegment(
            id=segment_id,
            call_id=call_id,
            seq=s.seq,
            speaker=s.speaker,
            start_time=s.start,
            end_time=s.end,
            text=s.text,
            sentiment=None,
            speaker_confidence=s.confidence,
            speaker_uncertain=s.speaker_uncertain,
        )
        db.add(row)
        segment_rows.append((s.seq, segment_id))
    db.flush()
    segment_id_by_seq = dict(segment_rows)

    speaker_by_seq = {s.seq: s.speaker for s in labelled}
    speaker_confidence_by_seq = {s.seq: s.confidence for s in labelled}
    seen_findings = set()

    for idx, v in enumerate(compliance.violations, start=1):
        rule = get_rule_by_code(v.rule_code)
        if rule is None:
            continue  # defensive: rules.py is the only source of truth for severity
        segment_id = segment_id_by_seq.get(v.segment_seq)
        if segment_id is None:
            continue  # defensive: should already be filtered by llm_service

        # A finding identical to one already persisted (same rule, segment
        # and quote) adds no evidence, so it is not stored twice. Findings
        # with a different quote on the same segment are kept: each quote is
        # distinct evidence, and scoring counts them as one incident.
        finding_key = (v.rule_code, v.segment_seq, " ".join(v.quote.lower().split()))
        if finding_key in seen_findings:
            continue
        seen_findings.add(finding_key)

        explanation = v.explanation
        cited_speaker = speaker_by_seq.get(v.segment_seq)
        cited_confidence = speaker_confidence_by_seq.get(v.segment_seq)
        # Two independent, additive reasons a violation's speaker
        # attribution is uncertain — neither discards the violation or its
        # evidence; both only gate automatic scoring (scoring_service) and
        # surface a note for a human reviewer to check against the audio.
        non_agent_cited = cited_speaker is not None and cited_speaker != "agent"
        low_confidence = llm_service.is_uncertain_confidence(cited_confidence) or any(
            s.seq == v.segment_seq and s.speaker_uncertain for s in labelled
        )
        model_flagged = bool(getattr(v, "needs_review", False))
        needs_speaker_review = non_agent_cited or low_confidence or model_flagged

        if non_agent_cited:
            # Defense-in-depth: R2-R6 are agent-misconduct rules (per the
            # compliance prompt's own "only flag the agent" instruction),
            # but that instruction is advisory, not enforced. If the cited
            # segment is nonetheless labelled 'borrower', don't trust the
            # attribution silently and don't discard a possibly-real
            # violation either.
            explanation = (
                f"[SPEAKER ATTRIBUTION FLAG: cited segment is labelled "
                f"'{cited_speaker}', not 'agent' — {rule['code']} requires agent "
                f"misconduct; verify against the audio before trusting this "
                f"speaker label.] {explanation or ''}".strip()
            )
        elif low_confidence:
            # Agent-labelled, but the labelling model itself was not
            # confident (or didn't say) — same "verify before trusting"
            # treatment, worded for this different reason.
            shown_confidence = "not provided" if cited_confidence is None else f"{cited_confidence:.2f}"
            explanation = (
                f"[LOW-CONFIDENCE SPEAKER ATTRIBUTION: model confidence for "
                f"this segment's speaker label is {shown_confidence} — verify "
                f"against the audio before trusting this speaker label.] "
                f"{explanation or ''}".strip()
            )
        elif model_flagged:
            # The analysis itself was unsure (unclear words or speaker); held
            # for a manager instead of being deducted automatically.
            explanation = (
                f"[FLAGGED FOR REVIEW: the analysis was not certain about "
                f"this finding — check the audio before relying on it.] "
                f"{explanation or ''}".strip()
            )

        _add_violation(
            db,
            call_id,
            idx=idx,
            rule_code=v.rule_code,
            segment_id=segment_id,
            severity=rule["default_severity"],  # never the LLM's own severity judgment
            start_time_seconds=_segment_start(labelled, v.segment_seq),
            quote=v.quote,
            explanation=explanation,
            confidence=v.confidence,
            needs_speaker_review=needs_speaker_review,
        )

    _store_unverified_findings(
        db, call_id, compliance, labelled, segment_id_by_seq, first_idx=len(compliance.violations) + 1
    )

    call.sentiment = compliance.borrower_sentiment
    call.agent_stayed_calm = compliance.agent_stayed_calm
    db.flush()

    # --- Deterministic scoring. ---
    call.score = scoring_service.compute_score(call_id, db)
    call.status = "ready"
    call.failure_reason = None
    db.commit()


def _segment_start(labelled, seq: int) -> float:
    for s in labelled:
        if s.seq == seq:
            return s.start
    return 0.0
