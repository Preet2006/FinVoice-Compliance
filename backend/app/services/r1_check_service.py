"""
R1 (calling hours) as a metadata result that is available WITHOUT a recording,
computed on read, plus detection of stored R1 evidence that no longer matches
the call's start time.

This is deliberately NOT a violation. Everything built on violations - the
violation list, the review endpoint (keyed by a violation id), the call score,
the call outcome, the severity filter - reads stored rows. A violation made up
on read would have no id to review, would not count toward the score, and would
duplicate the stored R1 row once the call is analysed. So this is a separate,
read-only description of what the metadata says; it never creates, changes or
hides a violation row or a review decision.

Stale evidence: when a call is analysed, the pipeline stores an R1 row whose
quote embeds the clock time at that moment ("Call started at 19:40:16 IST ...").
If the call's start_time is corrected afterwards, that stored text is stale.
`stored_evidence_current` / `needs_refresh` expose it; fixing it is a separate,
review-preserving step (see the report), never a silent rewrite.
"""

from dataclasses import dataclass
from typing import Iterable, Optional

from app.rules import R1_OK, R1_UNRESOLVED, R1_VIOLATION, evaluate_r1

STORED_NOT_APPLICABLE = "not_applicable"  # the call has not been analysed: no R1 row is expected yet
STORED_NONE = "none"
STORED_PRESENT = "present"


@dataclass
class R1Check:
    status: str  # ok | violation | unresolved - from the start time alone
    reason: str
    evaluated_start_time: Optional[str]
    stored_finding: str
    stored_evidence_current: Optional[bool]  # None when there is nothing to compare
    needs_refresh: bool


def r1_check(call, violations: Iterable) -> R1Check:
    evaluation = evaluate_r1(call.start_time)
    stamp = call.start_time.isoformat() if getattr(call, "start_time", None) is not None and hasattr(call.start_time, "isoformat") else None

    analysed = getattr(call, "status", None) == "ready"
    stored = [v for v in violations if v.rule_code == "R1"]

    if not analysed:
        return R1Check(evaluation.status, evaluation.reason, stamp, STORED_NOT_APPLICABLE, None, False)
    if evaluation.status == R1_UNRESOLVED:
        return R1Check(evaluation.status, evaluation.reason, stamp, STORED_PRESENT if stored else STORED_NONE, None, False)

    expected_quote = evaluation.violation["quote"] if evaluation.status == R1_VIOLATION else None
    if evaluation.status == R1_VIOLATION:
        current = any(v.quote == expected_quote for v in stored)  # a row exists and quotes the current time
    else:
        current = not stored  # in hours now: any stored R1 row is outdated
    return R1Check(
        evaluation.status, evaluation.reason, stamp,
        STORED_PRESENT if stored else STORED_NONE, current, not current,
    )
