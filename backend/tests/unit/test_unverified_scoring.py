"""Scoring with unverified evidence: held until confirmed, never merged or double counted."""

from app.models import Violation
from app.services.scoring_service import compute_score
from tests.unit.test_scoring_service import (  # noqa: F401  (db is a fixture)
    SEVERITY,
    add_finding,
    add_segment,
    add_violation,
    counted_ids,
    db,
    statuses,
)
from app.models import TranscriptSegment


def add_unverified(db, idx, rule="R3", seq=40, decision="pending", needs_speaker_review=False):
    segment_id = f"seg-{seq}"
    if db.get(TranscriptSegment, segment_id) is None:
        add_segment(db, seq)
    add_violation(db, idx, rule, SEVERITY[rule], decision=decision, segment_id=segment_id,
                  needs_speaker_review=needs_speaker_review)
    db.query(Violation).filter(Violation.id == f"v-{idx}").update({"evidence_status": "unverified"})
    db.commit()


def test_pending_unverified_finding_never_deducts(db):
    add_unverified(db, 1)
    assert compute_score("CALL-SCORE-01", db) == 100
    assert counted_ids(db) == set()


def test_confirmed_unverified_finding_deducts_like_any_confirmed_finding(db):
    add_unverified(db, 1, decision="confirmed")
    assert compute_score("CALL-SCORE-01", db) == 70


def test_dismissed_unverified_finding_never_deducts(db):
    add_unverified(db, 1, decision="false_alarm")
    assert compute_score("CALL-SCORE-01", db) == 100


def test_unverified_finding_is_not_merged_into_an_adjacent_verified_incident(db):
    add_finding(db, 1, "R3", 12)  # verified
    add_unverified(db, 2, "R3", seq=13, decision="confirmed")  # adjacent segment, same rule
    s = statuses(db)
    assert s["v-1"].incident_group_id is None and s["v-2"].incident_group_id is None
    assert compute_score("CALL-SCORE-01", db) == 100 - 60  # two separate findings


def test_confirmed_unverified_repeating_a_counting_verified_finding_is_not_counted_twice(db):
    add_finding(db, 1, "R3", 12)
    add_unverified(db, 2, "R3", seq=12, decision="confirmed")  # same rule, same segment
    assert compute_score("CALL-SCORE-01", db) == 70
    assert counted_ids(db) == {"v-1"}


def test_confirmed_unverified_on_the_same_segment_under_a_different_rule_still_counts(db):
    add_finding(db, 1, "R2", 12)
    add_unverified(db, 2, "R3", seq=12, decision="confirmed")
    assert compute_score("CALL-SCORE-01", db) == 100 - 60


def test_unverified_findings_are_excluded_from_overlap_notes(db):
    add_finding(db, 1, "R3", 7)
    add_unverified(db, 2, "R4", seq=7)
    assert statuses(db)["v-1"].overlapping_rule_codes == ()


def test_a_finding_with_no_segment_can_be_unverified_and_never_deducts_while_pending(db):
    add_violation(db, 1, "R3", SEVERITY["R3"])  # no segment, like a placeholder
    db.query(Violation).filter(Violation.id == "v-1").update({"evidence_status": "unverified"})
    db.commit()
    assert compute_score("CALL-SCORE-01", db) == 100


def test_existing_verified_and_speaker_hold_rules_are_unchanged(db):
    add_finding(db, 1, "R3", 3)
    add_finding(db, 2, "R3", 20, needs_speaker_review=True)
    add_finding(db, 3, "R2", 30, decision="false_alarm")
    assert compute_score("CALL-SCORE-01", db) == 70
