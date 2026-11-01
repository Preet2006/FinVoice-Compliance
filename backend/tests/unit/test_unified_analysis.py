import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.config import settings
from app.services import llm_service, unified_analysis_service as ua
from app.services.transcription_service import RawSegment


def no_sleep(_):
    pass


class FakeCompletions:
    def __init__(self, contents):
        self.contents = list(contents)

    def create(self, **kwargs):
        content = self.contents.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class FakeClient:
    def __init__(self, contents):
        self.chat = SimpleNamespace(completions=FakeCompletions(contents))


def segs(*texts, step=3.0):
    return [RawSegment(seq=i, start=i * step, end=i * step + step, text=t) for i, t in enumerate(texts)]


def seg_entry(index, speaker, confidence=0.95, uncertain=False, mixed=False):
    return {"index": index, "speaker": speaker, "confidence": confidence, "uncertain": uncertain, "mixed_speakers": mixed}


def comp(stated=False, index=None, quote=None):
    return {"stated": stated, "segment_index": index, "evidence_quote": quote}


def opening_of(turns, name=None, company=None, purpose=None, uncertain=False):
    return {"agent_turns_considered": turns, "name": name or comp(), "company": company or comp(),
            "purpose": purpose or comp(), "uncertain": uncertain}


def response(speakers, violations=None, opening=None, outcomes=None, sentiment=None):
    """A complete, valid model response; tests override the parts they care about."""
    full_outcomes = {c: {"violated": False, "uncertain": False, "note": ""} for c in ("R2", "R3", "R4", "R5", "R6")}
    full_outcomes.update(outcomes or {})
    return {
        "segments": [seg_entry(i, s) if isinstance(s, str) else s for i, s in enumerate(speakers)],
        "violations": violations or [],
        "rule_outcomes": full_outcomes,
        "opening_check": opening or opening_of([[0]], comp(True, 0, "x"), comp(True, 0, "x"), comp(True, 0, "x")),
        "sentiment": sentiment or {"borrower_overall": "neutral", "agent_stayed_calm": True},
        "review_recommended": False,
        "review_reasons": [],
    }


def finding(rule, index, quote, speaker="agent", uncertain=False, confidence=0.9):
    return {"rule_code": rule, "segment_index": index, "quote": quote, "speaker": speaker,
            "explanation": "x", "confidence": confidence, "uncertain": uncertain}


def validate(data, *texts):
    return ua.parse_and_validate(data, segs(*texts))


def r6_of(result):
    found = [v for v in result.violations if v.rule_code == "R6"]
    return found[0] if found else None


# --- schema validation ------------------------------------------------------


def test_missing_segment_index_raises_after_one_corrective_retry(monkeypatch):
    bad = json.dumps(response(["agent"]))  # only segment 0 labelled, but two exist
    monkeypatch.setattr(llm_service, "_get_client", lambda: FakeClient([bad, bad]))
    with pytest.raises(llm_service.LLMResponseValidationError, match="missing a valid speaker"):
        ua.analyse_call(segs("hello", "hi"), SimpleNamespace(start_time=None), sleep_fn=no_sleep)


def test_corrective_retry_succeeds_when_second_response_is_valid(monkeypatch):
    opening = opening_of([[0]], comp(True, 0, "this is Priya"), comp(True, 0, "Shaha Finlease"), comp(True, 0, "your loan"))
    good = json.dumps(response(["agent"], opening=opening))
    monkeypatch.setattr(llm_service, "_get_client", lambda: FakeClient([json.dumps({"segments": "nope"}), good]))
    labelled, result = ua.analyse_call(
        segs("this is Priya from Shaha Finlease about your loan"), SimpleNamespace(start_time=None), sleep_fn=no_sleep
    )
    assert labelled[0].speaker == "agent" and result.violations == []


@pytest.mark.parametrize(
    "bad",
    [None, {}, {"borrower_overall": "ok", "agent_stayed_calm": True}, {"borrower_overall": "neutral", "agent_stayed_calm": "yes"}],
)
def test_invalid_sentiment_is_rejected(bad):
    data = response(["agent"])
    data["sentiment"] = bad
    with pytest.raises(llm_service.LLMResponseValidationError):
        validate(data, "hello")


