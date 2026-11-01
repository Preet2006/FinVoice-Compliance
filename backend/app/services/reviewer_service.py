"""
Reviewer actions that change a call's analysis: correcting a speaker label and
adding a finding the analysis missed. Both are manager / head actions, both are
recorded in the append-only reviewer_audit table, and neither ever overwrites
what the AI produced:

* A corrected speaker is stored beside the AI's label (`speaker_override`), not
  over it; `TranscriptSegment.effective_speaker` is what scoring and the API use.
* A manual finding is a separate row (`source = 'manual'`). Its evidence is read
  from the stored transcript by word position with the same resolver the
  analysis uses, so a reviewer cannot attach text that is not in the transcript.

Because a reprocess re-transcribes the audio and replaces the stored segments,
reviewer work cannot be carried across it safely. `reviewer_work` reports what
exists so the pipeline can refuse to reprocess instead of silently erasing it.
"""

import json
from datetime import datetime

from sqlalchemy.orm import Session, joinedload

from app.models import Call, Review, ReviewerAudit, TranscriptSegment, Violation
from app.rules import get_rule_by_code
from app.services import evidence_span_service as spans
from app.services import scoring_service

SPEAKERS = ("agent", "borrower", "third_party", "unknown", "mixed")
MANUAL_RULES = ("R2", "R3", "R4", "R5", "R6")  # R1 is computed from call metadata, never typed in
MAX_COMMENT_CHARS = 1000


# While a call is being (re)processed its transcript is about to be replaced, so reviewer work
# done now would be erased. (Same states as pipeline.IN_PROGRESS_STATUSES; kept here to avoid a
# circular import, and checked equal by a test.)
PROCESSING_STATUSES = ("transcribing", "analyzing")


class ReviewerActionError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def guard_not_processing(db: Session, call: Call) -> None:
    """Refuses (409) a reviewer write while the call is being processed.

    The write lock is taken FIRST (a no-op update of the call row), and only then is the status
    read. Either this action holds the lock and the pipeline's replacement step waits for it and
    then sees the new reviewer work, or the pipeline holds it and this action waits and then sees
    the call's new status. A status read made before taking the lock could go stale."""
    db.query(Call).filter(Call.id == call.id).update({Call.status: Call.status}, synchronize_session=False)
    db.refresh(call)
    if call.status in PROCESSING_STATUSES:
        raise ReviewerActionError(
            409, "CALL_BEING_PROCESSED",
            "This call is being re-analysed right now, and the new analysis would replace anything you change. "
            "Wait for it to finish, then try again.",
        )


def _audit(db: Session, call_id: str, action: str, target_id: str, actor_id: str, detail: dict) -> None:
    db.add(ReviewerAudit(call_id=call_id, action=action, target_id=target_id, actor_id=actor_id,
                         detail=json.dumps(detail, ensure_ascii=False), created_at=datetime.utcnow()))


def invalidate_completed_review(db: Session, call: Call, actor_id: str, reason: str) -> None:
    """A reviewer change after sign-off means the call is no longer fully reviewed:
    the sign-off is cleared (and the clearing audited) so it can be completed again."""
    if call.review_completed_at is None:
        return
    _audit(db, call.id, "review_reopened", call.id, actor_id, {"reason": reason, "previously_completed_by": call.review_completed_by})
    call.review_completed_by = None
    call.review_completed_at = None


def complete_review(db: Session, call: Call, actor) -> Call:
    """Manager / head sign-off. Needs an analysed call and a decision on every finding
    that has not been dismissed; a call with no findings can be completed as it is."""
    guard_not_processing(db, call)
    if call.status != "ready":
        raise ReviewerActionError(409, "NOT_ANALYSED", "Only an analysed call can be reviewed.")
    if call.review_completed_at is not None:
        return call  # already signed off: idempotent
    undecided = (
        db.query(Review).join(Violation, Review.violation_id == Violation.id)
        .filter(Violation.call_id == call.id, Review.decision == "pending").count()
    )
    if undecided:
        raise ReviewerActionError(
            409, "UNDECIDED_FINDINGS",
            f"{undecided} finding(s) still need a decision. Confirm each one or mark it a false alarm, then complete the review.",
        )
    call.review_completed_by = actor.id
    call.review_completed_at = datetime.utcnow()
    _audit(db, call.id, "review_completed", call.id, actor.id, {"score": call.score})
    db.commit()
    return call


