from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import AppUser, Call, Office, Review, Rule, TranscriptSegment, Violation
from app.rules import RULES
from app.services.scoring_service import (
    MAX_INCIDENT_GAP_SECONDS,
    MAX_INCIDENT_SEGMENTS,
    compute_score,
    evaluate_violations,
)


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    office = Office(id="o-01", name="Jogeshwari")
    caller = AppUser(id="u-1", name="Test Caller", role="caller", office_id="o-01")
    session.add_all([office, caller])
    for r in RULES:
        session.add(Rule(**r))
    session.add(
        Call(
            id="CALL-SCORE-01",
            caller_id="u-1",
            office_id="o-01",
            account_type="personal_loan",
            start_time=datetime(2026, 10, 1, 10, 0, 0),
            status="ready",
        )
    )
    session.commit()
    yield session
    session.close()
    engine.dispose()


def add_violation(
    db, idx, rule_code, severity, decision="pending", needs_speaker_review=False, segment_id=None
):
    vid = f"v-{idx}"
    db.add(
        Violation(
            id=vid,
            call_id="CALL-SCORE-01",
            rule_code=rule_code,
            segment_id=segment_id,
            severity=severity,
            start_time_seconds=0.0,
            quote="quote",
            confidence=0.9,
            needs_speaker_review=needs_speaker_review,
        )
    )
    db.add(Review(id=f"r-{vid}", violation_id=vid, decision=decision))
    db.commit()


def test_no_violations_scores_100(db):
    assert compute_score("CALL-SCORE-01", db) == 100


def test_one_critical_scores_70(db):
    add_violation(db, 1, "R3", "critical")
    assert compute_score("CALL-SCORE-01", db) == 70


def test_one_critical_false_alarm_scores_100(db):
    add_violation(db, 1, "R3", "critical", decision="false_alarm")
    assert compute_score("CALL-SCORE-01", db) == 100


def test_confirming_does_not_change_score(db):
    add_violation(db, 1, "R3", "critical", decision="pending")
    pending_score = compute_score("CALL-SCORE-01", db)
    db.query(Review).filter(Review.violation_id == "v-1").update({"decision": "confirmed"})
    db.commit()
    confirmed_score = compute_score("CALL-SCORE-01", db)
    assert pending_score == confirmed_score == 70


def test_four_criticals_clamped_to_0(db):
    for i in range(1, 5):
        add_violation(db, i, "R3", "critical")
    assert compute_score("CALL-SCORE-01", db) == 0


def test_mixed_severities_and_one_false_alarm(db):
    add_violation(db, 1, "R3", "critical")  # -30
    add_violation(db, 2, "R1", "high")  # -15
    add_violation(db, 3, "R6", "medium", decision="false_alarm")  # removed
    assert compute_score("CALL-SCORE-01", db) == 100 - 30 - 15


# --- Phase 1: needs_speaker_review gating ----------------------------------

def test_uncertain_pending_violation_not_deducted(db):
    add_violation(db, 1, "R3", "critical", decision="pending", needs_speaker_review=True)
    assert compute_score("CALL-SCORE-01", db) == 100


def test_uncertain_confirmed_violation_deducted_normally(db):
    add_violation(db, 1, "R3", "critical", decision="confirmed", needs_speaker_review=True)
    assert compute_score("CALL-SCORE-01", db) == 70


def test_uncertain_false_alarm_violation_not_deducted(db):
    add_violation(db, 1, "R3", "critical", decision="false_alarm", needs_speaker_review=True)
    assert compute_score("CALL-SCORE-01", db) == 100


def test_certain_pending_violation_still_deducted_as_before(db):
    # Regression guard: ordinary (non-uncertain) violations are scored
    # exactly as before this change.
    add_violation(db, 1, "R3", "critical", decision="pending", needs_speaker_review=False)
    assert compute_score("CALL-SCORE-01", db) == 70


