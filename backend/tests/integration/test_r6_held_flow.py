"""
A garbled agent name is held, not charged: through the real pipeline (the real
analysis, with a faked provider answer), the stored finding is unverified and
non-deducting, the call outcome is 'incomplete', and a manager can resolve it
with the existing Batch 1 audio attestation. A genuinely missing opening still
deducts.
"""

import json
from types import SimpleNamespace

import pytest

from app.models import Violation
from app.services import llm_service, pipeline, transcription_service
from tests.conftest import MGR1_HEADERS
from tests.unit.test_unified_analysis import comp, opening_of, response
from tests.integration.test_pipeline import (  # noqa: F401  (scratch_audio is a fixture)
    make_queued_call,
    mock_transcription,
    scratch_audio,
    write_fake_audio,
)

GARBLED_TEXT = "एक प्रिच विकर बोल रहा हूं शहाफ इनली से मैं आपके पर्सनल लोन के बारे में कॉल कर रहा हूं"
MISSING_TEXT = "Hello, am I speaking to Prakash Nayak?"
# What the model reports for the opening: no usable name (it was garbled), company and purpose cited from the text.
GARBLED_OPENING = opening_of([[0]], comp(False), comp(True, 0, "शहाफ इनली से"), comp(True, 0, "पर्सनल लोन के बारे में"))
MISSING_OPENING = opening_of([[0]], comp(False), comp(False), comp(False))


def run(db_session, scratch_audio, monkeypatch, call_id, text, opening):
    make_queued_call(db_session, call_id=call_id)
    write_fake_audio(scratch_audio, "pipe.wav")
    mock_transcription(monkeypatch, [transcription_service.RawSegment(seq=0, start=0.0, end=3.0, text=text),
                                     transcription_service.RawSegment(seq=1, start=3.0, end=5.0, text="जी बोलिए")])
    payload = json.dumps(response(["agent", "borrower"], opening=opening))

    class Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=payload))])

    monkeypatch.setattr(llm_service, "_get_client", lambda: Client())
    return pipeline.process_call(call_id, db_session)


def test_garbled_name_is_stored_as_an_unverified_finding_and_does_not_deduct(db_session, scratch_audio, monkeypatch):
    call = run(db_session, scratch_audio, monkeypatch, "CALL-R6-HELD", GARBLED_TEXT,
               GARBLED_OPENING)

    [v] = db_session.query(Violation).filter_by(call_id="CALL-R6-HELD").all()
    assert v.rule_code == "R6" and v.evidence_status == "unverified" and v.review.decision == "pending"
    assert v.quote == GARBLED_TEXT  # stored transcript text, never an invented quote
    assert "could not be established" in v.evidence_reason and "agent" in v.explanation.lower()
    assert call.status == "ready" and call.score == 100  # nothing deducted


def test_the_held_finding_makes_the_outcome_incomplete_not_clean(client, db_session, scratch_audio, monkeypatch):
    run(db_session, scratch_audio, monkeypatch, "CALL-R6-HELD2", GARBLED_TEXT,
        GARBLED_OPENING)
    detail = client.get("/api/calls/CALL-R6-HELD2", headers=MGR1_HEADERS).json()
    assert detail["analysis_outcome"] == "incomplete" and detail["provisional_score"] is True
    assert detail["findings_unverified"] == 1 and detail["counted_violations"] == 0
    [flag] = client.get("/api/calls/CALL-R6-HELD2/violations", headers=MGR1_HEADERS).json()
    assert flag["evidence_status"] == "unverified" and flag["counts_toward_score"] is False


def test_a_manager_can_confirm_it_only_with_the_audio_attestation(client, db_session, scratch_audio, monkeypatch):
    run(db_session, scratch_audio, monkeypatch, "CALL-R6-HELD3", GARBLED_TEXT,
        GARBLED_OPENING)
    vid = db_session.query(Violation).filter_by(call_id="CALL-R6-HELD3").one().id

    plain = client.post(f"/api/violations/{vid}/review", json={"decision": "confirmed"}, headers=MGR1_HEADERS)
    assert plain.status_code == 422 and plain.json()["error"]["code"] == "EVIDENCE_VERIFICATION_REQUIRED"

    dismissed = client.post(f"/api/violations/{vid}/review", json={"decision": "false_alarm", "comment": "He says his name clearly."}, headers=MGR1_HEADERS)
    assert dismissed.status_code == 200 and dismissed.json()["call"]["score"] == 100


def test_a_genuinely_missing_opening_is_still_a_counted_violation(client, db_session, scratch_audio, monkeypatch):
    call = run(db_session, scratch_audio, monkeypatch, "CALL-R6-MISSING", MISSING_TEXT,
               MISSING_OPENING)
    [v] = db_session.query(Violation).filter_by(call_id="CALL-R6-MISSING").all()
    assert v.evidence_status == "verified" and v.severity == "medium"
    assert v.explanation == "Mandatory opening missing required element(s): agent name, company name, call purpose."
    assert call.score == 93  # existing policy: one medium violation = 7 points
    detail = client.get("/api/calls/CALL-R6-MISSING", headers=MGR1_HEADERS).json()
    assert detail["analysis_outcome"] == "violations_found"


def test_reprocessing_does_not_duplicate_the_held_finding(db_session, scratch_audio, monkeypatch):
    run(db_session, scratch_audio, monkeypatch, "CALL-R6-HELD4", GARBLED_TEXT,
        GARBLED_OPENING)
    pipeline.process_call("CALL-R6-HELD4", db_session)
    assert db_session.query(Violation).filter_by(call_id="CALL-R6-HELD4").count() == 1