def test_missing_opening_check_is_a_structural_error():
    data = response(["agent"])
    del data["opening_check"]
    with pytest.raises(llm_service.LLMResponseValidationError, match="opening_check"):
        validate(data, "hello")


def test_all_six_rules_are_handled_and_r1_is_never_accepted_from_the_model():
    data = response(
        ["agent", "borrower"],
        violations=[finding(r, 0, "hello") for r in ("R1", "R6", "R2", "R3", "R4", "R5")],
        opening=opening_of([[0]]),  # nothing stated -> R6 raised by the backend
    )
    _, result = validate(data, "hello", "yes")
    assert sorted(v.rule_code for v in result.violations) == ["R2", "R3", "R4", "R5", "R6"]
    reasons = {r["entry"]["rule_code"]: r["reason"] for r in result.rejected_findings}
    assert "backend" in reasons["R1"] and "opening_check" in reasons["R6"]
    assert r6_of(result).explanation.startswith("Mandatory opening missing")


def test_severity_is_not_a_model_output():
    data = response(["agent"], violations=[{**finding("R3", 0, "police"), "severity": "low"}])
    _, result = validate(data, "I will send police")
    assert not hasattr(result.violations[0], "severity")


def test_rule_outcome_inconsistencies_are_reported_not_hidden():
    data = response(["agent"], violations=[finding("R3", 0, "police")], outcomes={"R3": {"violated": False, "uncertain": False, "note": ""}})
    _, result = validate(data, "I will send police")
    assert "R3" in result.diagnostics["rule_outcome_inconsistencies"]


# --- evidence validation ------------------------------------------------------


def test_invented_quote_is_rejected_and_recorded():
    data = response(["agent"], violations=[finding("R3", 0, "we will burn your house")])
    _, result = validate(data, "please pay tomorrow")
    assert not any(v.rule_code == "R3" for v in result.violations)
    assert result.rejected_findings[0]["reason"].startswith("quote is not verbatim")


def test_nonexistent_segment_is_rejected():
    _, result = validate(response(["agent"], violations=[finding("R3", 7, "police")]), "police")
    assert result.rejected_findings[0]["reason"] == "segment_index does not exist"


def test_timestamps_come_from_the_transcript_not_the_model():
    data = response(["agent", "agent"], violations=[{**finding("R3", 1, "police"), "start_time": 999, "timestamp": "9:99"}])
    labelled, result = validate(data, "ok", "I will send police")
    v = next(x for x in result.violations if x.rule_code == "R3")
    assert v.segment_seq == 1 and (labelled[1].start, labelled[1].end) == (3.0, 6.0)


# --- garbled speech / R2 ------------------------------------------------------


def test_uncertain_abuse_finding_is_held_for_review_not_auto_accepted():
    data = response(["agent"], violations=[finding("R2", 0, "गुद्बाइ", uncertain=True)])
    _, result = validate(data, "ठीक है, गुद्बाइ.")
    assert next(v for v in result.violations if v.rule_code == "R2").needs_review is True


def test_model_can_decline_to_flag_a_garbled_closing():
    _, result = validate(response(["borrower"]), "ठीक है, गुद्बाइ.")
    assert not any(v.rule_code == "R2" for v in result.violations)


# --- speaker attribution / OTP -----------------------------------------------

OTP = ["आपके phone पर एक OTP आएगा", "Please OTP मुझे बता दीजे", "OTP? क्या मुझे आपको OTP बताना जरूरी है?", "नहीं, मैं OTP share नहीं करूँगा"]
ANY_OPENING = opening_of([[0, 1]], comp(True, 0, "आपके"), comp(True, 0, "आपके"), comp(True, 0, "आपके"))


def test_agent_otp_request_is_r5_and_borrower_refusal_is_not():
    data = response(["agent", "agent", "borrower", "borrower"], violations=[finding("R5", 1, "Please OTP मुझे बता दीजे")], opening=ANY_OPENING)
    labelled, result = validate(data, *OTP)
    r5 = [v for v in result.violations if v.rule_code == "R5"]
    assert len(r5) == 1 and r5[0].segment_seq == 1 and r5[0].needs_review is False
    assert [s.speaker for s in labelled] == ["agent", "agent", "borrower", "borrower"]


