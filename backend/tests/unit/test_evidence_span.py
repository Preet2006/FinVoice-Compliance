"""
Evidence by reference (Batch 3, offline).

Convention under test: a WORD is a maximal run of non-whitespace characters,
numbered from 0 per segment, `word_end` inclusive; the quote is the exact
substring of the stored text between those words. The prompt's numbered words
and the backend's resolver both come from `word_spans`.
"""

import json
import unicodedata
from pathlib import Path

import pytest

from app.services import evidence_span_service as ev
from app.services import llm_service, unified_analysis_service as ua
from app.services.transcription_service import RawSegment
from tests.unit.test_unified_analysis import comp, opening_of, response, seg_entry, segs, validate

FIXTURE = json.loads((Path(__file__).parent.parent / "fixtures" / "call2_urdu_evidence.json").read_text(encoding="utf-8"))

CORPUS = [
    "नमस्ते मैं प्रीज वेकर बोल रहा हूँ शहा पिनली से",  # Devanagari with vowel signs and a nasal sign
    "क्‍ष त्र क्‌ष ज्ञान",  # joiner / non-joiner inside a word
    "ہم آپ کے گھر بھی آئیں گے",  # Urdu, right-to-left
    "جی‌ ہاں، ٹھیک ہے۔ آپ کو‌ پتہ ہے",  # Urdu punctuation and a non-joiner
    "Please tell me the OTP you received",
    "आपके phone पर एक OTP आएगा",  # mixed script
    "pay now \U0001F600 or else \U0001F46E will come \U0001F4DE",  # characters outside the BMP
    "résumé now",  # decomposed accents
    "résumé now",  # precomposed accents
    "a\tb\nc  d e　f",  # tab, newline, double space, NBSP, ideographic space
    "ab​cd ef",  # zero-width space is NOT whitespace: it stays inside the word
    "  hello   world  ",  # leading, trailing and repeated spaces
    "22,000 rupees, 10:30 ok?",  # digits, commas, colons inside words
    "0:1 2:3",  # words that look like position markers
    "single",
    "ि",  # a lone combining vowel sign
]


def span_pairs(text):
    n = len(ev.word_spans(text))
    return [(i, j) for i in range(n) for j in range(i, n)]


# --- the convention -----------------------------------------------------------------------------


@pytest.mark.parametrize("text", CORPUS)
def test_every_word_range_round_trips_to_the_exact_stored_substring(text):
    spans = ev.word_spans(text)
    for i, j in span_pairs(text):
        s = ev.resolve_reference(3, text, i, j)
        assert s.char_start == spans[i][0] and s.char_end == spans[j][1]
        assert s.quote == text[s.char_start:s.char_end]  # an exact slice: nothing normalized, folded or re-scripted
        assert s.quote == s.quote.strip() and s.quote
        assert (s.segment_seq, s.word_start, s.word_end) == (3, i, j)


def test_whitespace_between_the_chosen_words_is_preserved_exactly():
    text = "a\tb\nc  d"
    assert ev.resolve_reference(0, text, 0, 3).quote == "a\tb\nc  d"
    assert ev.resolve_reference(0, text, 1, 2).quote == "b\nc"


def test_unicode_is_never_normalized_or_changed():
    nfd, nfc = "résumé now", "résumé now"
    assert ev.resolve_reference(0, nfd, 0, 0).quote == "résumé"  # still decomposed
    assert ev.resolve_reference(0, nfc, 0, 0).quote == "résumé"  # still precomposed
    assert unicodedata.normalize("NFC", nfd) != nfd  # so a normalizing resolver would have changed it


def test_offsets_are_code_points_not_utf16_units():
    text = "ok \U0001F600\U0001F600 done"
    s = ev.resolve_reference(0, text, 1, 1)
    assert s.quote == "\U0001F600\U0001F600" and (s.char_start, s.char_end) == (3, 5)  # two code points, four UTF-16 units


