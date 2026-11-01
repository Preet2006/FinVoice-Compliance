"""
Dashboard: role-scoped aggregates computed from stored rows only.

Fixture: CALL-C3-01 (caller3, office Lower Parel, manager mgr2) is the only analysed
call, with one pending R3 finding (v-1). CALL-C1-01 and CALL-C2-01 (office Jogeshwari,
manager mgr1) have metadata only.
"""

from datetime import datetime

import pytest

from app.models import Call, Review, TranscriptSegment, Violation
from tests.conftest import CALLER1_HEADERS, CALLER3_HEADERS, HEAD_HEADERS, MGR1_HEADERS, MGR2_HEADERS


def dash(client, headers):
    r = client.get("/api/dashboard", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def add_finding(db, vid, call_id, rule, seg, severity="critical", decision="pending", status="verified"):
    db.add(Violation(id=vid, call_id=call_id, rule_code=rule, segment_id=seg, severity=severity, start_time_seconds=1,
                     quote="q", explanation="e", evidence_status=status))
    db.add(Review(id=f"r-{vid}", violation_id=vid, decision=decision))
    db.commit()


def make_ready(db, call_id, score, seg_id):
    c = db.get(Call, call_id)
    c.status, c.has_audio, c.score = "ready", True, score
    db.add(TranscriptSegment(id=seg_id, call_id=call_id, seq=1, speaker="agent", speaker_confidence=0.95,
                             start_time=0, end_time=3, text="agent words here"))
    db.commit()


def test_empty_scope_shows_zeros_and_no_invented_numbers(client):
    d = dash(client, MGR1_HEADERS)  # mgr1's team has two calls and no analysis
    assert (d["total_calls"], d["calls_analyzed"], d["calls_awaiting_review"]) == (2, 0, 0)
    assert (d["verified_violations"], d["counted_violations"], d["unverified_findings"]) == (0, 0, 0)
    assert d["average_score"] is None and d["top_risky_callers"] == [] and d["breaches_by_caller"] == [] and d["breaches_by_office"] == []
    assert [r["rule_code"] for r in d["breaches_by_rule"]] == ["R1", "R2", "R3", "R4", "R5", "R6"]
    assert all(r["count"] == 0 for r in d["breaches_by_rule"])
    assert d["review_outcomes"] == {"confirmed": 0, "false_alarm": 0, "pending": 0}


def test_head_sees_the_whole_organisation(client):
    d = dash(client, HEAD_HEADERS)
    assert (d["total_calls"], d["calls_analyzed"], d["calls_awaiting_review"]) == (3, 1, 1)  # v-1 is pending
    assert (d["verified_violations"], d["counted_violations"]) == (1, 1)
    assert {r["rule_code"]: r["count"] for r in d["breaches_by_rule"]}["R3"] == 1
    assert d["average_score"] == 40  # the fixture call's stored score
    assert [b["name"] for b in d["breaches_by_caller"]] == ["Imran Khan"] and d["breaches_by_office"][0]["name"] == "Lower Parel"
    assert d["top_risky_callers"][0]["counted"] == 1
    assert d["review_outcomes"]["pending"] == 1


def test_roles_only_see_what_they_may_see(client):
    assert dash(client, MGR2_HEADERS)["verified_violations"] == 1  # their own team
    assert dash(client, MGR1_HEADERS)["verified_violations"] == 0  # another team's breach is not visible
    own = dash(client, CALLER3_HEADERS)
    assert own["total_calls"] == 1 and [b["name"] for b in own["breaches_by_caller"]] == ["Imran Khan"]
    other = dash(client, CALLER1_HEADERS)
    assert other["total_calls"] == 1 and other["verified_violations"] == 0 and other["breaches_by_caller"] == []


def test_unverified_findings_are_reported_separately_and_never_as_breaches(client, db_session):
    add_finding(db_session, "v-u", "CALL-C3-01", "R5", None, severity="high", status="unverified")
    d = dash(client, HEAD_HEADERS)
    assert d["unverified_findings"] == 1
    assert d["verified_violations"] == 1 and {r["rule_code"]: r["count"] for r in d["breaches_by_rule"]}["R5"] == 0
    assert d["counted_violations"] == 1  # an unverified finding never counts


def test_dismissed_findings_are_not_breaches_and_outcomes_are_tallied(client, db_session):
    add_finding(db_session, "v-fa", "CALL-C3-01", "R2", "seg-1", decision="false_alarm")
    add_finding(db_session, "v-ok", "CALL-C3-01", "R4", "seg-1", decision="confirmed")
    d = dash(client, HEAD_HEADERS)
    assert d["review_outcomes"] == {"confirmed": 1, "false_alarm": 1, "pending": 1}
    rules = {r["rule_code"]: r["count"] for r in d["breaches_by_rule"]}
    assert rules["R2"] == 0 and rules["R4"] == 1 and rules["R3"] == 1  # the false alarm is out, the confirmed one is in
    assert d["verified_violations"] == 2


def test_findings_on_calls_that_were_never_analysed_are_ignored(client, db_session):
    add_finding(db_session, "v-meta", "CALL-C1-01", "R1", None, severity="high")  # e.g. a seeded artefact on a metadata-only call
    d = dash(client, HEAD_HEADERS)
    assert d["verified_violations"] == 1 and {r["rule_code"]: r["count"] for r in d["breaches_by_rule"]}["R1"] == 0


def test_aggregation_and_top_risky_ranking_across_callers(client, db_session):
    make_ready(db_session, "CALL-C1-01", 70, "seg-a")
    make_ready(db_session, "CALL-C2-01", 100, "seg-b")
    add_finding(db_session, "v-a", "CALL-C1-01", "R2", "seg-a")
    add_finding(db_session, "v-b", "CALL-C1-01", "R5", "seg-a", severity="high")
    d = dash(client, HEAD_HEADERS)
    assert d["calls_analyzed"] == 3 and d["average_score"] == round((40 + 70 + 100) / 3, 1)
    by_caller = {b["name"]: b for b in d["breaches_by_caller"]}
    assert by_caller["Rahul Kumar"]["violations"] == 2 and by_caller["Pooja Singh"]["violations"] == 0 and by_caller["Imran Khan"]["violations"] == 1
    assert [b["name"] for b in d["top_risky_callers"]][0] == "Rahul Kumar"  # most counted breaches first
    assert "Pooja Singh" not in [b["name"] for b in d["top_risky_callers"]]  # no breaches, not "risky"
    assert sum(r["count"] for r in d["breaches_by_rule"]) == d["verified_violations"] == 3
    assert sum(b["violations"] for b in d["breaches_by_office"]) == 3
    mgr = dash(client, MGR1_HEADERS)  # only Jogeshwari
    assert mgr["verified_violations"] == 2 and [o["name"] for o in mgr["breaches_by_office"]] == ["Jogeshwari"]


def test_top_risky_callers_is_capped_at_five(client, db_session):
    from app.models import AppUser

    for i in range(7):
        db_session.add(AppUser(id=f"u-x{i}", name=f"Caller X{i}", role="caller", office_id="o-01", manager_id="u-mgr1"))
        db_session.add(Call(id=f"CALL-X{i}", caller_id=f"u-x{i}", office_id="o-01", account_type="personal_loan",
                            start_time=datetime(2026, 10, 3, 10), has_audio=True, status="ready", score=70))
        db_session.add(TranscriptSegment(id=f"seg-x{i}", call_id=f"CALL-X{i}", seq=1, speaker="agent",
                                         speaker_confidence=0.95, start_time=0, end_time=3, text="t"))
    db_session.commit()
    for i in range(7):
        add_finding(db_session, f"v-x{i}", f"CALL-X{i}", "R3", f"seg-x{i}")
    assert len(dash(client, HEAD_HEADERS)["top_risky_callers"]) == 5


def test_accuracy_is_reported_unavailable_with_a_reason(client):
    acc = dash(client, HEAD_HEADERS)["accuracy"]
    assert acc["available"] is False and "ground-truth" in acc["reason"]


def test_every_number_matches_the_database(client, db_session):
    d = dash(client, HEAD_HEADERS)
    assert d["total_calls"] == db_session.query(Call).count()
    assert d["calls_analyzed"] == db_session.query(Call).filter_by(status="ready").count()
    assert d["verified_violations"] == db_session.query(Violation).filter_by(evidence_status="verified").count()


@pytest.mark.parametrize("headers", [{}, {"X-Role": "head"}])
def test_requests_without_an_identity_are_rejected(client, headers):
        assert client.get("/api/dashboard", headers=headers).status_code == 401
