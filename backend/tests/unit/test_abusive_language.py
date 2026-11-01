"""
R2 abusive language: the contextual vocabulary check (app/services/abusive_language.py) and how the unified analysis
uses it. The model decides R2; this check adds a HELD finding (never a deduction) for a clear hit the model did not
report. The transcripts of CALL-0002 and CALL-0014 below are the stored evaluation transcripts, verbatim.

LIMITATION: the labels behind those two calls are script-derived, not audio-verified.
"""

import pytest

from app.services import abusive_language as al
from app.services import unified_analysis_service as ua
from tests.unit.test_unified_analysis import response, seg_entry, segs

HELD_PREFIX = "Possible abusive language"


def quotes(text, previous=""):
    return [(h.quote, h.strength) for h in al.detect_in_segment(0, text, previous)]


# ---------------------------------------------------------------------------------------------- detection
@pytest.mark.parametrize("text,expected", [
    ("You are an idiot and you know it", "idiot"),                       # English
    ("आप बेवकूफ हैं", "बेवकूफ"),                                           # Hindi, Devanagari
    ("आप ہمیں بے وکوف سمجھتے ہو", "بے وکوف"),                             # Urdu script, the recogniser split the word
    ("aap bewakoof ho", "bewakoof"),                                      # Roman Hindi
    ("aap hamein bevakoof samajhte ho kya", "bevakoof"),                  # spelling variant
    ("tum bewakuf ho", "bewakuf"),                                        # transliteration variant
    ("aap log nikamme ho", "nikamme"),
    ("Tum chutiya ho", "chutiya"),                                        # strong term
    ("You are a bastard", "bastard"),
    ("आप नालाइक हो", "नालाइक"),                                           # common Devanagari ASR spelling
    ("आप बेकूफ हो", "बेकूफ"),                                              # missing व in ASR output
    ("aap bekuf ho", "bekuf"),                                             # Roman Hindi spelling variant
])
def test_abusive_language_is_detected_in_each_language_and_spelling(text, expected):
    assert [q for q, _ in quotes(text)] == [expected]


@pytest.mark.parametrize("text", [
    "You are not an idiot",                      # negated
    "aap bewakoof nahi ho",                      # negated, Roman Hindi
    "aap bewakoof mat bano",                     # negated imperative
    "Don't use the word idiot with customers",   # discussing a word
    "He called me a fool yesterday",             # reported speech
    "If I said you are an idiot, you would complain",  # hypothetical
    '"idiot" is not a word we use here',         # quoted
    "I am an idiot, sorry",                      # self-reference
    "main bewakoof hoon",                        # self-reference, Roman Hindi
    "This design is stupid",                     # not aimed at the listener
    "The stupid printer is broken again",        # neutral use
    "aap ऐसे बात मत करिए",                         # nothing abusive at all
    "Hello, am I speaking with Mr Rakesh Menon?",
])
def test_neutral_negated_quoted_or_hypothetical_uses_are_not_flagged(text):
    assert quotes(text) == []


def test_a_weak_term_alone_is_not_enough_but_the_same_term_aimed_at_the_listener_is():
    assert quotes("bewakoof") == []
    assert quotes("aap bewakoof ho") != []


def test_a_head_full_of_straw_idiom_needs_a_mind_word_nearby():
    assert [q for q, _ in quotes("यह सब भूसा भरा है", previous="दिमाग में कुछ है भी या नहीं")] == ["भूसा भरा"]
    assert quotes("यह सब भूसा भरा है", previous="गोदाम में क्या है") == []  # straw in a barn is just straw


def test_the_quote_is_the_exact_stored_text_with_its_word_positions():
    text = "ek mahina?   Aap hamein bewakoof samajhte ho kya"
    [hit] = al.detect_in_segment(7, text)
    assert text[text.index(hit.quote): text.index(hit.quote) + len(hit.quote)] == hit.quote == "bewakoof"
    assert (hit.segment_seq, hit.word_start, hit.word_end) == (7, 4, 4)