@pytest.mark.parametrize(
    "text, words",
    [
        ("a\tb\nc  d e　f", ["a", "b", "c", "d", "e", "f"]),
        ("ab​cd ef", ["ab​cd", "ef"]),
        ("  hello   world  ", ["hello", "world"]),
        ("", []),
        ("   \t\n", []),
        ("क्‍ष त्र", ["क्‍ष", "त्र"]),
    ],
)
def test_words_are_maximal_runs_of_non_whitespace(text, words):
    assert [text[a:b] for a, b in ev.word_spans(text)] == words


# --- the prompt and the resolver cannot disagree -------------------------------------------------------


@pytest.mark.parametrize("text", CORPUS)
def test_the_numbered_prompt_text_and_the_resolver_agree_on_every_word(text):
    rendered = ev.number_words(text)
    spans = ev.word_spans(text)
    tokens = rendered.split(" ") if rendered else []
    assert len(tokens) == len(spans)
    for index, token in enumerate(tokens):
        prefix = f"{index}:"
        assert token.startswith(prefix)
        word_shown_to_the_model = token[len(prefix):]
        assert word_shown_to_the_model == text[spans[index][0]:spans[index][1]]
        assert ev.resolve_reference(0, text, index, index).quote == word_shown_to_the_model  # "word 7" means the same on both sides


def test_the_user_message_uses_the_shared_numbering():
    texts = ["नमस्ते मैं बोल रहा हूँ", "ہم آپ کے گھر", "Please tell me the OTP"]
    raw = segs(*texts)
    user = ua.build_messages(raw, None)[1]["content"].split("TRANSCRIPT:\n")[1].splitlines()
    for line, s in zip(user, raw):
        assert line == f"[{s.seq}] ({s.start:.1f}-{s.end:.1f}s) {ev.number_words(s.text)}"


def test_validation_reads_the_same_text_the_prompt_showed():
    texts = ["ہم آپ کے گھر بھی آئیں گے", "ठीक है"]
    raw = segs(*texts)
    _, result = ua.parse_and_validate(response(["agent", "borrower"], violations=[ref("R3", 0, 3, 5)]), raw)
    [v] = non_r6(result)
    assert v.quote == "گھر بھی آئیں"  # words 3-5 as numbered in the prompt


# --- strict validation of every reference type -------------------------------------------------------------

TEXT = "agar paisa nahi aaya to main police bhej dunga"  # 9 words, 0-8


@pytest.mark.parametrize(
    "start, end, fragment",
    [
        (None, 3, "both"), (3, None, "both"), (None, None, "both"),
        (True, 3, "whole numbers"), (1, False, "whole numbers"), (True, True, "whole numbers"),
        (1.0, 3, "whole numbers"), (1, 3.0, "whole numbers"), (1.5, 2, "whole numbers"),
        (float("nan"), 2, "whole numbers"), (float("inf"), 2, "whole numbers"),
        ("1", 3, "whole numbers"), (1, "3", "whole numbers"), ([1], 3, "whole numbers"), ({"a": 1}, 3, "whole numbers"),
        (-1, 3, "negative"), (2, -1, "negative"), (-5, -2, "negative"),
        (4, 2, "after"),
        (0, 9, "outside"), (9, 9, "outside"), (50, 60, "outside"), (0, 10**30, "outside"),
    ],
)
def test_malformed_or_out_of_range_references_are_rejected_with_a_reason(start, end, fragment):
    with pytest.raises(ev.InvalidReference, match=fragment):
        ev.resolve_reference(5, TEXT, start, end)


@pytest.mark.parametrize("start, end, quote", [(0, 0, "agar"), (8, 8, "dunga"), (6, 8, "police bhej dunga"), (0, 8, TEXT)])
def test_boundary_references_are_accepted(start, end, quote):
    assert ev.resolve_reference(5, TEXT, start, end).quote == quote