def test_mix_of_certain_and_uncertain_violations(db):
    add_violation(db, 1, "R3", "critical", decision="pending", needs_speaker_review=False)  # -30
    add_violation(db, 2, "R1", "high", decision="pending", needs_speaker_review=True)  # not deducted yet
    assert compute_score("CALL-SCORE-01", db) == 70


# --- Incident deduplication ------------------------------------------------
# Severity always comes from the rule, as in production (pipeline.py).

SEVERITY = {r["code"]: r["default_severity"] for r in RULES}


def add_segment(db, seq, speaker="agent", start=None, end=None, speaker_uncertain=False, speaker_confidence=0.95):
    start = float(seq * 2) if start is None else start
    end = start + 2.0 if end is None else end
    segment_id = f"seg-{seq}"
    db.add(
        TranscriptSegment(
            id=segment_id,
            call_id="CALL-SCORE-01",
            seq=seq,
            speaker=speaker,
            start_time=start,
            end_time=end,
            text=f"segment {seq}",
            speaker_uncertain=speaker_uncertain,
            speaker_confidence=speaker_confidence,
        )
    )
    db.commit()
    return segment_id


@pytest.mark.parametrize("speaker", ["third_party", "unknown", "mixed"])
def test_new_non_agent_states_hold_pending_findings_from_score(db, speaker):
    add_segment(db, 1, speaker=speaker)
    add_finding(db, 1, "R3", 1)
    assert compute_score("CALL-SCORE-01", db) == 100


def test_explicit_segment_uncertainty_holds_pending_agent_finding(db):
    add_segment(db, 1, speaker="agent", speaker_uncertain=True)
    add_finding(db, 1, "R3", 1)
    assert compute_score("CALL-SCORE-01", db) == 100


@pytest.mark.parametrize("confidence", [None, 0.59])
def test_pending_agent_finding_with_missing_or_low_confidence_is_held(db, confidence):
    add_segment(db, 1, speaker="agent")
    db.query(TranscriptSegment).filter_by(id="seg-1").update({"speaker_confidence": confidence})
    db.commit()
    add_finding(db, 1, "R3", 1)
    assert compute_score("CALL-SCORE-01", db) == 100


def test_pending_agent_finding_at_confidence_boundary_is_scored(db):
    add_segment(db, 1, speaker="agent")
    db.query(TranscriptSegment).filter_by(id="seg-1").update({"speaker_confidence": 0.6})
    db.commit()
    add_finding(db, 1, "R3", 1)
    assert compute_score("CALL-SCORE-01", db) == 70


def test_confirming_low_confidence_finding_uses_existing_human_attestation_rule(db):
    add_segment(db, 1, speaker="agent")
    db.query(TranscriptSegment).filter_by(id="seg-1").update({"speaker_confidence": 0.59})
    db.commit()
    add_finding(db, 1, "R3", 1)
    db.query(Review).filter_by(violation_id="v-1").update({"decision": "confirmed"})
    db.commit()
    assert compute_score("CALL-SCORE-01", db) == 70


def test_correcting_uncertain_speaker_to_agent_does_not_confirm_finding(db):
    add_segment(db, 1, speaker="unknown")
    add_finding(db, 1, "R3", 1, needs_speaker_review=True)
    segment = db.get(TranscriptSegment, "seg-1")
    segment.speaker_override = "agent"
    db.commit()
    assert db.get(Review, "r-v-1").decision == "pending"
    assert compute_score("CALL-SCORE-01", db) == 100


def add_finding(db, idx, rule_code, seq, **kwargs):
    """A violation on segment `seq`, creating the segment if needed."""
    segment_id = f"seg-{seq}"
    if db.get(TranscriptSegment, segment_id) is None:
        add_segment(db, seq)
    add_violation(db, idx, rule_code, SEVERITY[rule_code], segment_id=segment_id, **kwargs)


def statuses(db):
    rows = db.query(Violation).filter(Violation.call_id == "CALL-SCORE-01").all()
    return evaluate_violations(rows)


def counted_ids(db):
    return {vid for vid, s in statuses(db).items() if s.counts_toward_score}