def test_r5_on_a_borrower_refusal_claimed_as_agent_is_flagged_for_review():
    data = response(["agent", "agent", "borrower", "borrower"], violations=[finding("R5", 3, "नहीं, मैं OTP share नहीं करूँगा", speaker="agent")], opening=ANY_OPENING)
    _, result = validate(data, *OTP)
    assert next(v for v in result.violations if v.rule_code == "R5").needs_review is True


def test_uncertain_and_mixed_segments_are_capped_at_low_confidence():
    data = response([seg_entry(0, "agent", 0.99, uncertain=True), seg_entry(1, "borrower", 0.97, mixed=True), seg_entry(2, "agent", 0.95)])
    labelled, _ = validate(data, "a", "b", "c")
    assert labelled[0].confidence <= 0.5 and labelled[1].confidence <= 0.5 and labelled[2].confidence == 0.95
    assert llm_service.is_uncertain_confidence(labelled[0].confidence) and llm_service.is_uncertain_confidence(labelled[1].confidence)


def test_missing_or_malformed_confidence_is_never_high():
    data = response([{"index": 0, "speaker": "agent", "confidence": "high"}, {"index": 1, "speaker": "borrower"}])
    labelled, _ = validate(data, "a", "b")
    assert [s.confidence for s in labelled] == [None, None]
    assert all(llm_service.is_uncertain_confidence(s.confidence) for s in labelled)


def test_third_party_is_preserved_as_third_party():
    labelled, _ = validate(response(["agent", "third_party"]), "I am calling about Karan", "I am his colleague")
    assert [s.speaker for s in labelled] == ["agent", "third_party"]


def test_unknown_and_mixed_states_are_preserved_and_uncertain():
    data = response([
        seg_entry(0, "unknown", 0.9),
        seg_entry(1, "agent", 0.9, mixed=True),
    ])
    labelled, _ = validate(data, "unclear fragment", "hello yes no")
    assert [s.speaker for s in labelled] == ["unknown", "mixed"]
    assert all(s.speaker_uncertain for s in labelled)


# --- R6 across segments and conversational turns -----------------------------

OPENING = [
    "Good afternoon. This is Prit Jhavikar calling from Shahah, Finley's regarding Mr. Karan Malhotra.",
    "Am I speaking with someone from his office?",
    "Yes, I am his colleague. May I know why are you calling?",
    "I am calling because Karan has an outstanding loan of 55,000 rupees to the company.",
]
OPENING_SPEAKERS = ["agent", "agent", "third_party", "agent"]


def test_turns_group_consecutive_segments_of_one_speaker():
    labelled, _ = validate(response(OPENING_SPEAKERS), *OPENING)
    assert [(sp, [s.seq for s in ss]) for sp, ss in ua.conversational_turns(labelled)] == [
        ("agent", [0, 1]), ("third_party", [2]), ("agent", [3])]
    assert [[s.seq for s in t] for t in ua.first_agent_turns(labelled)] == [[0, 1], [3]]


def test_purpose_in_second_agent_turn_passes_even_as_the_third_agent_segment():
    opening = opening_of([[0, 1], [3]], comp(True, 0, "This is Prit Jhavikar"), comp(True, 0, "Shahah, Finley's"),
                         comp(True, 3, "calling because Karan has an outstanding loan"))
    _, result = validate(response(OPENING_SPEAKERS, opening=opening), *OPENING)
    assert r6_of(result) is None and "turn_definition_mismatch" not in result.diagnostics


def test_hindi_purpose_can_span_adjacent_agent_segments():
    texts = [
        "Namaste main Pooja bol rahi hoon Shaha Finlease se",
        "Rakesh ji ke personal loan ke baare mein baat karni thi",
        "Haan boliye",
    ]
    opening = opening_of([[0, 1]], comp(True, 0, "main Pooja bol rahi hoon"), comp(True, 0, "Shaha Finlease"), comp(False))
    _, result = validate(response(["agent", "agent", "borrower"], opening=opening), *texts)
    assert r6_of(result) is None