@pytest.mark.parametrize("text", ["", "   ", "\t\n"])
def test_a_segment_with_no_words_cannot_be_cited(text):
    with pytest.raises(ev.InvalidReference, match="no words"):
        ev.resolve_reference(2, text, 0, 0)


def test_an_integer_subclass_is_not_accepted_as_a_position():
    class FancyInt(int):
        pass

    with pytest.raises(ev.InvalidReference, match="whole numbers"):
        ev.resolve_reference(0, TEXT, FancyInt(1), FancyInt(2))


def test_the_reference_is_checked_without_looking_at_what_it_points_to():
    # Well-formedness is one question; whether the words support the violation
    # is another, and the resolver has no opinion on it.
    for words in ("agar paisa", "dunga"):
        assert ev.resolve_reference(0, TEXT, 0, 1).quote == "agar paisa"
    assert ev.resolve_reference(0, "good morning sir", 0, 2).quote == "good morning sir"


# --- the echo is a consistency signal, kept apart from the reference -------------------------------------------


def span_of(text, i, j, seq=0):
    return ev.resolve_reference(seq, text, i, j)


def echo(value, text=TEXT, i=6, j=8, others=None):
    return ev.assess_echo(value, span_of(text, i, j), text, others or {})


@pytest.mark.parametrize("value", [None, "", "   ", "\n", 5, 3.5, ["x"], {"a": 1}, True])
def test_an_absent_or_non_text_echo_is_absent(value):
    assert echo(value).kind == ev.ECHO_ABSENT


@pytest.mark.parametrize("value", ["police bhej dunga", "POLICE  bhej   dunga", "bhej dunga", "police", "6:police 7:bhej 8:dunga", "  police bhej dunga  "])
def test_an_echo_inside_the_cited_words_matches(value):
    assert echo(value).kind == ev.ECHO_MATCHES_SPAN


def test_an_echo_elsewhere_in_the_same_segment_means_the_position_is_off():
    assert echo("paisa nahi aaya").kind == ev.ECHO_OUTSIDE_SPAN


def test_a_single_word_outside_the_span_proves_nothing():
    assert echo("paisa").kind == ev.ECHO_UNINFORMATIVE


def test_an_echo_in_another_script_is_not_found_and_not_a_conflict():
    # The Urdu-to-Devanagari case: the reference is still good.
    assert echo("पुलिस भेज दूंगा", others={1: "something else entirely"}).kind == ev.ECHO_NOT_FOUND


def test_an_echo_found_only_in_a_different_segment_is_a_conflict():
    result = echo("jail jaoge zaroor", others={1: "tum jail jaoge zaroor", 2: "nothing here"})
    assert result.kind == ev.ECHO_CONFLICT and result.other_segments == (1,)


def test_a_conflict_lists_every_other_segment_that_contains_it():
    assert echo("jail jaoge zaroor", others={4: "tum jail jaoge zaroor", 2: "wo jail jaoge zaroor"}).other_segments == (2, 4)


def test_found_in_the_cited_segment_wins_over_found_elsewhere():
    assert echo("paisa nahi aaya", others={1: "paisa nahi aaya bhi"}).kind == ev.ECHO_OUTSIDE_SPAN


def test_a_single_word_echo_in_another_segment_is_not_a_conflict():
    assert echo("jail", others={1: "jail jail jail"}).kind == ev.ECHO_UNINFORMATIVE


def test_assessing_an_echo_never_raises_and_never_changes_the_span():
    span = span_of(TEXT, 6, 8)
    for value in (None, "x", "police", "\x00", "🙂" * 50, "a b " * 1000):
        ev.assess_echo(value, span, TEXT, {1: "y"})
    assert span.quote == "police bhej dunga"


# --- finding validation: references ----------------------------------------------------------------------------


def ref(rule, idx, ws, we, quote=None, **extra):
    entry = {"rule_code": rule, "segment_index": idx, "word_start": ws, "word_end": we, "speaker": "agent",
             "explanation": "x", "confidence": 0.9, "uncertain": False}
    if quote is not None:
        entry["quote"] = quote
    entry.update(extra)
    return entry