def test_policy_constants_are_the_documented_values():
    assert MAX_INCIDENT_GAP_SECONDS == 1.0
    assert MAX_INCIDENT_SEGMENTS == 3


# Should count once


def test_exact_duplicates_same_rule_same_segment_count_once(db):
    add_finding(db, 1, "R3", 5)
    add_finding(db, 2, "R3", 5)  # different quote, same rule + segment
    assert compute_score("CALL-SCORE-01", db) == 70
    s = statuses(db)
    assert sum(x.counts_toward_score for x in s.values()) == 1
    assert s["v-1"].incident_group_id == s["v-2"].incident_group_id is not None


def test_adjacent_same_speaker_same_rule_count_once(db):
    add_finding(db, 1, "R5", 4)
    add_finding(db, 2, "R5", 5)
    assert compute_score("CALL-SCORE-01", db) == 100 - 15
    s = statuses(db)
    assert s["v-1"].incident_group_id == s["v-2"].incident_group_id is not None


def test_continuous_police_and_jail_statement_counts_as_one_r3_incident(db):
    # "...to main police bhej dunga [seg 12] aur aapko jail jaana padega [seg 13]"
    # is one sentence that Whisper split in two.
    add_finding(db, 1, "R3", 12)
    add_finding(db, 2, "R3", 13)
    assert compute_score("CALL-SCORE-01", db) == 70


def test_carrier_is_deterministic_and_stable(db):
    add_finding(db, 1, "R3", 12)
    add_finding(db, 2, "R3", 13)
    assert counted_ids(db) == {"v-1"}  # lowest segment, then id
    assert counted_ids(db) == counted_ids(db)


# Should count separately


def test_non_consecutive_segments_count_separately(db):
    add_finding(db, 1, "R3", 16)
    add_finding(db, 2, "R3", 18)  # seq 17 between them
    assert compute_score("CALL-SCORE-01", db) == 100 - 60
    assert statuses(db)["v-1"].incident_group_id is None


def test_adjacent_segments_with_different_speakers_count_separately(db):
    add_segment(db, 1, speaker="agent")
    add_segment(db, 2, speaker="borrower")
    add_violation(db, 1, "R3", "critical", segment_id="seg-1")
    add_violation(db, 2, "R3", "critical", segment_id="seg-2")
    assert compute_score("CALL-SCORE-01", db) == 100 - 60


def test_gap_exactly_at_threshold_merges_and_just_above_does_not(db):
    # seq 1 ends at 2.0. seq 2 starts exactly 1.0s later: merges.
    add_segment(db, 1, start=0.0, end=2.0)
    add_segment(db, 2, start=3.0, end=5.0)  # gap 1.00
    # seq 10 ends at 12.0. seq 11 starts 1.01s later: does not.
    add_segment(db, 10, start=10.0, end=12.0)
    add_segment(db, 11, start=13.01, end=15.0)  # gap 1.01
    for idx, seq in enumerate((1, 2, 10, 11), start=1):
        add_violation(db, idx, "R3", "critical", segment_id=f"seg-{seq}")
    # {1,2} merge (-30); 10 and 11 stay separate (-60)
    assert compute_score("CALL-SCORE-01", db) == 100 - 30 - 60


def test_overlapping_timestamps_still_count_as_one_utterance(db):
    add_segment(db, 1, start=0.0, end=2.5)
    add_segment(db, 2, start=2.0, end=4.0)  # starts before the previous one ends
    add_violation(db, 1, "R3", "critical", segment_id="seg-1")
    add_violation(db, 2, "R3", "critical", segment_id="seg-2")
    assert compute_score("CALL-SCORE-01", db) == 70


def test_segment_starting_before_the_previous_one_does_not_merge(db):
    add_segment(db, 1, start=5.0, end=7.0)
    add_segment(db, 2, start=1.0, end=3.0)  # out of order: data is not trustworthy
    add_violation(db, 1, "R3", "critical", segment_id="seg-1")
    add_violation(db, 2, "R3", "critical", segment_id="seg-2")
    assert compute_score("CALL-SCORE-01", db) == 100 - 60


