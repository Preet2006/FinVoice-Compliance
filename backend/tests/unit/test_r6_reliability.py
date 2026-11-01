"""
R6 (mandatory opening) reliability: a name that is garbled or not really the
agent's is not credited; an opening that may be hidden inside a merged or
mislabelled segment is held for a manager, never silently passed and never a
deduction. The scope (first two agent turns) and the late-introduction rule are
unchanged.
"""

import json
from pathlib import Path

import pytest

from app.services import opening_service, unified_analysis_service as ua
from app.services.transcription_service import RawSegment
from tests.unit.test_unified_analysis import comp, opening_of, r6_of, response, segs

FIXTURE = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "eval_openings.json").read_text(encoding="utf-8"))
OK_OPENING = "Hello this is Priya calling from Shaha Finlease about your pending loan payment"


def run(speakers, texts, opening, confidences=None):
    data = response(speakers, opening=opening)
    if confidences:
        for entry, c in zip(data["segments"], confidences):
            entry["confidence"] = c
    labelled, result = ua.parse_and_validate(data, segs(*texts))
    return result


def held(result):
    return [r for r in result.rejected_findings if r["entry"].get("rule_code") == "R6"]


def cited(text, phrase, idx=0):
    return comp(True, idx, phrase)


def complete(text):
    return opening_of([[0]], cited(text, "this is Priya"), cited(text, "Shaha Finlease"), cited(text, "pending loan payment"))


# --- the seven required scenarios -------------------------------------------------------------------
def test_complete_opening_passes():
    result = run(["agent", "borrower"], [OK_OPENING, "yes speaking"], complete(OK_OPENING))
    assert r6_of(result) is None and held(result) == []
    assert result.diagnostics["r6"]["components"] == {"name": "stated", "company": "stated", "purpose": "stated"}


def test_missing_agent_name_is_a_violation():
    text = "Hello calling from Shaha Finlease about your pending loan payment"
    opening = opening_of([[0]], None, cited(text, "Shaha Finlease"), cited(text, "pending loan payment"))
    result = run(["agent", "borrower"], [text, "yes"], opening)
    v = r6_of(result)
    assert v is not None and "agent name" in v.explanation and held(result) == []


def test_missing_company_name_is_a_violation():
    text = "Hello this is Priya about your pending loan payment"
    opening = opening_of([[0]], cited(text, "this is Priya"), None, cited(text, "pending loan payment"))
    v = r6_of(run(["agent", "borrower"], [text, "yes"], opening))
    assert v is not None and "company name" in v.explanation


def test_missing_purpose_is_a_violation():
    text = "Hello this is Priya from Shaha Finlease"
    opening = opening_of([[0]], cited(text, "this is Priya"), cited(text, "Shaha Finlease"), None)
    v = r6_of(run(["agent", "borrower"], [text, "yes"], opening))
    assert v is not None and "call purpose" in v.explanation


def test_garbled_name_is_held_not_credited_and_not_a_violation():
    text = "नमस्ते सि यो हूँ शाह फिनलीस से आपके लोन के पेमेंट के बारे में"
    opening = opening_of([[0]], cited(text, "सि यो"), cited(text, "शाह फिनलीस"), cited(text, "लोन के पेमेंट"))
    result = run(["agent", "borrower"], [text, "हाँ"], opening)
    assert r6_of(result) is None  # no deduction
    [h] = held(result)
    assert h["kind"] == "invalid_evidence" and result.diagnostics["r6"]["components"]["name"] == "unclear"
    assert "name_not_credited" in result.diagnostics["r6"]


def test_a_name_cited_far_from_any_introduction_is_not_credited():
    # the borrower's name, asked about by the agent, is not the agent's own introduction
    text = "नमस्ते क्या मैं मीना जी से बात कर रहा हूं शाह फिनलीस के लोन पेमेंट के बारे में"
    opening = opening_of([[0]], cited(text, "मीना"), cited(text, "शाह फिनलीस"), cited(text, "लोन पेमेंट"))
    result = run(["agent", "borrower"], [text, "हाँ"], opening)
    assert r6_of(result) is None and len(held(result)) == 1


