"""
Dashboard aggregates, computed on read from the stored calls, findings and
review decisions - nothing is cached, estimated or invented.

Scope: only the calls the requesting role may see (access_control.visible_caller_ids),
so a caller sees their own calls, a manager their team's, the head everything.

Findings are counted from ANALYSED calls only (status 'ready'). Calls that have
only metadata have no analysis; a finding stored against one (the seeded demo
database holds a few start-time artefacts) is not evidence of a breach.

What a number means:
  calls_awaiting_review  analysed calls a manager or head has not yet signed off.
  reviewed_calls         analysed calls whose review a manager or head completed. Finishing the
                         AI analysis never makes a call 'reviewed'.
  verified_violations  findings whose evidence matched the transcript (or that a
                       reviewer added), excluding ones a reviewer dismissed.
  counted_violations   the subset that actually deducts from a score (after
                       incident grouping and the hold / unverified rules).
  unverified_findings  findings whose evidence could not be verified; they never
                       deduct and are reported separately, never as breaches.
Precision and recall need audio-verified ground truth, which the application
does not store, so they are reported as unavailable instead of being computed
from script-derived expectations.
"""

from collections import defaultdict
from typing import Optional

from sqlalchemy.orm import Session, joinedload

from app.models import Call, Violation
from app.services import evaluation_metrics, scoring_service
from app.services.access_control import visible_caller_ids

ACCURACY_UNAVAILABLE = (
    "Precision and recall need audio-verified ground-truth labels, and none are stored. "
    "Expected labels written from call scripts are not ground truth, so no accuracy is computed from them."
)
TOP_RISKY = 5


def _dismissed(v) -> bool:
    return v.review is not None and v.review.decision == "false_alarm"


def build_dashboard(db: Session, role: str, user_id: str) -> dict:
    ids: Optional[set] = visible_caller_ids(role, user_id, db)
    query = db.query(Call).options(joinedload(Call.caller), joinedload(Call.office))
    if ids is not None:
        query = query.filter(Call.caller_id.in_(ids or {""}))
    calls = query.all()
    analysed = [c for c in calls if c.status == "ready"]

    violations = []
    if analysed:
        violations = (
            db.query(Violation)
            .options(joinedload(Violation.review), joinedload(Violation.segment))
            .filter(Violation.call_id.in_([c.id for c in analysed]))
            .all()
        )
    by_call = defaultdict(list)
    for v in violations:
        by_call[v.call_id].append(v)

    counted_ids = set()
    for rows in by_call.values():
        for vid, status in scoring_service.evaluate_violations(rows).items():
            if status.counts_toward_score:
                counted_ids.add(vid)

    rule_counts, caller_rows, office_rows = defaultdict(int), {}, {}
    verified = unverified = 0
    review = {"confirmed": 0, "false_alarm": 0, "pending": 0}
    call_by_id = {c.id: c for c in analysed}

    def bucket(table, key, name):
        return table.setdefault(key, {"id": key, "name": name, "violations": 0, "counted": 0, "calls": 0, "_scores": []})

    for c in analysed:
        for table, key, name in ((caller_rows, c.caller_id, c.caller.name), (office_rows, c.office_id, c.office.name)):
            b = bucket(table, key, name)
            b["calls"] += 1
            if c.score is not None:
                b["_scores"].append(c.score)

    for v in violations:
        decision = v.review.decision if v.review else "pending"
        review[decision] = review.get(decision, 0) + 1
        if v.evidence_status == "unverified":
            unverified += 1
            continue
        if _dismissed(v):
            continue
        verified += 1
        rule_counts[v.rule_code] += 1
        c = call_by_id[v.call_id]
        for table, key, name in ((caller_rows, c.caller_id, c.caller.name), (office_rows, c.office_id, c.office.name)):
            b = bucket(table, key, name)
            b["violations"] += 1
            b["counted"] += 1 if v.id in counted_ids else 0

    def finish(table):
        out = []
        for b in table.values():
            scores = b.pop("_scores")
            b["average_score"] = round(sum(scores) / len(scores), 1) if scores else None
            out.append(b)
        return out

    callers, offices = finish(caller_rows), finish(office_rows)
    callers.sort(key=lambda b: (-b["violations"], b["name"]))
    offices.sort(key=lambda b: (-b["violations"], b["name"]))
    risky = sorted((b for b in callers if b["counted"] > 0), key=lambda b: (-b["counted"], b["average_score"] if b["average_score"] is not None else 101, b["name"]))[:TOP_RISKY]

    pending = sum(1 for c in calls if c.status in ("queued", "transcribing", "analyzing") and c.has_audio)
    failed = sum(1 for c in calls if c.status == "failed")
    reviewed = sum(1 for c in analysed if c.review_completed_at is not None)
    awaiting = len(analysed) - reviewed
    scores = [c.score for c in analysed if c.score is not None]
    return {
        "scope": role,
        "total_calls": len(calls),
        "calls_analyzed": len(analysed),
        "calls_pending": pending,
        "calls_failed": failed,
        "calls_awaiting_review": awaiting,
        "reviewed_calls": reviewed,
        "verified_violations": verified,
        "counted_violations": sum(1 for v in violations if v.id in counted_ids),
        "unverified_findings": unverified,
        "average_score": round(sum(scores) / len(scores), 1) if scores else None,
        "breaches_by_rule": [{"rule_code": r, "count": rule_counts.get(r, 0)} for r in ("R1", "R2", "R3", "R4", "R5", "R6")],
        "breaches_by_caller": callers,
        "breaches_by_office": offices,
        "top_risky_callers": risky,
        "review_outcomes": review,
        "accuracy": {"available": False, "reason": ACCURACY_UNAVAILABLE},
        "evaluation": evaluation_metrics.build(db, analysed),
    }