def test_two_distinct_r3_incidents_far_apart_count_separately(db):
    add_finding(db, 1, "R3", 3)
    add_finding(db, 2, "R3", 4)  # one incident with seg 3
    add_finding(db, 3, "R3", 20)  # a genuinely separate threat later in the call
    assert compute_score("CALL-SCORE-01", db) == 100 - 60


# Different rules are never merged


def test_r3_and_r4_on_the_same_segment_both_count_and_are_flagged(db):
    add_finding(db, 1, "R3", 7)
    add_finding(db, 2, "R4", 7)
    assert compute_score("CALL-SCORE-01", db) == 100 - 60
    s = statuses(db)
    assert s["v-1"].counts_toward_score and s["v-2"].counts_toward_score
    assert s["v-1"].overlapping_rule_codes == ("R4",)
    assert s["v-2"].overlapping_rule_codes == ("R3",)
    assert s["v-1"].incident_group_id is None


def test_r3_and_r4_on_adjacent_segments_both_count(db):
    add_finding(db, 1, "R3", 7)
    add_finding(db, 2, "R4", 8)
    assert compute_score("CALL-SCORE-01", db) == 100 - 60
    assert statuses(db)["v-1"].incident_group_id is None


def test_r2_and_r3_on_the_same_segment_both_count_and_are_not_flagged(db):
    add_finding(db, 1, "R2", 7)
    add_finding(db, 2, "R3", 7)
    assert compute_score("CALL-SCORE-01", db) == 100 - 60
    assert statuses(db)["v-1"].overlapping_rule_codes == ()


def test_dismissed_counterpart_clears_the_overlap_note(db):
    add_finding(db, 1, "R3", 7)
    add_finding(db, 2, "R4", 7, decision="false_alarm")
    assert statuses(db)["v-1"].overlapping_rule_codes == ()
    assert compute_score("CALL-SCORE-01", db) == 70


def test_different_rules_never_merge_even_with_identical_segments(db):
    add_finding(db, 1, "R5", 4)
    add_finding(db, 2, "R6", 4)
    add_finding(db, 3, "R2", 5)
    assert compute_score("CALL-SCORE-01", db) == 100 - 15 - 7 - 30


# Group size cap


@pytest.mark.parametrize(
    "run_length, expected_deductions",
    [(1, 1), (2, 1), (3, 1), (4, 2), (5, 2), (6, 2), (7, 3)],
)
def test_runs_longer_than_the_cap_are_split_greedily(db, run_length, expected_deductions):
    for i in range(run_length):
        add_finding(db, i + 1, "R3", 30 + i)
    assert compute_score("CALL-SCORE-01", db) == max(0, 100 - 30 * expected_deductions)


# Missing / malformed data must never cause unsafe merging


def _seg(seq=1, speaker="agent", start=0.0, end=2.0, speaker_confidence=0.95):
    return SimpleNamespace(seq=seq, speaker=speaker, start_time=start, end_time=end, speaker_confidence=speaker_confidence)


def _viol(vid, segment_id, segment, rule="R3", decision="pending", needs=False):
    return SimpleNamespace(
        id=vid,
        call_id="C",
        rule_code=rule,
        segment_id=segment_id,
        segment=segment,
        severity="critical",
        needs_speaker_review=needs,
        review=SimpleNamespace(decision=decision),
    )


@pytest.mark.parametrize(
    "bad_segment",
    [
        _seg(speaker=None),
        _seg(speaker=""),
        _seg(start=None),
        _seg(end=None),
        _seg(start=5.0, end=1.0),  # ends before it starts
        _seg(start="abc"),
        _seg(seq=None),
        _seg(seq="2"),
        None,  # segment id set but the segment row is missing
    ],
)
def test_adjacent_rows_are_not_merged_when_segment_data_is_unreliable(bad_segment):
    good = _viol("v-a", "s1", _seg(seq=1, start=0.0, end=2.0))
    bad = _viol("v-b", "s2", bad_segment)
    s = evaluate_violations([good, bad])
    assert s["v-a"].counts_toward_score and s["v-b"].counts_toward_score
    assert s["v-a"].incident_group_id is None and s["v-b"].incident_group_id is None