def test_merged_speaker_turns_are_not_reliable_opening_evidence():
    # the first segment holds both speakers' first turns and is labelled borrower
    merged = "Hello is this Anil ji speaking yes who is this"
    texts = [merged, "I am Tarun from Shaha Finlease", "your personal loan payment is pending"]
    opening = opening_of([[1, 2]], cited(texts[1], "I am Tarun", 1), cited(texts[1], "Shaha Finlease", 1), cited(texts[2], "loan payment is pending", 2))
    result = run(["borrower", "agent", "agent"], texts, opening)
    assert r6_of(result) is None  # not a deduction ...
    [h] = held(result)  # ... and not a pass: held for a manager, anchored on the merged segment
    assert h["entry"]["segment_index"] == 0 and "first segment" in h["entry"]["explanation"]
    assert "unreliable_opening" in result.diagnostics["r6"]


def test_late_introduction_does_not_repair_an_incomplete_opening():
    texts = ["Hello is this Anil ji speaking", "yes", "your payment is pending", "who is this", "I am Tarun from Shaha Finlease about your loan"]
    # agent turn 1 = seg 0, turn 2 = seg 2, the introduction comes in agent turn 3 (seg 4)
    opening = opening_of([[0], [2]], None, None, cited(texts[2], "your payment is pending", 2))
    result = run(["agent", "borrower", "agent", "borrower", "agent"], texts, opening)
    v = r6_of(result)
    assert v is not None and "agent name" in v.explanation and "company name" in v.explanation


def test_uncertain_speaker_label_in_the_opening_holds_it():
    result = run(["agent", "borrower"], [OK_OPENING, "yes"], complete(OK_OPENING), confidences=[0.4, 0.95])
    assert r6_of(result) is None and len(held(result)) == 1
    assert "uncertain" in held(result)[0]["entry"]["explanation"]


# --- name credibility unit cases -----------------------------------------------------------------------------
@pytest.mark.parametrize("scope,quote,expected", [
    ("Hello this is Priya Sharma calling", "Priya Sharma", True),
    ("Hello my name is Rahul and I am calling", "Rahul", True),
    ("hello main Imran bol raha hoon shaha finlease se", "Imran", True),
    ("नमस्ते मैं हरीश पटेल बोल रहा हूँ शहाफ इनलीस से", "हरीश पटेल", True),
    ("ہیلو میں عمران بول رہا ہوں شاہ فنلیس سے", "میں عمران بول رہا ہوں", True),
    ("नमस्ते सि यो हूँ शहा फिंली का", "सि यो", False),  # only short fragments
    ("Hello is this Meena ji, I need to discuss your loan", "Meena", False),  # no introduction nearby
    ("hello main bol raha hoon but a very long sentence follows here with Rahul at the very end", "Rahul", False),
    ("anything", "", False),
    ("anything", "words not in scope", False),
])
def test_name_evidence_credibility(scope, quote, expected):
    assert opening_service.name_evidence_credible(scope, quote) is expected


# --- the captured evaluation recordings (script-derived roles by one speaker; not audio-verified) --------------------
def replay(call_id):
    c = FIXTURE[call_id]
    raw = [RawSegment(seq=s["seq"], start=s["start"], end=s["end"], text=s["text"]) for s in c["transcript"]]
    return ua.parse_and_validate(c["captured_response"], raw)[1]


def test_call_12_garbled_name_that_was_the_borrowers_name_is_now_held():
    result = replay("CALL-0012")
    assert r6_of(result) is None and len(held(result)) == 1  # was silently credited as the agent's name
    assert result.diagnostics["r6"]["components"]["name"] == "unclear"


def test_call_17_merged_first_segment_is_held_not_silently_passed():
    result = replay("CALL-0017")
    assert r6_of(result) is None
    [h] = held(result)
    assert h["entry"]["segment_index"] == 0 and "merge" in h["entry"]["explanation"]


def test_call_13_clear_opening_is_still_credited():
    result = replay("CALL-0013")
    assert r6_of(result) is None and held(result) == []
    assert result.diagnostics["r6"]["components"] == {"name": "stated", "company": "stated", "purpose": "stated"}
