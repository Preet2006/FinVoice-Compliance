"""
R1 as a metadata result on the call detail (computed on read), and detection of
stored R1 evidence that no longer matches a corrected start time. Reading never
creates, changes or hides a violation row, a review decision or a score.
"""

from datetime import datetime
from types import SimpleNamespace

import pytest

from app.models import Call, Review, Violation
from app.services import llm_service, pipeline, r1_check_service as r1
from tests.conftest import CALLER1_HEADERS, CALLER3_HEADERS, MGR1_HEADERS
from tests.integration.test_pipeline import (  # noqa: F401  (scratch_audio is a fixture)
    labelled_segments,
    make_queued_call,
    mock_compliance,
    mock_speaker_labelling,
    mock_transcription,
    raw_segments,
    scratch_audio,
    write_fake_audio,
)

CLEAN = llm_service.ComplianceResult(violations=[], borrower_sentiment="neutral", agent_stayed_calm=True)


def detail(client, call_id, headers=MGR1_HEADERS):
    return client.get(f"/api/calls/{call_id}", headers=headers).json()


def analyse(db_session, scratch_audio, monkeypatch, call_id, hour):
    make_queued_call(db_session, call_id=call_id, hour=hour)
    write_fake_audio(scratch_audio, "pipe.wav")
    mock_transcription(monkeypatch, raw_segments())
    mock_speaker_labelling(monkeypatch, labelled_segments())
    mock_compliance(monkeypatch, CLEAN)
    pipeline.process_call(call_id, db_session)


def audio_less_call(db_session, call_id, start):
    db_session.add(Call(id=call_id, caller_id="u-caller1", office_id="o-01", account_type="personal_loan", start_time=start,
                        has_audio=False, status="metadata_only", is_ground_truth=False))
    db_session.commit()


# --- a call with no recording still gets an R1 result ------------------------------------------------


@pytest.mark.parametrize("hour, minute, expected", [(7, 30, "violation"), (8, 0, "ok"), (18, 59, "ok"), (19, 0, "violation"), (20, 45, "violation")])
def test_an_audio_less_call_gets_an_r1_result_from_its_start_time(client, db_session, hour, minute, expected):
    audio_less_call(db_session, "CALL-NOAUDIO", datetime(2026, 10, 4, hour, minute))
    body = detail(client, "CALL-NOAUDIO")
    assert body["has_audio"] is False and body["r1_check"]["status"] == expected
    assert body["r1_check"]["stored_finding"] == "not_applicable" and body["r1_check"]["needs_refresh"] is False
    assert body["r1_check"]["evaluated_start_time"] == f"2026-10-04T{hour:02d}:{minute:02d}:00"


def test_the_r1_result_is_not_a_violation_a_score_or_an_outcome(client, db_session):
    audio_less_call(db_session, "CALL-NOAUDIO", datetime(2026, 10, 4, 7, 30))
    body = detail(client, "CALL-NOAUDIO")
    assert body["r1_check"]["status"] == "violation"
    assert body["score"] is None and body["analysis_outcome"] == "not_analyzed" and body["counted_violations"] == 0
    assert client.get("/api/calls/CALL-NOAUDIO/violations", headers=MGR1_HEADERS).json() == []  # no made-up row
    assert db_session.query(Violation).filter_by(call_id="CALL-NOAUDIO").count() == 0


def test_reading_the_detail_repeatedly_creates_nothing(client, db_session):
    audio_less_call(db_session, "CALL-NOAUDIO", datetime(2026, 10, 4, 7, 30))
    violations_before, reviews_before = db_session.query(Violation).count(), db_session.query(Review).count()
    for _ in range(3):
        detail(client, "CALL-NOAUDIO")
        client.get("/api/calls/CALL-NOAUDIO/violations", headers=MGR1_HEADERS)
    assert (db_session.query(Violation).count(), db_session.query(Review).count()) == (violations_before, reviews_before)


def test_role_scoping_is_unchanged_for_the_r1_result(client, db_session):
    audio_less_call(db_session, "CALL-NOAUDIO", datetime(2026, 10, 4, 7, 30))
    assert client.get("/api/calls/CALL-NOAUDIO", headers=CALLER3_HEADERS).status_code == 404  # another caller's call
    assert detail(client, "CALL-NOAUDIO", CALLER1_HEADERS)["r1_check"]["status"] == "violation"


# --- an analysed call: the stored row and the metadata agree --------------------------------------------


def test_a_stored_r1_row_that_matches_the_start_time_is_current(client, db_session, scratch_audio, monkeypatch):
    analyse(db_session, scratch_audio, monkeypatch, "CALL-A", hour=21)
    body = detail(client, "CALL-A")
    assert body["r1_check"] == {**body["r1_check"], "status": "violation", "stored_finding": "present", "stored_evidence_current": True, "needs_refresh": False}