def test_unreliable_segment_data_still_allows_exact_duplicate_merge():
    # Same segment id needs no timestamps to know it is the same moment.
    a = _viol("v-a", "s1", None)
    b = _viol("v-b", "s1", None)
    s = evaluate_violations([a, b])
    assert [x.counts_toward_score for x in (s["v-a"], s["v-b"])].count(True) == 1
    assert s["v-a"].incident_group_id == s["v-b"].incident_group_id is not None


# Review interactions


def test_one_false_alarm_fragment_and_one_eligible_fragment_deduct_once(db):
    add_finding(db, 1, "R3", 12, decision="false_alarm")
    add_finding(db, 2, "R3", 13)
    assert compute_score("CALL-SCORE-01", db) == 70
    assert counted_ids(db) == {"v-2"}  # the deduction moves to the remaining row


def test_group_identity_does_not_change_when_a_row_is_reviewed(db):
    add_finding(db, 1, "R3", 12)
    add_finding(db, 2, "R3", 13)
    before = statuses(db)["v-1"].incident_group_id
    db.query(Review).filter(Review.violation_id == "v-1").update({"decision": "false_alarm"})
    db.commit()
    assert statuses(db)["v-1"].incident_group_id == before is not None


def test_all_fragments_false_alarm_means_no_deduction(db):
    add_finding(db, 1, "R3", 12, decision="false_alarm")
    add_finding(db, 2, "R3", 13, decision="false_alarm")
    assert compute_score("CALL-SCORE-01", db) == 100
    assert counted_ids(db) == set()


def test_dismissed_row_between_two_flagged_rows_does_not_split_the_incident(db):
    add_finding(db, 1, "R3", 12)
    add_finding(db, 2, "R3", 13, decision="false_alarm")
    add_finding(db, 3, "R3", 14)
    assert compute_score("CALL-SCORE-01", db) == 70
    s = statuses(db)
    assert s["v-1"].incident_group_id == s["v-3"].incident_group_id is not None


def test_uncertain_only_group_deducts_nothing_while_pending(db):
    add_finding(db, 1, "R3", 12, needs_speaker_review=True)
    add_finding(db, 2, "R3", 13, needs_speaker_review=True)
    assert compute_score("CALL-SCORE-01", db) == 100
    assert counted_ids(db) == set()


def test_confirmed_uncertain_row_follows_existing_confirmed_scoring(db):
    add_finding(db, 1, "R3", 12, needs_speaker_review=True, decision="confirmed")
    assert compute_score("CALL-SCORE-01", db) == 70


def test_confirming_one_uncertain_fragment_makes_the_group_count_once(db):
    add_finding(db, 1, "R3", 12, needs_speaker_review=True)
    add_finding(db, 2, "R3", 13, needs_speaker_review=True, decision="confirmed")
    assert compute_score("CALL-SCORE-01", db) == 70
    assert counted_ids(db) == {"v-2"}


def test_certain_fragment_carries_the_deduction_for_a_group_with_an_uncertain_one(db):
    add_finding(db, 1, "R3", 12, needs_speaker_review=True)
    add_finding(db, 2, "R3", 13)
    assert compute_score("CALL-SCORE-01", db) == 70
    assert counted_ids(db) == {"v-2"}


def test_r1_and_rows_without_a_segment_stay_standalone(db):
    add_violation(db, 1, "R1", "high")
    add_violation(db, 2, "R3", "critical")
    add_violation(db, 3, "R3", "critical")  # same rule, no segment: still independent
    assert compute_score("CALL-SCORE-01", db) == 100 - 15 - 30 - 30
    assert all(s.incident_group_id is None for s in statuses(db).values())
    assert counted_ids(db) == {"v-1", "v-2", "v-3"}