def reviewer_work(db: Session, call_id: str) -> list:
    """Human-readable list of reviewer work that a reprocess would erase."""
    found = []
    if db.query(Call.id).filter(Call.id == call_id, Call.review_completed_at.isnot(None)).first():
        found.append("a completed review sign-off")
    corrections = db.query(TranscriptSegment).filter(
        TranscriptSegment.call_id == call_id, TranscriptSegment.speaker_override.isnot(None)).count()
    if corrections:
        found.append(f"{corrections} corrected speaker label(s)")
    manual = db.query(Violation).filter(Violation.call_id == call_id, Violation.source == "manual").count()
    if manual:
        found.append(f"{manual} manually added finding(s)")
    decided = (
        db.query(Review).join(Violation, Review.violation_id == Violation.id)
        .filter(Violation.call_id == call_id, Violation.source == "ai", Review.decision != "pending").count()
    )
    if decided:
        found.append(f"{decided} review decision(s)")
    return found


def correct_speaker(db: Session, call: Call, segment_id: str, new_speaker, comment, actor) -> TranscriptSegment:
    guard_not_processing(db, call)
    if new_speaker not in SPEAKERS:
        raise ReviewerActionError(422, "INVALID_SPEAKER", f"speaker must be one of {list(SPEAKERS)}.")
    comment = (comment or "").strip()
    if len(comment) > MAX_COMMENT_CHARS:
        raise ReviewerActionError(422, "COMMENT_TOO_LONG", f"comment must be at most {MAX_COMMENT_CHARS} characters.")
    segment = db.query(TranscriptSegment).filter(TranscriptSegment.id == segment_id, TranscriptSegment.call_id == call.id).first()
    if segment is None:
        raise ReviewerActionError(404, "SEGMENT_NOT_FOUND", f"Segment {segment_id} does not belong to call {call.id}.")

    if new_speaker != "agent":
        # A finding a reviewer added says "the agent said this"; moving the segment away
        # from the agent while it still counts would contradict the reviewer's own work.
        for v in db.query(Violation).options(joinedload(Violation.review)).filter(
                Violation.segment_id == segment.id, Violation.source == "manual"):
            if (v.review.decision if v.review else "pending") != "false_alarm":
                raise ReviewerActionError(409, "SEGMENT_HAS_MANUAL_FINDING", f"Manual finding {v.id} relies on this segment being the agent's. Mark it a false alarm first, then change the speaker.")

    before = segment.effective_speaker
    segment.speaker_override = new_speaker
    segment.speaker_override_by = actor.id
    segment.speaker_override_at = datetime.utcnow()
    segment.speaker_override_comment = comment or None

    held = []
    if new_speaker != "agent":
        # R2-R6 are agent-misconduct rules: an undecided AI finding on a segment
        # now known not to be the agent's stops deducting until a reviewer rules on it.
        for v in db.query(Violation).options(joinedload(Violation.review)).filter(
                Violation.segment_id == segment.id, Violation.source == "ai"):
            if (v.review.decision if v.review else "pending") == "pending" and not v.needs_speaker_review:
                v.needs_speaker_review = True
                held.append(v.id)

    invalidate_completed_review(db, call, actor.id, "a speaker label was corrected")
    _audit(db, call.id, "speaker_correction", segment.id, actor.id, {
        "segment_seq": segment.seq, "ai_speaker": segment.speaker, "previous_effective": before,
        "new_speaker": new_speaker, "comment": comment or None, "findings_held": held,
        "speaker_uncertain": bool(segment.speaker_uncertain)})
    db.flush()
    call.score = scoring_service.compute_score(call.id, db)
    db.commit()
    return segment


