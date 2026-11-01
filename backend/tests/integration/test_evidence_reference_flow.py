"""
Evidence by reference, end to end in unified mode (offline).

The real `analyse_call` and `parse_and_validate` run; only the provider
transport (`_get_client`, `_chat_json`) is replaced by a canned model response.
"""

from app.config import settings
from app.models import Violation
from app.services import llm_service, pipeline, transcription_service
from tests.integration.test_pipeline import (  # noqa: F401  (scratch_audio is a fixture)
    make_queued_call,
    mock_transcription,
    scratch_audio,
    write_fake_audio,
)
from tests.unit.test_unified_analysis import response

TEXTS = ["agar paisa nahi aaya to police bhej dunga", "aur aapko jail jaana padega", "theek hai"]


def raw():
    return [transcription_service.RawSegment(seq=i, start=2.0 * i, end=2.0 * i + 2.0, text=t) for i, t in enumerate(TEXTS)]


def ref(idx, ws, we, quote=None):
    entry = {"rule_code": "R3", "segment_index": idx, "word_start": ws, "word_end": we, "speaker": "agent",
             "explanation": "threat", "confidence": 0.9, "uncertain": False}
    if quote is not None:
        entry["quote"] = quote
    return entry


def run(db_session, scratch_audio, monkeypatch, call_id, violations):
    monkeypatch.setattr(llm_service, "_get_client", lambda: object())
    payload = response(["agent", "agent", "borrower"], violations=violations)
    monkeypatch.setattr(llm_service, "_chat_json", lambda *a, **k: payload)
    call = make_queued_call(db_session, call_id=call_id)
    write_fake_audio(scratch_audio, "pipe.wav")
    mock_transcription(monkeypatch, raw())
    result = pipeline.process_call(call.id, db_session)
    db_session.expire_all()
    return result, db_session.query(Violation).filter_by(call_id=call_id).order_by(Violation.id).all()


def test_a_valid_reference_is_stored_verified_with_the_stored_text_as_the_quote(db_session, scratch_audio, monkeypatch):
    result, rows = run(db_session, scratch_audio, monkeypatch, "CALL-REF-01", [ref(0, 5, 7, quote="POLICE bhej dunga")])
    [v] = [r for r in rows if r.rule_code == "R3"]
    assert v.evidence_status == "verified" and v.quote == "police bhej dunga"
    assert v.segment_id == "s-CALL-REF-01-0000" and float(v.start_time_seconds) == 0.0
    assert result.score == 70  # a verified critical R3 deducts


def test_an_out_of_range_reference_is_stored_unverified_showing_the_stored_segment(db_session, scratch_audio, monkeypatch):
    result, rows = run(db_session, scratch_audio, monkeypatch, "CALL-REF-02", [ref(1, 2, 40)])
    [v] = [r for r in rows if r.rule_code == "R3"]
    assert v.evidence_status == "unverified" and v.segment_id == "s-CALL-REF-02-0001"
    assert v.quote == TEXTS[1]  # the stored text of the cited segment, never a model-written quote
    assert "outside segment 1" in v.evidence_reason
    assert result.score == 100  # unverified evidence never deducts


def test_an_echo_found_only_in_another_segment_is_stored_unverified(db_session, scratch_audio, monkeypatch):
    result, rows = run(db_session, scratch_audio, monkeypatch, "CALL-REF-03", [ref(0, 0, 1, quote="aapko jail jaana padega")])
    [v] = [r for r in rows if r.rule_code == "R3"]
    assert v.evidence_status == "unverified" and v.quote == TEXTS[0]
    assert "segment 1, not in the cited segment 0" in v.evidence_reason
    assert result.score == 100


def test_an_echo_that_places_the_words_elsewhere_is_held_not_deducted(db_session, scratch_audio, monkeypatch):
    result, rows = run(db_session, scratch_audio, monkeypatch, "CALL-REF-04", [ref(0, 5, 7, quote="paisa nahi aaya")])
    [v] = [r for r in rows if r.rule_code == "R3"]
    assert v.evidence_status == "verified" and v.quote == "police bhej dunga"
    assert v.needs_speaker_review is True or v.review.decision == "pending"
    assert result.score == 100  # held: not deducted until a manager confirms


def test_a_quote_only_response_that_is_not_verbatim_cannot_bypass_verification(db_session, scratch_audio, monkeypatch):
    entry = {"rule_code": "R3", "segment_index": 0, "quote": "we will burn your house", "speaker": "agent",
             "explanation": "threat", "confidence": 0.9, "uncertain": False}
    result, rows = run(db_session, scratch_audio, monkeypatch, "CALL-REF-05", [entry])
    [v] = [r for r in rows if r.rule_code == "R3"]
    assert v.evidence_status == "unverified" and v.quote == TEXTS[0] and result.score == 100


def test_reprocessing_the_same_reference_response_is_idempotent(db_session, scratch_audio, monkeypatch):
    first, rows = run(db_session, scratch_audio, monkeypatch, "CALL-REF-06", [ref(0, 5, 7)])
    again = pipeline.process_call("CALL-REF-06", db_session)
    db_session.expire_all()
    assert db_session.query(Violation).filter_by(call_id="CALL-REF-06", rule_code="R3").count() == 1
    assert again.score == first.score == 70