def test_score_is_still_clamped_at_zero_and_deterministic(db):
    for i in range(5):
        add_finding(db, i + 1, "R3", 100 + i * 10)  # five separate incidents
    assert compute_score("CALL-SCORE-01", db) == 0
    assert compute_score("CALL-SCORE-01", db) == 0


# Historical reference structures.
#
# These reproduce the *structure* logged from live runs (rule codes, cited
# segment numbers, severities, speakers where logged, and the score each run
# produced). Segment timestamps were NOT logged, so they are synthetic
# (contiguous, 2s each). They show how the policy treats those violation
# patterns; they are not replays of the original audio timing.

CALL2_SEGMENT_SPEAKERS = {11: "agent", 12: "agent", 13: "agent", 14: "borrower",
                          15: "borrower", 16: "agent", 17: "borrower", 18: "agent"}


@pytest.mark.parametrize(
    "label, flagged, logged_score, policy_score",
    [
        ("call_02 run 1", [("R3", 12), ("R3", 13), ("R3", 16), ("R3", 18)], 0, 10),
        ("call_02 run 2", [("R3", 12), ("R3", 13), ("R3", 18)], 10, 40),
        ("call_02 run 3", [("R3", 12), ("R3", 13), ("R3", 16), ("R3", 18)], 0, 10),
    ],
)
def test_call_02_logged_violation_structure(db, label, flagged, logged_score, policy_score):
    for seq, speaker in CALL2_SEGMENT_SPEAKERS.items():
        add_segment(db, seq, speaker=speaker)
    for idx, (rule, seq) in enumerate(flagged, start=1):
        add_violation(db, idx, rule, SEVERITY[rule], segment_id=f"seg-{seq}")

    row_sum = max(0, 100 - sum(30 for _ in flagged))
    assert row_sum == logged_score  # what the pre-change formula produced
    assert compute_score("CALL-SCORE-01", db) == policy_score


@pytest.mark.parametrize(
    "label, flagged, logged_score, policy_score",
    [
        ("call_07 run 1", [("R6", 0), ("R5", 5)], 78, 78),
        ("call_07 run 2", [("R6", 0), ("R5", 5), ("R5", 6)], 63, 78),
        ("call_07 run 3", [("R6", 0), ("R5", 5), ("R5", 6), ("R2", 14)], 33, 48),
    ],
)
def test_call_07_logged_violation_structure(db, label, flagged, logged_score, policy_score):
    for seq in (0, 5, 6, 14):
        add_segment(db, seq, speaker="agent")
    weights = {"R6": 7, "R5": 15, "R2": 30}
    for idx, (rule, seq) in enumerate(flagged, start=1):
        add_violation(db, idx, rule, SEVERITY[rule], segment_id=f"seg-{seq}")

    assert 100 - sum(weights[r] for r, _ in flagged) == logged_score
    assert compute_score("CALL-SCORE-01", db) == policy_score


@pytest.mark.parametrize(
    "label, flagged, logged_score, policy_score",
    [
        # Segments 4 and 6 are separated by segment 5, so they are two
        # separate incidents and the policy must not change the score.
        ("call_04 run A", [("R6", 0), ("R4", 4), ("R4", 6)], 33, 33),
        ("call_04 run B", [("R4", 6)], 70, 70),
    ],
)
def test_call_04_logged_violation_structure(db, label, flagged, logged_score, policy_score):
    for seq in (0, 4, 5, 6):
        add_segment(db, seq, speaker="borrower" if seq == 5 else "agent")
    weights = {"R6": 7, "R4": 30}
    for idx, (rule, seq) in enumerate(flagged, start=1):
        add_violation(db, idx, rule, SEVERITY[rule], segment_id=f"seg-{seq}")

    assert 100 - sum(weights[r] for r, _ in flagged) == logged_score
    assert compute_score("CALL-SCORE-01", db) == policy_score