def test_generic_opening_without_debt_purpose_is_still_a_violation():
    text = "Namaste main Pooja bol rahi hoon Shaha Finlease se ek zaroori baat ke liye"
    opening = opening_of([[0]], comp(True, 0, "main Pooja bol rahi hoon"), comp(True, 0, "Shaha Finlease"), comp(False))
    _, result = validate(response(["agent"], opening=opening), text)
    assert r6_of(result) is not None and "call purpose" in r6_of(result).explanation


def test_hindi_asr_self_introduction_variant_is_recognised_as_a_cue():
    from app.services.opening_service import has_self_introduction_cue

    assert has_self_introduction_cue("मैं नवीन गुप्ता डोल्रहाँ शाहा फिनलीस से") is True


def test_purpose_evidence_from_a_third_agent_turn_is_not_accepted():
    texts = [OPENING[0], OPENING[1], OPENING[2], "Please ask him to call.", "ok", "He owes money for the loan."]
    speakers = ["agent", "agent", "third_party", "agent", "third_party", "agent"]
    opening = opening_of([[0, 1], [3]], comp(True, 0, "This is Prit Jhavikar"), comp(True, 0, "Shahah, Finley's"),
                         comp(True, 5, "He owes money for the loan"))
    _, result = validate(response(speakers, opening=opening), *texts)
    r6 = r6_of(result)
    assert "call purpose" in r6.explanation and r6.needs_review is True


def test_turn_definition_mismatch_is_reported():
    opening = opening_of([[0], [1]], comp(True, 0, "This is Prit"), comp(True, 0, "Shahah"), comp(True, 3, "outstanding loan"))
    _, result = validate(response(OPENING_SPEAKERS, opening=opening), *OPENING)
    assert "turn_definition_mismatch" in result.diagnostics


def test_genuinely_missing_opening_flags_all_three_components():
    _, result = validate(response(["agent", "borrower", "agent"], opening=opening_of([[0], [2]])),
                         "Hello, am I speaking to Prakash?", "Yes who is this", "Your loan is overdue")
    r6 = r6_of(result)
    assert "agent name, company name, call purpose" in r6.explanation and r6.segment_seq == 0


def test_recognised_company_does_not_hide_a_missing_name_or_purpose():
    opening = opening_of([[0]], company=comp(True, 0, "Shaha Finlease"))
    _, result = validate(response(["agent"], opening=opening), "Calling from Shaha Finlease")
    assert r6_of(result).explanation == "Mandatory opening missing required element(s): agent name, call purpose."


# --- company name variants -----------------------------------------------------


@pytest.mark.parametrize(
    "text, phrase",
    [
        ("Good afternoon, this is Preet Jagekar calling from Shahah, Finley's regarding your personal loan.", "Shahah, Finley's"),
        ("this is Preet calling from Shaha Finley's about your account and loan", "Shaha Finley's"),
        ("नमस्ते मैं प्रीज वेकर बोल रहा हूँ शहा पिनली से मैं आपके पर्सनल लोन के पेंडिंग पेमेंट के बारे में बात कर रहा हूँ", "शहा पिनली"),
        ("मैं प्रीच रिकर बोल रहा हूं शहा पिनलीस से मैं आपके पर्सनल लोन के बारे में बात कर रहा हूं", "शहा पिनलीस"),
        ("main Preet bol raha hoon, Shahafinlees se, aapke loan ke baare mein", "Shahafinlees"),
    ],
)
def test_model_recognised_company_variants_pass_including_devanagari(text, phrase):
    opening = opening_of([[0]], comp(True, 0, text[:12]), comp(True, 0, phrase), comp(True, 0, text[-12:]))
    _, result = validate(response(["agent"], opening=opening), text)
    assert r6_of(result) is None


def test_original_transcript_text_is_never_rewritten():
    text = "नमस्ते मैं प्रीज बोल रहा हूँ शहा पिनली से लोन के बारे में"
    opening = opening_of([[0]], comp(True, 0, "मैं प्रीज बोल रहा हूँ"), comp(True, 0, "शहा पिनली"), comp(True, 0, "लोन के बारे में"))
    labelled, _ = validate(response(["agent"], opening=opening), text)
    assert labelled[0].text == text and "Finlease" not in labelled[0].text


