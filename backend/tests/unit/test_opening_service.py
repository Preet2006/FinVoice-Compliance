"""
R6 (mandatory opening): the evaluator, its decision on the analysis's opening_check, and offline
regression on saved transcripts of calls 7-9.

The saved-transcript tests feed the model's RECORDED answers from earlier live
runs to the current backend code. They show how the backend now treats those
answers; they say nothing about how a live model would answer today.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import llm_service, opening_service as op, turns, unified_analysis_service as ua

FIXTURE = json.loads((Path(__file__).parent.parent / "fixtures" / "saved_openings.json").read_text(encoding="utf-8"))
CASES = {c["id"]: c for c in FIXTURE["cases"]}


def seg(seq, text, speaker="agent"):
    return SimpleNamespace(seq=seq, speaker=speaker, text=text, start=float(seq), end=seq + 1.0, confidence=0.95)


def evaluate(texts, **kw):
    kw.setdefault("name_stated", False)
    kw.setdefault("company_stated", False)
    kw.setdefault("purpose_stated", False)
    return op.evaluate_opening([seg(i, t) for i, t in enumerate(texts)], **kw)


# --- self-introduction cue ----------------------------------------------------

CUE_YES = [
    "Good afternoon, this is Preet Jagekar calling from Shahah, Finley's",
    "Hello, my name is Priya", "I am Priya from the collections team", "I'm Imran calling from the bank",
    "main Imran bol raha hoon, Shaha Finlease se", "Namaste, main Rekha bol rahi hoon", "mera naam Pooja hai",
    "एक प्रिच विकर बोल रहा हूं शहाफ इनली से", "खुअलो, मैं प्री च कर बोर रहा हूं शहाफ इनली से",
    "नमस्ते मैं प्रीज वेकर बोल रहा हूँ", "मेरा नाम पूजा है", "मैं रेखा बोल रही हूं",
]
CUE_NO = [
    "Hello, am I speaking to Prakash Nayak?", "Sir, your personal loan is overdue by 35,000 rupees.",
    "Is this a good time to talk?", "this is important, please listen", "I am calling about the outstanding payment",
    "I understand, sir.", "I am sorry sir", "Calling from Shaha Finlease about your loan", "Good morning", "",
    "क्या मेरी बात रहुल मेहता जी से हो रही है", "आप पेमेंट कब तक कर पाएंगे", "कॉल कर रहा हूं",
]


@pytest.mark.parametrize("text", CUE_YES)
def test_introduction_phrasing_is_detected(text):
    assert op.has_self_introduction_cue(text) is True


@pytest.mark.parametrize("text", CUE_NO)
def test_ordinary_sentences_are_not_mistaken_for_an_introduction(text):
    assert op.has_self_introduction_cue(text) is False


# --- component statuses ---------------------------------------------------------


def test_everything_stated_is_ok():
    out = evaluate(["this is Priya from Shaha Finlease about your loan"], name_stated=True, company_stated=True, purpose_stated=True)
    assert out.kind == op.KIND_OK and out.statuses == {"name": "stated", "company": "stated", "purpose": "stated"}


def test_genuinely_missing_opening_is_a_definite_violation_listing_each_component():
    out = evaluate(["Hello, am I speaking to Prakash Nayak?", "Your loan is overdue, you need to pay today."])
    assert out.kind == op.KIND_VIOLATION
    assert out.missing == ["agent name", "company name", "call purpose"]
    assert out.explanation == "Mandatory opening missing required element(s): agent name, company name, call purpose."
    assert out.anchor_seq == 0


def test_garbled_name_with_an_introduction_is_held_not_a_violation():
    out = evaluate(
        ["खुअलो, मैं प्री च कर बोर रहा हूं शहाफ इनली से मैं आपके पर्सनल लोन के बारे में कॉल कर रहा हूं"],
        company_stated=True, purpose_stated=True,
    )
    assert out.kind == op.KIND_HELD and out.statuses["name"] == op.UNCLEAR
    assert out.missing == [] and "could not be established" in out.explanation
    assert "check the audio" in out.explanation.lower()


def test_a_missing_company_is_not_excused_by_an_unclear_name():
    out = evaluate(["एक प्रिच विकर बोल रहा हूं मैं आपके लोन के बारे में"], purpose_stated=True)
    assert out.kind == op.KIND_VIOLATION and out.missing == ["company name"]
    assert out.unclear == ["agent name"] and "could not be established" in out.explanation


def test_a_missing_purpose_is_not_excused_by_a_recognised_company_or_name():
    out = evaluate(["this is Priya from Shaha Finlease"], name_stated=True, company_stated=True)
    assert out.kind == op.KIND_VIOLATION and out.missing == ["call purpose"]


def test_company_variant_is_recognised_by_the_matcher_even_if_the_model_said_no():
    out = evaluate(["नमस्ते मैं प्रीज वेकर बोल रहा हूँ शहा पिनली से"], name_stated=True, purpose_stated=True)
    assert out.kind == op.KIND_OK and out.diagnostics["company_recognised_by"] == "matcher"


def test_an_unrelated_company_is_not_accepted():
    out = evaluate(["this is Preet from Mahindra Finance about your loan"], name_stated=True, purpose_stated=True)
    assert out.kind == op.KIND_VIOLATION and out.missing == ["company name"]


def test_unsupported_model_claims_raise_the_violation_for_review():
    out = evaluate(["Hello?"], unsupported=("company",))
    assert out.kind == op.KIND_VIOLATION and out.needs_review is True and "evidence is not in" in out.explanation


def test_evidence_quote_must_come_from_the_scope_or_the_first_segment_is_used():
    out = evaluate(["first line", "second line"], evidence_quote="invented words")
    assert out.quote == "first line"
    out = evaluate(["first line", "second line"], evidence_quote="second  LINE")
    assert out.quote == "second  LINE"


# --- the analysis's opening_check, decided by the evaluator -------------------------------


def unified_decision(segments, name, company, purpose):
    labelled = [llm_service.LabelledSegment(seq=s["seq"], speaker=s["speaker"], start=float(s["seq"]), end=s["seq"] + 1.0, text=s["text"], confidence=0.95) for s in segments]
    turn_list = [[s.seq for s in t] for t in turns.first_agent_turns(labelled)]
    data = {"opening_check": {"agent_turns_considered": turn_list, "name": name, "company": company, "purpose": purpose, "uncertain": False}}
    diagnostics = {}
    violation, held = ua._evaluate_opening(data, labelled, diagnostics)
    return violation, held, diagnostics


def summarize(violations, rejected):
    r6 = [v for v in violations if v.rule_code == "R6"]
    held = [r for r in rejected if isinstance(r["entry"], dict) and r["entry"].get("rule_code") == "R6"]
    if r6:
        return ("violation", r6[0].explanation)
    if held:
        return ("held", held[0]["entry"]["explanation"])
    return ("ok", None)


GARBLED = [
    {"seq": 0, "speaker": "agent", "text": "एक प्रिच विकर बोल रहा हूं शहाफ इनली से मैं आपके पर्सनल लोन के बारे में कॉल कर रहा हूं"},
    {"seq": 1, "speaker": "borrower", "text": "आप बोलिए"},
]
MISSING = [
    {"seq": 0, "speaker": "agent", "text": "Hello, am I speaking to Prakash Nayak?"},
    {"seq": 1, "speaker": "borrower", "text": "Yes"},
    {"seq": 2, "speaker": "agent", "text": "Your loan is overdue."},
]
SPLIT = [
    {"seq": 0, "speaker": "agent", "text": "Good afternoon. This is Priya calling from Shaha Finlease."},
    {"seq": 1, "speaker": "agent", "text": "Am I speaking with someone from his office?"},
    {"seq": 2, "speaker": "borrower", "text": "Yes"},
    {"seq": 3, "speaker": "agent", "text": "I am calling because Karan has an outstanding loan."},
]


def comp(stated, idx=None, quote=None):
    return {"stated": stated, "segment_index": idx, "evidence_quote": quote}


def test_a_garbled_name_after_an_introduction_is_held_not_a_violation():
    violation, held, _ = unified_decision(GARBLED, comp(False), comp(True, 0, "शहाफ इनली से"), comp(True, 0, "पर्सनल लोन के बारे में"))
    assert violation is None and held is not None and held["entry"]["rule_code"] == "R6"
    assert held["kind"] == llm_service.KIND_INVALID_EVIDENCE
    assert "could not be established" in held["entry"]["explanation"]


def test_a_genuinely_missing_opening_is_a_violation_listing_each_component():
    violation, held, _ = unified_decision(MISSING, comp(False), comp(False), comp(False))
    assert held is None
    assert violation.explanation == "Mandatory opening missing required element(s): agent name, company name, call purpose."


def test_a_purpose_stated_in_the_second_agent_turn_passes():
    violation, held, _ = unified_decision(
        SPLIT, comp(True, 0, "This is Priya"), comp(True, 0, "Shaha Finlease"), comp(True, 3, "calling because Karan has an outstanding loan"),
    )
    assert violation is None and held is None


def test_the_scope_reaches_the_second_turn_past_a_split_first_turn():
    # A company named only in agent turn 2 is still inside the scope, and the matcher recognises it.
    segments = [
        {"seq": 0, "speaker": "agent", "text": "Good afternoon, this is Priya."},
        {"seq": 1, "speaker": "agent", "text": "Am I speaking with Mr Karan?"},
        {"seq": 2, "speaker": "borrower", "text": "Yes"},
        {"seq": 3, "speaker": "agent", "text": "I am calling from Shaha Finlease about your loan."},
    ]
    violation, held, diagnostics = unified_decision(segments, comp(True, 0, "this is Priya"), comp(False), comp(True, 3, "about your loan"))
    assert violation is None and held is None
    assert diagnostics["company_recognised_by"] == "matcher"


# --- R6 comes only from opening_check -----------------------------------------------


def test_the_models_own_r6_verdict_and_r6_findings_never_decide_the_result():
    data = {
        "opening_check": {"agent_turns_considered": [[0]], "name": comp(True, 0, "this is Priya"), "company": comp(True, 0, "Shaha Finlease"), "purpose": comp(True, 0, "your loan"), "uncertain": False},
        "rule_outcomes": {"R6": {"violated": True, "uncertain": False, "note": "claims a violation"}},
    }
    labelled = [llm_service.LabelledSegment(seq=0, speaker="agent", start=0.0, end=1.0, text="this is Priya from Shaha Finlease about your loan", confidence=0.9)]
    diagnostics = {}
    violation, held = ua._evaluate_opening(data, labelled, diagnostics)
    assert violation is None and held is None
    assert diagnostics["r6"]["model_outcome_overridden"] == {"model_said_violated": True, "backend_decided": "ok"}


def test_r6_is_not_reported_as_a_rule_outcome_inconsistency():
    problems = {}
    ua._consistency({"rule_outcomes": {c: {"violated": False} for c in ("R2", "R3", "R4", "R5", "R6")}}, [], problems)
    assert "R6" not in problems.get("rule_outcome_inconsistencies", {})


# --- saved transcripts of calls 7, 8 and 9 (offline; recorded model answers) ----------


def as_labelled(case):
    return [llm_service.LabelledSegment(seq=s["seq"], speaker=s["speaker"], start=float(s["seq"]), end=s["seq"] + 1.0, text=s["text"], confidence=0.9) for s in case["segments"]]


def test_saved_call_9_turns_are_grouped_correctly():
    labelled = as_labelled(CASES["CALL-0009-legacy-run1"])
    assert [[s.seq for s in t] for t in turns.first_agent_turns(labelled)] == [[0, 1], [3]]
    old = [s for s in labelled if s.speaker == "agent"][:2]
    assert [s.seq for s in old] == [0, 1]  # the old scope never reached the turn that states the purpose
    assert [s.seq for s in turns.first_agent_turn_segments(labelled)] == [0, 1, 3]


@pytest.mark.parametrize("case_id", [k for k, c in CASES.items() if c["pipeline"] == "unified"])
def test_saved_unified_answers(case_id):
    case = CASES[case_id]
    labelled = as_labelled(case)
    data = {"opening_check": case["recorded_model_opening"]}
    violation, held = ua._evaluate_opening(data, labelled, {})
    if case["call"] == "CALL-0007":
        assert violation is None and held is not None and held["entry"]["segment_index"] == 0
    elif case["call"] == "CALL-0008":
        assert violation is None and held is None
    else:
        # The model credited no purpose, but turn 2 says "I am calling because ... an outstanding loan ...":
        # the backend's purpose check recognises it, so the opening passes.
        assert violation is None and held is None
