import pytest

from app.services.company_name_matcher import _normalize, company_name_mentioned


def test_vowel_signs_are_preserved_not_stripped():
    # Before the fix "शहा पिनली" was reduced to its consonants "शहपनल".
    assert _normalize("शहा पिनली") == "शहापिनली"
    assert "ा" in _normalize("शाह") and "ि" in _normalize("फिन") and "ी" in _normalize("पिनली")


def test_spacing_punctuation_and_case_are_still_ignored():
    assert _normalize("Shahah, Finley's") == "shahahfinleys"
    assert _normalize("शहा  पिनली!") == _normalize("शहापिनली")


def test_nukta_is_folded_so_precomposed_and_decomposed_forms_agree():
    assert _normalize("फिनलीज़") == _normalize("फिनलीज")
    assert _normalize("फ़िनलीज") == _normalize("फिनलीज")


@pytest.mark.parametrize(
    "text",
    [
        "शहा पिनली",  # the variant that was missed
        "नमस्ते मैं प्रीज वेकर बोल रहा हूँ शहा पिनली से मैं आपके पर्सनल लोन के पेंडिंग पेमेंट के बारे में बात कर रहा हूँ",
        "शहा पिनलीस",
        "शाफ इनलीज",
        "शहाफ इनलीज",
        "शहाफ इनली",
        "शाह फिनलीज",
        "शाहा फिनलीज़",
        "शाहाफिनलीज",
        "मैं रेखा बोल रही हूं शाह फिनलीज से",
    ],
)
def test_legitimate_devanagari_variants_are_recognised(text):
    assert company_name_mentioned(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "महिंद्रा फाइनेंस",
        "बजाज फिनसर्व",
        "टाटा कैपिटल",
        "हिंदुजा लेलैंड",
        "शाइन फाइनेंस",
        "शाहरुख खान",
        "शाह जी",
        "शहर में",
        "फाइनेंस कंपनी",
        "पर्सनल लोन",
        "आउटस्टैंडिंग पेमेंट",
        "आपके लोन अकाउंट में 40,000 रुपया पेंडिंग है",
        "नमस्ते मैं प्रीज वेकर बोल रहा हूँ महिंद्रा फाइनेंस से मैं आपके पर्सनल लोन के बारे में बात कर रहा हूँ",
    ],
)
def test_unrelated_devanagari_names_and_words_do_not_match(text):
    assert company_name_mentioned(text) is False


def test_variants_pass_the_skeleton_test_and_unrelated_names_do_not():
    from app.services import company_name_matcher as m

    def passes(text):
        words = text.split()
        return any(m._skeleton_match(m._skeleton(" ".join(words[i : i + n]))) for n in (1, 2, 3) for i in range(len(words) - n + 1))

    assert all(passes(t) for t in ["शहा पिनली", "शहा पिनलीस", "शाफ इनलीज", "शहाफ इनली"])
    assert not any(passes(t) for t in ["महिंद्रा फाइनेंस", "शाहरुख खान", "शाह जी", "बजाज फिनसर्व", "टाटा कैपिटल"])


@pytest.mark.parametrize("text", ["", "   ", "नमस्ते", "ा", "ि"])
def test_empty_or_marks_only_text_never_matches(text):
    assert company_name_mentioned(text) is False


def test_latin_matching_is_unchanged():
    assert company_name_mentioned("calling from Shahah, Finley's about your loan") is True
    assert company_name_mentioned("calling from Mahindra Finance about your loan") is False