def non_r6(result):
    # The backend's own abusive-language support (a held R2) is a separate mechanism, tested in test_abusive_language.py.
    return [v for v in result.violations if v.rule_code != "R6" and not v.explanation.startswith("Possible abusive language")]


def rejected_for(result, rule="R3"):
    return [r for r in result.rejected_findings if isinstance(r["entry"], dict) and r["entry"].get("rule_code") == rule]


def run(violations, *texts, speakers=None):
    return validate(response(speakers or ["agent"] * len(texts), violations=violations), *texts)[1]


def test_a_valid_reference_gives_a_verified_finding_whose_quote_is_the_stored_text():
    result = run([ref("R3", 0, 6, 8)], TEXT)
    [v] = non_r6(result)
    assert v.quote == "police bhej dunga" and v.segment_seq == 0 and v.needs_review is False
    assert result.diagnostics["evidence"] == {"reference_findings": 1, "echo_absent": 1}


def test_a_model_quote_that_differs_from_the_stored_words_never_becomes_the_evidence():
    result = run([ref("R3", 0, 6, 8, quote="POLICE bhej dunga!!")], TEXT)
    assert non_r6(result)[0].quote == "police bhej dunga"


@pytest.mark.parametrize(
    "entry_changes, fragment",
    [
        ({"word_end": 99}, "outside segment 0"),
        ({"word_start": -1}, "negative"),
        ({"word_start": 7, "word_end": 2}, "after"),
        ({"word_start": 1.0}, "whole numbers"),
        ({"word_start": "1"}, "whole numbers"),
        ({"word_end": None}, "both"),
        ({"word_start": True}, "whole numbers"),
    ],
)
def test_a_bad_reference_is_stored_for_review_not_accepted(entry_changes, fragment):
    result = run([ref("R3", 0, 6, 8, **entry_changes)], TEXT)
    assert non_r6(result) == []
    [r] = rejected_for(result)
    assert r["kind"] == llm_service.KIND_INVALID_EVIDENCE and fragment in r["reason"]
    assert result.diagnostics["evidence"]["reference_invalid"] == 1


def test_a_missing_second_field_is_a_bad_reference_even_if_the_quote_is_perfect():
    entry = ref("R3", 0, 6, 8, quote="police bhej dunga")
    del entry["word_end"]
    result = run([entry], TEXT)
    assert non_r6(result) == [] and "both" in rejected_for(result)[0]["reason"]


def test_a_bad_reference_is_not_rescued_by_a_verbatim_quote():
    result = run([ref("R3", 0, 40, 41, quote="police bhej dunga")], TEXT)  # the quote IS verbatim, the reference is not
    assert non_r6(result) == [] and len(rejected_for(result)) == 1


def test_a_reference_to_a_missing_segment_is_rejected():
    result = run([ref("R3", 9, 0, 1)], TEXT)
    assert non_r6(result) == [] and rejected_for(result)[0]["reason"] == "segment_index does not exist"


def test_a_segment_index_that_is_not_a_whole_number_is_rejected():
    for bad in (None, "0", 0.0, True, [0]):
        result = run([ref("R3", bad, 0, 1)], TEXT)
        assert non_r6(result) == [], bad


def test_a_reference_is_accepted_whatever_the_words_say_support_is_not_this_layers_question():
    result = run([ref("R5", 0, 0, 1, explanation="asks for an OTP")], "good morning sir how are you")
    [v] = non_r6(result)
    assert v.quote == "good morning" and v.explanation == "asks for an OTP"  # the explanation and the words go to the manager together


def test_the_same_words_cited_twice_are_one_finding():
    result = run([ref("R3", 0, 6, 8), ref("R3", 0, 6, 8, explanation="again")], TEXT)
    assert len(non_r6(result)) == 1
    assert [r["kind"] for r in rejected_for(result)] == [llm_service.KIND_DUPLICATE]