def test_very_short_or_unlisted_words_are_not_fuzzy_matched():
    assert quotes("aap saala ho") == []          # mild; deliberately not in the vocabulary
    assert quotes("aap pagli ho") == []          # not an entry; no unrestricted fuzzy matching
    assert quotes("MC Donalds ke baare mein") == []  # an ambiguous abbreviation is not in the vocabulary


# ---------------------------------------------------------------------------------------------- the stored evaluation calls
CALL_0002 = [
    "ہیلو میں عمران بول رہا ہوں شاہ فنلیس سے آپ کے کرڈٹ کارڈ کے بارے میں کال کیا ہے",
    "پراکھان جی سے بات ہو رہی ہے",
    "ہاں میں بڑا بول رہی ہوں",
    "میڈم آپ کے کرڈٹ کارڈ کا 62000 روپیا باقی ہے کب دے رہے ہو",
    "ابھی پیسے نہیں ہے مجھے سوڑا ٹائم چاہیے",
    "آپ ہمیشہ یہی بولتے ہو", "کتنا ٹائم چاہیے", "بس ایک مہینہ دے دیجئے", "ایک مہینہ",
    "آپ ہمیں بے وکوف سمجھتے ہو کیا",
    "آپ ایسے بات نہ کیجئے", "اگر کل تک پیسہ نہیں آیا", "تو میں پولیس بھیج دوں گا", "اور آپ کو جیل جانا پڑے گا",
]
CALL_0014 = [
    "ہیلو میں سمیر قریشی بول رہا ہوں شہا فنلیس سے آپ کے پرسنل لون کی پینڈنگ پیمنٹ کے بارے میں کیا میری بات دیپک پلائی جی سے ہو رہی ہے",
    "ہاں دیپک بول رہا ہوں آپ کا 9,800 تین ہفتے سے پینڈنگ ہے پیمنٹ کب تک کرو گے",
    "سر ابھی تھوڑا ٹائٹ چل رہا ہے", "اگلے مہینے کی پہلی تاریخ تک ہو جائے گا", "ہر بار وہی بہانہ",
    "آپ لوگ کبھی سیدھی بات بات نہیں کرنا چاہتے", "بلکل نکم میں ہوں", "سر آپ ایسے بات مت کیجئے", "میں بھی ٹرائے کر رہا ہوں", "ٹرائے",
    "اتنا بھی سمجھ نہیں آتا کیا", "دماغ میں کچھ ہے بھی یا نہیں", "یہ سب بھوسا بھرائے", "میں کمپلین کر دوں گا",
]


def model_answer_as_captured(texts, agent_until):
    """What the model returned for these calls: only the first segments labelled agent, every later segment
    labelled borrower at high confidence, no R2 reported (the captured behaviour that missed the abuse)."""
    speakers = [seg_entry(i, "agent" if i <= agent_until else "borrower", 0.97) for i in range(len(texts))]
    return response(speakers)


def held_r2(result):
    return [v for v in result.violations if v.rule_code == "R2"]


def test_call_0002_the_insult_the_model_missed_is_found_and_held_with_exact_evidence():
    _, result = ua.parse_and_validate(model_answer_as_captured(CALL_0002, 3), segs(*CALL_0002))
    [v] = held_r2(result)
    assert v.segment_seq == 9 and v.quote == "بے وکوف" and v.quote in CALL_0002[9]
    assert v.needs_review is True  # held: it never deducts until a manager confirms it
    assert v.explanation.startswith(HELD_PREFIX) and "may be wrong" in v.explanation  # states the speaker-label doubt
    assert result.diagnostics["abusive_language"]["added_held"] == 1