def test_deterministic_matcher_still_backs_up_a_model_that_misses_the_company():
    text = "this is Preet calling from Shahafinlees about your credit card account"
    opening = opening_of([[0]], comp(True, 0, "this is Preet"), comp(False), comp(True, 0, "credit card account"))
    _, result = validate(response(["agent"], opening=opening), text)
    assert r6_of(result) is None and result.diagnostics["company_recognised_by"] == "matcher"


def test_unrelated_company_is_not_accepted():
    text = "this is Preet from Mahindra Finance about your personal loan"
    opening = opening_of([[0]], comp(True, 0, "this is Preet"), comp(False), comp(True, 0, "personal loan"))
    _, result = validate(response(["agent"], opening=opening), text)
    assert "company name" in r6_of(result).explanation


def test_model_claim_with_evidence_absent_from_the_transcript_is_not_trusted():
    opening = opening_of([[0]], comp(True, 0, "this is Preet"), comp(True, 0, "Shaha Finlease"), comp(True, 0, "your loan"))
    _, result = validate(response(["agent"], opening=opening), "this is Preet, about your loan")
    r6 = r6_of(result)
    assert "company name" in r6.explanation and r6.needs_review is True


# --- prompt contract -------------------------------------------------------------


def _prompt(call=None):
    call = call or SimpleNamespace(start_time=datetime(2026, 10, 4, 7, 30), account_type="credit_card", language="en")
    system, user = (m["content"] for m in ua.build_messages(segs("hello"), call))
    return system, user


def test_prompt_contains_rules_company_turn_definition_and_metadata():
    system, user = _prompt()
    for code in ("R1", "R2", "R3", "R4", "R5", "R6"):
        assert f"{code} [" in system
    assert "Shaha Finlease" in system
    assert "consecutive segments by the same speaker are ONE turn" in system
    assert "Never report R1" in system
    assert "start_time=2026-10-04T07:30:00" in user and "account_type=credit_card" in user


# Phrases that appeared in the evaluation recordings or in failure reports. A
# generalized prompt must not contain any of them.
SCENARIO_SPECIFIC = [
    "CALL-", "call_0", "Call 2", "Call 7", "Call 8", "Call 9", "Karan", "Malhotra", "Preet", "Jhagekar", "Jhavikar",
    "Rahul", "Rohit", "Amit", "Neeraj", "Prakash", "Sandeep", "Imran", "Finley", "Shahah", "Shahafinlees", "Shaf Inlees",
    "शहा पिनली", "शाफ इनली", "शहाफ", "गुद्ब", "बेवकूफ", "पुलिस भेज", "55,000", "22,000", "आपके phone पर",
    "Please OTP", "goodbye", "colleague", "WORKED EXAMPLES",
]


def test_prompt_has_no_call_ids_utterances_or_worked_examples():
    system, user = _prompt()
    for banned in SCENARIO_SPECIFIC:
        assert banned not in system, f"scenario-specific text in the prompt: {banned!r}"
    assert "e.g." not in system.split("OUTPUT:")[0].replace("e.g. an outstanding", "")  # no ad-hoc examples before the schema


def test_service_source_has_no_scenario_specific_literals():
    import inspect

    source = inspect.getsource(ua)
    for banned in SCENARIO_SPECIFIC:
        if banned in ("goodbye", "colleague", "Call 2", "Call 7", "Call 8", "Call 9", "e.g."):
            continue  # ordinary words that may legitimately appear in comments or the generic schema
        assert banned not in source, f"scenario-specific literal in the service: {banned!r}"


def test_prompt_has_no_examples_switch():
    import inspect

    assert "include_examples" not in inspect.signature(ua.build_messages).parameters
    assert "include_examples" not in inspect.signature(ua.analyse_call).parameters


def test_prompt_requires_explicit_uncertainty_instead_of_forced_labels():
    system, _ = _prompt()
    assert "set `uncertain` or `mixed_speakers`" in system
    assert "low confidence instead of a confident guess" in system
    assert "self-estimate, not proof" in system
    assert "Do not decide from a segment's wording or grammatical form alone" in system