def test_different_or_overlapping_words_in_one_segment_are_separate_findings():
    result = run([ref("R3", 0, 0, 1), ref("R3", 0, 6, 8), ref("R3", 0, 7, 8)], TEXT)
    assert [v.quote for v in non_r6(result)] == ["agar paisa", "police bhej dunga", "bhej dunga"]


def test_the_same_words_under_two_rules_are_two_findings():
    result = run([ref("R3", 0, 6, 8), ref("R4", 0, 6, 8)], TEXT)
    assert sorted(v.rule_code for v in non_r6(result)) == ["R3", "R4"]


def test_a_speaker_mismatch_still_holds_a_reference_finding_for_review():
    result = run([ref("R3", 0, 6, 8, speaker="borrower")], TEXT)
    assert non_r6(result)[0].needs_review is True


def test_the_models_uncertainty_is_kept():
    assert run([ref("R3", 0, 6, 8, uncertain=True)], TEXT) and non_r6(run([ref("R3", 0, 6, 8, uncertain=True)], TEXT))[0].needs_review is True


# --- the echo's effect on a finding ----------------------------------------------------------------------------------


def test_an_echo_in_another_script_is_accepted_by_reference_and_counted():
    result = run([ref("R3", 0, 6, 8, quote="पुलिस भेज दूंगा")], TEXT)
    [v] = non_r6(result)
    assert v.quote == "police bhej dunga" and v.needs_review is False
    assert result.diagnostics["evidence"]["echo_not_found"] == 1


def test_an_echo_that_places_the_words_elsewhere_in_the_segment_holds_the_finding():
    result = run([ref("R3", 0, 6, 8, quote="paisa nahi aaya")], TEXT)
    [v] = non_r6(result)
    assert v.quote == "police bhej dunga" and v.needs_review is True  # position uncertain: not deducted until reviewed
    assert result.diagnostics["evidence"]["echo_outside_span"] == 1


def test_an_echo_found_only_in_another_segment_is_a_conflict_and_is_stored_unverified():
    result = run([ref("R3", 0, 0, 1, quote="tum jail jaoge zaroor")], TEXT, "tum jail jaoge zaroor", speakers=["agent", "agent"])
    assert non_r6(result) == []
    [r] = rejected_for(result)
    assert r["kind"] == llm_service.KIND_INVALID_EVIDENCE
    assert "appear in segment 1, not in the cited segment 0" in r["reason"]
    assert r["entry"]["quote"] == "tum jail jaoge zaroor"  # kept, so a manager can see what the model claimed
    assert result.diagnostics["evidence"]["echo_conflict"] == 1


def test_a_matching_echo_is_the_strongest_case_and_changes_nothing():
    [v] = non_r6(run([ref("R3", 0, 6, 8, quote="police bhej dunga")], TEXT))
    assert v.quote == "police bhej dunga" and v.needs_review is False


# --- the Call 2 Urdu regression (real stored text, the model's real rejected quote) ---------------------------------------


def call2_raw():
    return [RawSegment(seq=s["seq"], start=float(s["seq"]), end=float(s["seq"]) + 1.0, text=s["text"]) for s in FIXTURE["segments"]]


def call2_response(violations):
    data = response(["agent"] * len(FIXTURE["segments"]), violations=violations)
    data["segments"] = [seg_entry(s["seq"], s["speaker"]) for s in FIXTURE["segments"]]
    return data


def stored_segment_18():
    return next(s["text"] for s in FIXTURE["segments"] if s["seq"] == 18)


def test_the_fixture_is_the_real_failure():
    entry = FIXTURE["rejected_model_entry"]
    assert entry["segment_index"] == 18 and entry["quote"] == "हम आप के घर भी आएँगे"  # Devanagari
    assert all("؀" <= c <= "ۿ" or c in " ‌" for c in stored_segment_18())  # the stored text is Urdu script