def test_call_0014_the_straw_idiom_the_model_missed_is_found_and_held():
    _, result = ua.parse_and_validate(model_answer_as_captured(CALL_0014, 0), segs(*CALL_0014))
    [v] = held_r2(result)
    assert v.segment_seq == 12 and v.quote == "بھوسا بھرائے" and v.needs_review is True


def test_call_0014_garbled_self_reference_is_not_invented_into_a_finding():
    # "بلکل نکم میں ہوں" ("bilkul nikamm main hoon") reads as the speaker describing themselves; the check does not guess.
    assert al.detect_in_segment(6, CALL_0014[6]) == []


def test_a_hit_the_model_already_reported_is_not_repeated():
    answer = model_answer_as_captured(CALL_0002, 3)
    answer["violations"] = [{"rule_code": "R2", "segment_index": 9, "word_start": 2, "word_end": 3, "speaker": "agent",
                             "explanation": "insult", "confidence": 0.9, "uncertain": False}]
    answer["rule_outcomes"]["R2"]["violated"] = True
    _, result = ua.parse_and_validate(answer, segs(*CALL_0002))
    assert len(held_r2(result)) == 1 and held_r2(result)[0].explanation == "insult"
    assert result.diagnostics["abusive_language"]["already_reported"] == 1


def test_abuse_by_the_borrower_is_not_charged_to_the_agent():
    texts = ["Hello this is Priya from Shaha Finlease", "Yes who is this", "You are an idiot, leave me alone", "Please calm down sir"]
    speakers = [seg_entry(0, "agent"), seg_entry(1, "borrower"), seg_entry(2, "borrower"), seg_entry(3, "agent")]
    _, result = ua.parse_and_validate(response(speakers), segs(*texts))
    assert held_r2(result) == [] and result.diagnostics["abusive_language"]["ignored_borrower_segments"] == 1


def test_a_clear_insult_in_an_agent_segment_the_model_did_not_report_is_held_not_deducted():
    texts = ["Hello this is Priya from Shaha Finlease", "Yes who is this", "aap bewakoof ho kya", "ji sorry"]
    speakers = [seg_entry(0, "agent"), seg_entry(1, "borrower"), seg_entry(2, "agent"), seg_entry(3, "borrower")]
    _, result = ua.parse_and_validate(response(speakers), segs(*texts))
    [v] = held_r2(result)
    assert (v.segment_seq, v.quote, v.needs_review) == (2, "bewakoof", True) and "did not report it" in v.explanation


def test_a_borrower_run_with_common_asr_spelling_is_held_not_charged_to_agent():
    texts = [
        "Hello this is Priya from Shaha Finlease",
        "Yes who is this",
        "Aap theek se baat kijiye",
        "Main payment kal karunga",
        "Aap jaise nalayik log system kharab karte hain bilkul bekuf ho aap",
        "Please calm down",
    ]
    _, result = ua.parse_and_validate(model_answer_as_captured(texts, 0), segs(*texts))
    findings = held_r2(result)
    assert {v.quote for v in findings} == {"nalayik", "bekuf"}
    assert all(v.segment_seq == 4 and v.needs_review is True for v in findings)
    assert all("may be wrong" in v.explanation for v in findings)


def test_a_clean_call_gets_no_added_finding():
    texts = ["Hello this is Priya from Shaha Finlease", "Yes", "Your payment is pending, when can you pay?", "Next week"]
    speakers = [seg_entry(0, "agent"), seg_entry(1, "borrower"), seg_entry(2, "agent"), seg_entry(3, "borrower")]
    _, result = ua.parse_and_validate(response(speakers), segs(*texts))
    assert held_r2(result) == [] and "abusive_language" not in result.diagnostics


def test_the_prompt_asks_for_multilingual_r2_and_for_per_segment_labelling():
    from types import SimpleNamespace

    system = ua.build_messages(segs("hello"), SimpleNamespace(start_time=None, account_type="x", language="hi"))[0]["content"]
    assert "Urdu script" in system and "bewakoof" in system and "LABELLING RUNS" in system