def test_prompt_states_rule_boundaries_without_naming_scenarios():
    system, _ = _prompt()
    assert "R3 versus R4" in system and "intimidation alone does not establish R4" in system
    assert "Disclosure-only coercion" in system and "not automatically R3" in system
    assert "genuinely ambiguous" in system and "set `uncertain` true" in system
    assert "do not merge separate incidents" in system
    assert "Only the AGENT can breach R2-R6" in system
    assert "never put R6 in `violations`" in system


def test_prompt_and_backend_use_the_same_turn_definition():
    system, _ = _prompt()
    assert "consecutive segments by the same speaker are ONE turn" in system
    labelled, _ = validate(response(["agent", "agent", "borrower", "agent"]), "a", "b", "c", "d")
    assert [[s.seq for s in t] for t in ua.first_agent_turns(labelled)] == [[0, 1], [3]]


# --- R3 / R4, duplicates, distinct incidents ------------------------------------


def test_r3_and_r4_on_one_segment_with_different_evidence_are_both_kept():
    text = "tumhare bhai ne loan nahi bhara, office mein sabko bata dunga warna ghar aa jaunga"
    data = response(["agent"], violations=[finding("R4", 0, "office mein sabko bata dunga"), finding("R3", 0, "ghar aa jaunga")])
    _, result = validate(data, text)
    assert sorted(v.rule_code for v in result.violations if v.rule_code != "R6") == ["R3", "R4"]
    assert result.rejected_findings == []


def test_third_party_debt_disclosure_is_added_as_held_r4_support():
    texts = [
        "Hello this is Priya from Shaha Finlease",
        "He is not home, who is calling?",
        "His personal loan payment is pending for two months",
    ]
    labelled, result = validate(response(["agent", "third_party", "agent"]), *texts)
    [v] = [v for v in result.violations if v.rule_code == "R4"]
    assert (v.segment_seq, v.quote, v.needs_review) == (2, texts[2], True)
    assert (labelled[2].start, labelled[2].end) == (6.0, 9.0)
    assert result.diagnostics["third_party_disclosure"] == {"candidates": 1, "added_held": 1}


def test_ordinary_borrower_debt_conversation_does_not_create_r4_support():
    texts = ["Hello this is Priya from Shaha Finlease", "Yes, I am the borrower", "Your payment is pending"]
    _, result = validate(response(["agent", "borrower", "agent"]), *texts)
    assert not any(v.rule_code == "R4" for v in result.violations)


def test_uncertain_or_mixed_recipient_does_not_create_r4_support():
    for recipient in ("unknown", "mixed"):
        texts = ["Hello this is Priya from Shaha Finlease", "I am not sure who this is", "Your loan is pending"]
        _, result = validate(response(["agent", recipient, "agent"]), *texts)
        assert not any(v.rule_code == "R4" for v in result.violations)


def test_ambiguous_classification_is_kept_but_held_for_review():
    data = response(["agent"], violations=[finding("R3", 0, "sabko bata dunga", uncertain=True)])
    _, result = validate(data, "pay today or sabko bata dunga that you owe money")
    assert next(v for v in result.violations if v.rule_code == "R3").needs_review is True


def test_identical_evidence_reported_twice_is_kept_once_and_the_duplicate_is_recorded():
    data = response(["agent"], violations=[finding("R3", 0, "send police"), finding("R3", 0, "  Send   POLICE ")])
    _, result = validate(data, "I will send police")
    assert [v.rule_code for v in result.violations if v.rule_code == "R3"] == ["R3"]
    assert result.rejected_findings[0]["reason"].startswith("duplicate")


def test_distinct_occurrences_of_one_rule_are_not_merged():
    data = response(["agent", "borrower", "agent"],
                    violations=[finding("R3", 0, "send police"), finding("R3", 2, "come to your house"), finding("R3", 2, "arrest you")])
    _, result = validate(data, "I will send police", "please stop", "we will come to your house and arrest you")
    assert len([v for v in result.violations if v.rule_code == "R3"]) == 3
    assert result.rejected_findings == []


def test_there_is_no_analysis_mode_setting():
    assert not hasattr(settings, "analysis_mode")