def test_before_the_old_format_rejected_the_genuine_urdu_threat():
    entry = {k: v for k, v in FIXTURE["rejected_model_entry"].items()}
    _, result = ua.parse_and_validate(call2_response([entry]), call2_raw())
    assert non_r6(result) == []
    [r] = rejected_for(result)
    assert r["reason"].startswith("quote is not verbatim") and r["kind"] == llm_service.KIND_INVALID_EVIDENCE


def test_now_a_reference_with_the_same_devanagari_echo_gives_the_stored_urdu_quote():
    n_words = len(ev.word_spans(stored_segment_18()))
    entry = ref("R3", 18, 0, n_words - 1, quote=FIXTURE["rejected_model_entry"]["quote"],
                explanation="Agent threatens to come to the borrower's home")
    _, result = ua.parse_and_validate(call2_response([entry]), call2_raw())
    [v] = non_r6(result)
    assert v.quote == stored_segment_18()  # identical to the stored text, character for character
    assert v.quote.encode("utf-8") == stored_segment_18().encode("utf-8")
    assert all("؀" <= c <= "ۿ" or c in " ‌" for c in v.quote)  # still Urdu script; never re-scripted
    assert "हम" not in v.quote and v.needs_review is False
    assert rejected_for(result) == []
    assert result.diagnostics["evidence"]["echo_not_found"] == 1


def test_a_partial_urdu_span_is_the_exact_substring():
    text = stored_segment_18()
    words = ev.word_spans(text)
    [v] = non_r6(ua.parse_and_validate(call2_response([ref("R3", 18, 3, 5)]), call2_raw())[1])
    assert v.quote == text[words[3][0]:words[5][1]] and v.quote in text


# --- quote-only responses cannot bypass verification ---------------------------------------------------------------------


def quote_only(rule, idx, quote, **extra):
    entry = {"rule_code": rule, "segment_index": idx, "quote": quote, "speaker": "agent", "explanation": "x", "confidence": 0.9, "uncertain": False}
    entry.update(extra)
    return entry


def test_a_verbatim_quote_only_response_is_still_accepted():
    result = run([quote_only("R3", 0, "police bhej dunga")], TEXT)
    [v] = non_r6(result)
    assert v.quote == "police bhej dunga" and result.diagnostics["evidence"] == {"quote_only_findings": 1}


@pytest.mark.parametrize(
    "quote, reason",
    [
        ("we will burn your house down", "not verbatim"),
        ("पुलिस भेज दूंगा", "not verbatim"),
        ("", "missing quote"),
        ("   ", "missing quote"),
        (None, "missing quote"),
        (42, "missing quote"),
    ],
)
def test_a_quote_only_response_with_a_bad_quote_is_rejected(quote, reason):
    result = run([quote_only("R3", 0, quote)], TEXT)
    assert non_r6(result) == []
    [r] = rejected_for(result)
    assert reason in r["reason"] and r["kind"] == llm_service.KIND_INVALID_EVIDENCE


def test_a_quote_from_another_segment_does_not_verify_the_cited_one():
    result = run([quote_only("R3", 0, "tum jail jaoge zaroor")], TEXT, "tum jail jaoge zaroor", speakers=["agent", "agent"])
    assert non_r6(result) == [] and "not verbatim" in rejected_for(result)[0]["reason"]


def test_a_response_with_neither_a_quote_nor_a_reference_is_rejected():
    entry = {"rule_code": "R3", "segment_index": 0, "speaker": "agent", "explanation": "x", "confidence": 0.9}
    result = run([entry], TEXT)
    assert non_r6(result) == [] and rejected_for(result)[0]["reason"] == "missing quote"


def test_position_markers_copied_with_the_words_are_stripped_but_only_if_the_rest_is_verbatim():
    ok = run([quote_only("R3", 0, "6:police 7:bhej 8:dunga")], TEXT)
    assert non_r6(ok)[0].quote == "police bhej dunga"
    bad = run([quote_only("R3", 0, "6:burn 7:your 8:house")], TEXT)
    assert non_r6(bad) == [] and "not verbatim" in rejected_for(bad)[0]["reason"]
    only_markers = run([quote_only("R3", 0, "0: 1: 2:")], TEXT)
    assert non_r6(only_markers) == []


