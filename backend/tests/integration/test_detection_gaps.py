"""
Two detection gaps seen in the Batch 3 live validation, pinned offline.

  Call 2: the expected R2 (the agent's insult) was not reported.
  Call 7: the second OTP request was not reported as its own incident.

Root cause in both (see the fixture note): the model labelled the agent's lines
as `borrower`, at 0.90-0.99 confidence, so it saw no agent breach there. Nothing
was dropped by validation, de-duplication or grouping.

LIMITATION: the expected speakers come from call_scripts.md, aligned by
inspection. They are script-derived, not audio-verified, and Call 7 is due to be
re-recorded. Tests marked "captured" describe what the model returned; tests
marked "counterfactual" describe what the pipeline does GIVEN a different model
answer. Neither claims what the recordings contain, and neither is a finding
about a real call.
"""

import copy
import json
from pathlib import Path

import pytest

from app.config import settings
from app.models import Violation
from app.services import llm_service, pipeline, scoring_service, transcription_service
from app.services import unified_analysis_service as ua
from tests.integration.test_pipeline import make_queued_call, mock_transcription, scratch_audio, write_fake_audio  # noqa: F401

FIXTURE = json.loads((Path(__file__).parent.parent / "fixtures" / "detection_gaps.json").read_text(encoding="utf-8"))
SEVERITY_WEIGHT = {"critical": 30, "high": 15, "medium": 7, "low": 3}


def raw(case):
    return [transcription_service.RawSegment(seq=s["seq"], start=s["start"], end=s["end"], text=s["text"]) for s in case["transcript"]]


def validated(case, response):
    return ua.parse_and_validate(response, raw(case))[1]


def with_speakers(response, speaker, indexes):
    out = copy.deepcopy(response)
    for entry in out["segments"]:
        if entry["index"] in indexes:
            entry["speaker"] = speaker
    return out


def word_range(case, seq, phrase):
    """Word positions of `phrase` inside segment `seq`, using the shared tokenizer."""
    words = next(s["text"] for s in case["transcript"] if s["seq"] == seq).split()
    target = phrase.split()
    for i in range(len(words) - len(target) + 1):
        if words[i:i + len(target)] == target:
            return i, i + len(target) - 1
    raise AssertionError(f"{phrase!r} not in segment {seq}")


def finding(rule, seq, span, speaker="agent"):
    return {"rule_code": rule, "segment_index": seq, "word_start": span[0], "word_end": span[1], "speaker": speaker,
            "explanation": "counterfactual", "confidence": 0.9, "uncertain": False}


def run_pipeline(db_session, scratch_audio, monkeypatch, case, response, call_id):
    monkeypatch.setattr(llm_service, "_get_client", lambda: object())
    monkeypatch.setattr(llm_service, "_chat_json", lambda *a, **k: response)
    call = make_queued_call(db_session, call_id=call_id)
    write_fake_audio(scratch_audio, "pipe.wav")
    mock_transcription(monkeypatch, raw(case))
    result = pipeline.process_call(call.id, db_session)
    db_session.expire_all()
    rows = db_session.query(Violation).filter_by(call_id=call_id).order_by(Violation.id).all()
    return result, rows, scoring_service.evaluate_violations(rows)


C2, C7 = FIXTURE["call2"], FIXTURE["call7"]


# --- what the model returned (captured) -----------------------------------------------------------------------------------


@pytest.mark.parametrize("case", [C2, C7], ids=["call2", "call7"])
def test_captured_validation_dropped_nothing(case):
    """The omission is upstream of validation: no finding was rejected, merged or de-duplicated."""
    assert case["captured_rejected_findings"] == []
    result = validated(case, case["captured_response"])
    # R6 may be held (an unclear agent name); that is the opening check, not a dropped model finding.
    assert [r for r in result.rejected_findings if r["entry"].get("rule_code") != "R6"] == []
    model_side = [v for v in result.violations if v.rule_code != "R6" and not v.explanation.startswith("Possible abusive language")]
    assert {v.rule_code for v in model_side} == {v.get("rule_code") for v in case["captured_response"]["violations"]}


@pytest.mark.parametrize("case", [C2, C7], ids=["call2", "call7"])
def test_captured_model_labelled_the_script_agent_lines_as_borrower_with_high_confidence(case):
    """Root cause. 'Script-expected' is script-derived and not audio-verified."""
    by_index = {e["index"]: e for e in case["captured_response"]["segments"]}
    for seq in case["script_expected_agent_segments"]:
        assert by_index[seq]["speaker"] == "borrower"
        assert by_index[seq]["confidence"] >= 0.9 and not by_index[seq]["uncertain"] and not by_index[seq]["mixed_speakers"]


def test_call2_captured_model_reported_no_r2_and_said_so():
    outcomes = C2["captured_response"]["rule_outcomes"]
    assert outcomes["R2"]["violated"] is False
    assert not any(v["rule_code"] == "R2" for v in C2["captured_response"]["violations"])