def create_manual_finding(db: Session, call: Call, actor, *, rule_code, segment_id, word_start, word_end, comment, quote=None) -> Violation:
    guard_not_processing(db, call)
    if rule_code == "R1":
        raise ReviewerActionError(422, "R1_NOT_MANUAL", "R1 (calling hours) is evaluated from the call's start time, not added by hand.")
    if rule_code not in MANUAL_RULES or get_rule_by_code(rule_code) is None:
        raise ReviewerActionError(422, "INVALID_RULE", f"rule_code must be one of {list(MANUAL_RULES)}.")
    comment = (comment or "").strip()
    if not comment:
        raise ReviewerActionError(422, "COMMENT_REQUIRED", "A comment explaining why this is a violation is required.")
    if len(comment) > MAX_COMMENT_CHARS:
        raise ReviewerActionError(422, "COMMENT_TOO_LONG", f"comment must be at most {MAX_COMMENT_CHARS} characters.")
    segment = db.query(TranscriptSegment).filter(TranscriptSegment.id == segment_id, TranscriptSegment.call_id == call.id).first()
    if segment is None:
        raise ReviewerActionError(404, "SEGMENT_NOT_FOUND", f"Segment {segment_id} does not belong to call {call.id}.")
    if segment.effective_speaker != "agent":
        raise ReviewerActionError(422, "SPEAKER_NOT_AGENT", "This segment is not labelled as the agent. Correct the speaker first; these rules apply to the agent's words.")

    try:
        span = spans.resolve_reference(segment.seq, segment.text, word_start, word_end)
    except spans.InvalidReference as exc:
        raise ReviewerActionError(422, "INVALID_EVIDENCE", str(exc)[:1].upper() + str(exc)[1:] + ".")
    if isinstance(quote, str) and quote.strip() and " ".join(quote.split()).lower() != " ".join(span.quote.split()).lower():
        raise ReviewerActionError(422, "EVIDENCE_MISMATCH", "The quote does not match the transcript words at that position; the transcript text is used as evidence.")

    existing = (
        db.query(Violation).options(joinedload(Violation.review))
        .filter(Violation.call_id == call.id, Violation.rule_code == rule_code, Violation.segment_id == segment.id).all()
    )
    for v in existing:
        if (v.review.decision if v.review else "pending") != "false_alarm":
            raise ReviewerActionError(409, "DUPLICATE_FINDING", f"{rule_code} is already flagged on this segment ({v.id}); review that finding instead of adding another.")

    n = db.query(Violation).filter(Violation.call_id == call.id, Violation.source == "manual").count() + 1
    violation_id = f"v-{call.id}-m{n:03d}"
    while db.query(Violation.id).filter(Violation.id == violation_id).first():
        n += 1
        violation_id = f"v-{call.id}-m{n:03d}"
    now = datetime.utcnow()
    rule = get_rule_by_code(rule_code)
    db.add(Violation(
        id=violation_id, call_id=call.id, rule_code=rule_code, segment_id=segment.id,
        severity=rule["default_severity"], start_time_seconds=segment.start_time, quote=span.quote,
        explanation=comment, confidence=None, needs_speaker_review=False, evidence_status="verified",
        source="manual", created_by=actor.id, created_at=now,
    ))
    # The reviewer's own finding is their decision: recorded as confirmed.
    db.add(Review(id=f"r-{violation_id}", violation_id=violation_id, decision="confirmed",
                  reviewer_id=actor.id, comment=comment, reviewed_at=now))
    invalidate_completed_review(db, call, actor.id, "a manual finding was added")
    _audit(db, call.id, "manual_finding", violation_id, actor.id, {
        "rule_code": rule_code, "segment_id": segment.id, "segment_seq": segment.seq,
        "word_start": span.word_start, "word_end": span.word_end, "quote": span.quote, "comment": comment})
    db.flush()
    call.score = scoring_service.compute_score(call.id, db)
    db.commit()
    return db.query(Violation).filter(Violation.id == violation_id).one()