def test_stripping_markers_cannot_turn_a_time_of_day_into_something_else():
    # "10:30" is genuine text. The strip is only a fallback after the quote
    # failed as given, and the stripped result must itself be in the segment.
    assert non_r6(run([quote_only("R3", 0, "10:30 baje")], "call at 10:30 baje aaunga"))[0].quote == "10:30 baje"
    assert non_r6(run([quote_only("R3", 0, "10:30 baje")], "call at 11:45 baje aaunga")) == []


def test_old_and_new_formats_can_be_mixed_in_one_response_and_each_is_checked_by_its_own_rules():
    result = run([ref("R3", 0, 0, 1), quote_only("R4", 0, "burn the house"), quote_only("R5", 0, "paisa nahi")], TEXT)
    assert sorted(v.rule_code for v in non_r6(result)) == ["R3", "R5"]
    assert [r["entry"]["rule_code"] for r in rejected_for(result, "R4")] == ["R4"]


# --- the opening evidence tolerates copied position markers (still verbatim after stripping) -------------------------------


def test_opening_evidence_with_copied_markers_is_accepted_only_when_the_stripped_text_is_verbatim():
    text = "Good morning this is Priya calling from Acme Credit about your loan"  # not the company name, so the matcher cannot rescue it
    good = opening_of([[0]], comp(True, 0, "2:this 3:is 4:Priya"), comp(True, 0, "7:Acme 8:Credit"), comp(True, 0, "10:your 11:loan"))
    _, result = validate(response(["agent"], opening=good), text)
    assert result.diagnostics["r6"]["components"]["company"] == "stated"
    fake = opening_of([[0]], comp(True, 0, "2:this 3:is 4:Priya"), comp(True, 0, "7:Mahindra 8:Finance"), comp(True, 0, "10:your 11:loan"))
    _, result = validate(response(["agent"], opening=fake), text)
    assert result.diagnostics["r6"]["components"]["company"] != "stated"


# --- the prompt contract ---------------------------------------------------------------------------------------------------


def prompt():
    return ua.build_messages(segs("नमस्ते मैं बोल रहा हूँ"), None)[0]["content"]


def test_the_prompt_asks_for_a_position_not_a_retyped_quote():
    system = prompt()
    for phrase in ("BY POSITION", "word_start", "word_end", "both inclusive", "not part of the text", "reads the words from the transcript itself"):
        assert phrase in system
    assert "copied verbatim from exactly one segment" not in system  # the old requirement is gone
    assert "`quote` is optional" in system and "without the position markers" in system


def test_the_output_schema_has_the_reference_fields_and_an_optional_quote():
    system = prompt()
    assert '"word_start": <int>, "word_end": <int>, "quote": "<optional: the same words, copied exactly>"' in system


def test_the_prompt_tells_the_model_the_numbers_are_markers_and_where_they_count_from():
    assert "counting from 0 in each segment" in prompt() and "`n:`" in prompt()


def test_the_transcript_shows_every_word_with_its_position():
    user = ua.build_messages(segs("alpha beta gamma"), None)[1]["content"]
    assert "0:alpha 1:beta 2:gamma" in user


def test_numbering_adds_a_bounded_amount_of_text():
    plain = sum(len(s["text"]) for s in FIXTURE["segments"])
    numbered = sum(len(ev.number_words(s["text"])) for s in FIXTURE["segments"])
    assert numbered / plain < 1.8  # measured on the real Call 2 segments; a live run reports the true token cost


def test_the_prompt_is_still_free_of_scenario_specific_text():
    system = prompt()
    for banned in ("CALL-", "Karan", "Malhotra", "Preet", "Finley", "WORKED EXAMPLES", "गुद्ब", "बेवकूफ"):
        assert banned not in system