def test_call7_captured_model_reported_one_r5_and_the_other_request_sits_in_a_borrower_labelled_segment():
    r5 = [v["segment_index"] for v in C7["captured_response"]["violations"] if v["rule_code"] == "R5"]
    assert r5 == [6]
    assert "बता दीजे" in next(s["text"] for s in C7["transcript"] if s["seq"] == 9)  # an anaphoric request: it names no credential itself


# --- counterfactual: the pipeline handles the findings if the model had made them ----------------------------------------------


def test_counterfactual_call2_r2_on_a_correctly_labelled_agent_segment_is_verified_and_deducts(db_session, scratch_audio, monkeypatch):
    span = word_range(C2, 9, "بے وکوف")
    response = with_speakers(C2["captured_response"], "agent", {5, 6, 8, 9})
    response["violations"] = list(response["violations"]) + [finding("R2", 9, span)]
    before = run_pipeline(db_session, scratch_audio, monkeypatch, C2, C2["captured_response"], "CALL-GAP-2A")
    after = run_pipeline(db_session, scratch_audio, monkeypatch, C2, response, "CALL-GAP-2B")
    r2 = [v for v in after[1] if v.rule_code == "R2"]
    assert len(r2) == 1 and r2[0].evidence_status == "verified" and "بے وکوف" in r2[0].quote
    assert after[2][r2[0].id].counts_toward_score is True
    assert after[0].score < before[0].score


def test_counterfactual_call2_an_r2_finding_on_a_borrower_labelled_segment_is_held_not_lost_and_not_deducted(db_session, scratch_audio, monkeypatch):
    """If the model flags the words but its segment label still says borrower, the finding
    survives as held-for-review instead of vanishing, and it never deducts unreviewed."""
    span = word_range(C2, 9, "بے وکوف")
    response = copy.deepcopy(C2["captured_response"])
    response["violations"] = list(response["violations"]) + [finding("R2", 9, span, speaker="agent")]
    base = run_pipeline(db_session, scratch_audio, monkeypatch, C2, C2["captured_response"], "CALL-GAP-2C")
    result, rows, status = run_pipeline(db_session, scratch_audio, monkeypatch, C2, response, "CALL-GAP-2D")
    [r2] = [v for v in rows if v.rule_code == "R2"]
    assert r2.needs_speaker_review is True and status[r2.id].counts_toward_score is False
    assert result.score == base[0].score


def test_counterfactual_call7_two_otp_requests_are_two_incidents_not_merged_or_deduplicated(db_session, scratch_audio, monkeypatch):
    first = word_range(C7, 6, "OTP मुझे बता दीजे")
    second = word_range(C7, 9, "जल्दी process करने के लिए बता दीजे")
    response = with_speakers(C7["captured_response"], "agent", {9})
    response["violations"] = [finding("R5", 6, first), finding("R5", 9, second)]
    result, rows, status = run_pipeline(db_session, scratch_audio, monkeypatch, C7, response, "CALL-GAP-7A")
    r5 = [v for v in rows if v.rule_code == "R5"]
    assert len(r5) == 2 and all(v.evidence_status == "verified" for v in r5)  # the validator did not de-duplicate them
    assert all(status[v.id].incident_group_id is None for v in r5)  # a refusal sits between them: neither is grouped with the other
    assert all(status[v.id].counts_toward_score for v in r5)
    one_incident = run_pipeline(db_session, scratch_audio, monkeypatch, C7, {**response, "violations": [response["violations"][0]]}, "CALL-GAP-7B")
    assert one_incident[0].score - result.score == SEVERITY_WEIGHT[r5[0].severity]  # the second request costs exactly one more R5


def test_counterfactual_call7_an_r5_cited_on_a_borrower_labelled_segment_is_held(db_session, scratch_audio, monkeypatch):
    second = word_range(C7, 9, "जल्दी process करने के लिए बता दीजे")
    response = copy.deepcopy(C7["captured_response"])
    response["violations"] = list(response["violations"]) + [finding("R5", 9, second, speaker="agent")]
    base = run_pipeline(db_session, scratch_audio, monkeypatch, C7, C7["captured_response"], "CALL-GAP-7C")
    result, rows, status = run_pipeline(db_session, scratch_audio, monkeypatch, C7, response, "CALL-GAP-7D")
    held = [v for v in rows if v.rule_code == "R5" and v.segment_id and v.segment_id.endswith("0009")]
    assert len(held) == 1 and held[0].needs_speaker_review is True and status[held[0].id].counts_toward_score is False
    assert result.score == base[0].score


def test_a_credential_word_is_not_enough_to_find_the_second_request():
    """Why a keyword backstop would not have caught it: the borrower-labelled request names no credential."""
    text = next(s["text"] for s in C7["transcript"] if s["seq"] == 9)
    assert "OTP" not in text and "otp" not in text.lower()
