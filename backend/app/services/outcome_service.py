"""
Call-level analysis outcome: one value that says what the analysis actually
established, so a score of 100 is never mistaken for "verified clean".

Computed on read (never stored) so it cannot go stale when a manager reviews
a finding. Precedence, first match wins:

  1. not_analyzed     queued, processing, or no audio
  2. failed           analysis failed and no usable results exist
  3. incomplete       unverified evidence remains unresolved (even if other
                      violations were found - the counts show both)
  4. violations_found at least one confirmed / score-counting violation
  5. pending_review   held findings await review
  6. clean            analysed successfully, nothing counting or unresolved

A failed reprocess that kept valid earlier results does NOT change the
outcome: it is computed from the preserved results and reported alongside
`last_reprocess_failed`.
"""

from dataclasses import dataclass
from typing import Iterable, Optional

from sqlalchemy.orm import Session, joinedload

from app.models import TranscriptSegment, Violation
from app.rules import get_severity_weight
from app.services import scoring_service

NOT_ANALYZED = "not_analyzed"
FAILED = "failed"
INCOMPLETE = "incomplete"
VIOLATIONS_FOUND = "violations_found"
PENDING_REVIEW = "pending_review"
CLEAN = "clean"

_NOT_ANALYZED_STATUSES = {"metadata_only", "queued", "transcribing", "analyzing"}


@dataclass(frozen=True)
class AnalysisOutcome:
    outcome: str
    counted_violations: int = 0
    findings_pending_review: int = 0  # held for speaker review, undecided
    findings_unverified: int = 0  # evidence failed validation, undecided
    # The numeric score is correct for what it counts but not final while
    # unresolved findings remain.
    provisional_score: bool = False
    last_reprocess_failed: bool = False
    # not_applicable until analysed; then pending_review until a manager or head
    # signs the review off (analysis finishing never sets 'reviewed').
    review_status: str = "not_applicable"
    findings_total: int = 0  # findings not dismissed as false alarms
    # The highest severity among the findings that deduct from the score; None when nothing deducts.
    top_severity: Optional[str] = None


def _decision(v) -> str:
    return v.review.decision if v.review else "pending"


def compute_outcome(call, violations: Iterable, has_results: bool) -> AnalysisOutcome:
    if not call.has_audio or call.status in _NOT_ANALYZED_STATUSES:
        return AnalysisOutcome(NOT_ANALYZED)
    if call.status == "failed" and not has_results:
        return AnalysisOutcome(FAILED)

    rows = list(violations)
    statuses = scoring_service.evaluate_violations(rows)
    counted = sum(1 for s in statuses.values() if s.counts_toward_score)
    top = max((v.severity for v in rows if statuses[v.id].counts_toward_score), key=get_severity_weight, default=None)
    unverified = sum(
        1 for v in rows if getattr(v, "evidence_status", "verified") == "unverified" and _decision(v) == "pending"
    )
    held = sum(
        1
        for v in rows
        if v.needs_speaker_review
        and getattr(v, "evidence_status", "verified") != "unverified"
        and _decision(v) == "pending"
    )
    last_failed = bool(call.failure_reason)

    if not has_results:
        # "ready" with no transcript at all cannot be certified clean.
        outcome = INCOMPLETE
    elif unverified > 0:
        outcome = INCOMPLETE
    elif counted > 0:
        outcome = VIOLATIONS_FOUND
    elif held > 0:
        outcome = PENDING_REVIEW
    else:
        outcome = CLEAN

    return AnalysisOutcome(
        review_status="reviewed" if getattr(call, "review_completed_at", None) is not None else "pending_review",
        findings_total=sum(1 for v in rows if _decision(v) != "false_alarm"),
        outcome=outcome,
        counted_violations=counted,
        top_severity=top,
        findings_pending_review=held,
        findings_unverified=unverified,
        provisional_score=unverified > 0 or held > 0,
        last_reprocess_failed=last_failed,
    )


def outcomes_for_calls(db: Session, calls: list) -> dict:
    """Outcomes for many calls with two queries (used by the call list)."""
    ids = [c.id for c in calls]
    if not ids:
        return {}
    violations = (
        db.query(Violation)
        .options(joinedload(Violation.segment), joinedload(Violation.review))
        .filter(Violation.call_id.in_(ids))
        .all()
    )
    by_call: dict = {}
    for v in violations:
        by_call.setdefault(v.call_id, []).append(v)
    with_results = {
        row[0] for row in db.query(TranscriptSegment.call_id).filter(TranscriptSegment.call_id.in_(ids)).distinct()
    }
    return {c.id: compute_outcome(c, by_call.get(c.id, []), c.id in with_results) for c in calls}


def outcome_for_call(db: Session, call) -> AnalysisOutcome:
    return outcomes_for_calls(db, [call])[call.id]