def test_an_in_hours_analysed_call_with_no_r1_row_is_current(client, db_session, scratch_audio, monkeypatch):
    analyse(db_session, scratch_audio, monkeypatch, "CALL-B", hour=11)
    body = detail(client, "CALL-B")
    assert (body["r1_check"]["status"], body["r1_check"]["stored_finding"], body["r1_check"]["needs_refresh"]) == ("ok", "none", False)


# --- stale evidence after a start-time correction ---------------------------------------------------------


def stored_r1(db_session, call_id):
    return db_session.query(Violation).filter_by(call_id=call_id, rule_code="R1").one()


def set_start(db_session, call_id, when):
    db_session.query(Call).filter_by(id=call_id).update({"start_time": when})
    db_session.commit()


def test_a_corrected_time_that_still_violates_leaves_stored_evidence_quoting_the_old_time(client, db_session, scratch_audio, monkeypatch):
    analyse(db_session, scratch_audio, monkeypatch, "CALL-12", hour=21)  # stored: "Call started at 21:00:00 IST"
    set_start(db_session, "CALL-12", datetime(2026, 10, 6, 19, 15))  # corrected: still out of hours, different time
    body = detail(client, "CALL-12")
    assert body["r1_check"]["status"] == "violation" and body["r1_check"]["stored_evidence_current"] is False
    assert body["r1_check"]["needs_refresh"] is True
    assert "21:00:00" in stored_r1(db_session, "CALL-12").quote  # the displayed evidence is stale


def test_a_corrected_time_back_inside_hours_flags_the_outdated_violation(client, db_session, scratch_audio, monkeypatch):
    analyse(db_session, scratch_audio, monkeypatch, "CALL-20", hour=21)
    set_start(db_session, "CALL-20", datetime(2026, 10, 10, 16, 50))
    body = detail(client, "CALL-20")
    assert (body["r1_check"]["status"], body["r1_check"]["stored_finding"], body["r1_check"]["needs_refresh"]) == ("ok", "present", True)


def test_a_corrected_time_that_newly_violates_flags_the_missing_row(client, db_session, scratch_audio, monkeypatch):
    analyse(db_session, scratch_audio, monkeypatch, "CALL-8", hour=11)
    set_start(db_session, "CALL-8", datetime(2026, 10, 4, 7, 30))
    body = detail(client, "CALL-8")
    assert (body["r1_check"]["status"], body["r1_check"]["stored_finding"], body["r1_check"]["needs_refresh"]) == ("violation", "none", True)


def test_detecting_stale_evidence_never_touches_a_managers_decision_or_the_score(client, db_session, scratch_audio, monkeypatch):
    analyse(db_session, scratch_audio, monkeypatch, "CALL-12", hour=21)
    row = stored_r1(db_session, "CALL-12")
    row.review.decision = "false_alarm"
    row.review.comment = "Manager checked the call log"
    row.review.reviewer_id = "u-mgr1"
    db_session.commit()
    score_before = db_session.get(Call, "CALL-12").score
    set_start(db_session, "CALL-12", datetime(2026, 10, 6, 19, 15))

    for _ in range(3):
        assert detail(client, "CALL-12")["r1_check"]["needs_refresh"] is True
    client.get("/api/calls/CALL-12/violations", headers=MGR1_HEADERS)

    db_session.expire_all()
    row = stored_r1(db_session, "CALL-12")
    assert (row.review.decision, row.review.comment, row.review.reviewer_id) == ("false_alarm", "Manager checked the call log", "u-mgr1")
    assert db_session.get(Call, "CALL-12").score == score_before
    assert db_session.query(Violation).filter_by(call_id="CALL-12", rule_code="R1").count() == 1  # no duplicate


# --- the service on its own -------------------------------------------------------------------------------------


def fake_call(start, status="ready"):
    return SimpleNamespace(start_time=start, status=status)


def fake_r1(quote):
    return SimpleNamespace(rule_code="R1", quote=quote)


def test_a_missing_start_time_is_unresolved_never_a_pass_or_a_violation():
    result = r1.r1_check(fake_call(None, "metadata_only"), [])
    assert result.status == "unresolved" and result.evaluated_start_time is None and result.needs_refresh is False
    analysed = r1.r1_check(fake_call(None, "ready"), [fake_r1("whatever")])
    assert analysed.status == "unresolved" and analysed.stored_evidence_current is None and analysed.needs_refresh is False


def test_only_r1_rows_are_considered():
    result = r1.r1_check(fake_call(datetime(2026, 10, 4, 11, 0)), [SimpleNamespace(rule_code="R3", quote="x")])
    assert result.stored_finding == "none" and result.needs_refresh is False


def test_an_exact_quote_match_is_what_makes_stored_evidence_current():
    exact = "Call started at 07:30:00 IST (outside permitted hours 08:00-19:00)"
    assert r1.r1_check(fake_call(datetime(2026, 10, 4, 7, 30)), [fake_r1(exact)]).stored_evidence_current is True
    assert r1.r1_check(fake_call(datetime(2026, 10, 4, 7, 30)), [fake_r1(exact.replace("07:30:00", "07:45:00"))]).stored_evidence_current is False
