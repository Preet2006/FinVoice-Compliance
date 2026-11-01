"""
R6 mandatory opening: tolerant recognition of the company name "Shaha Finlease" in garbled speech-to-text, and the
checks around it. The stored evaluation transcripts of CALL-0001, CALL-0005 and CALL-0009 are used verbatim.

Root causes of those three false positives (see the test names): CALL-0001 - Whisper rendered the company as
"का इनली" and the opening was garbled; CALL-0005 and CALL-0009 - the company was recognised but the model did not
credit the purpose, which the agent stated in plain English in its second turn.
"""

import pytest

from app.services import opening_service as os_
from app.services import unified_analysis_service as ua
from app.services.company_name_matcher import company_match, company_name_mentioned
from app.services.llm_service import LabelledSegment
from tests.unit.test_unified_analysis import comp, opening_of, response


# ---------------------------------------------------------------------------------------------- company name
@pytest.mark.parametrize("text", [
    "this is Pooja calling from Shaha Finlease",             # exact
    "this is Pooja calling from Shah Finlease",               # minor spelling
    "this is Pooja calling from Shaha Finleasee",
    "this is Pooja calling from Sha Fin lees",                # split
    "this is Pooja calling from Shaw Finlee",
    "main Pooja bol rahi hoon Shaha Finlease se",
    "मैं पूजा बोल रही हूं शाह फिनलीस से",                      # Devanagari transliteration
    "मैं पूजा बोल रही हूं शहा पिनली से",                       # p/f confusion
    "میں پوجا بول رہی ہوں شاہ فنلیس سے",                      # Urdu script
])
def test_the_company_is_recognised_through_spelling_and_script_variation(text):
    assert company_name_mentioned(text) is True


def test_shaha_finance_is_accepted_only_when_the_agent_is_introducing_themselves():
    assert company_match("Shaha Finance", introduced=False) is None
    assert company_match("this is Pooja from Shaha Finance", introduced=True) == "finance-variant"


def test_a_bare_inlee_se_core_is_accepted_only_in_an_introduction():
    assert company_match("का इनली से", introduced=False) is None
    assert company_match("का इनली से", introduced=True) == "core"


@pytest.mark.parametrize("text", [
    "this is Pooja calling from Shree Finserv",
    "this is Pooja calling from Shah Enterprises",
    "this is Pooja calling from HDFC Finance",
    "this is Pooja calling from Bajaj Finlease",       # similar ending, wrong company
    "this is Pooja calling from Shahi Finance",
    "this is Anil calling about your loan",           # an "n l" name is not the company
    "Hello, am I speaking with Mr Rakesh Menon?",
])
def test_an_unrelated_company_or_phrase_is_not_accepted(text):
    assert company_name_mentioned(text, introduced=True) is False


def test_a_completely_garbled_name_stays_unrecognised():
    assert company_match("ब्राराँ आमस्ते हैं अच्त कर बोल रहा हूं", introduced=True) is None


# ---------------------------------------------------------------------------------------------- the opening, on the stored calls
def lab(*rows):
    return [LabelledSegment(seq=i, speaker=sp, start=i * 3.0, end=i * 3.0 + 3.0, text=t, confidence=0.95) for i, (sp, t) in enumerate(rows)]


CALL_0001 = lab(
    ("agent", "ब्राराँ आमस्ते हैं अच्त कर बोल रहा हूं का इनली से क्या मेरी बात दिनेश क से हैं स्यर्व"),
    ("agent", "यह निटेश ब्रफलिये c यह कॉल कॉलधें आपके आर्मिजय की सबूपिशन करने शाहाएं कि अमेरे में"),
    ("agent", "क्या आप अभी बात कर सकते हैं?"),
    ("borrower", "हाँ बोलिए"),
    ("agent", "आपके लोन अमाउंट में अभी 48,000 रुपे बाकी है"),
)
CALL_0005 = lab(
    ("agent", "Good morning, this is Preet Javekar calling from Shahafinlees about your credit card account."),
    ("agent", "Am I speaking with Mr. Rakesh Menon?"),
    ("borrower", "Yes, again. You people calling every single day."),
    ("agent", "I understand. It is frustrating, sir, and I apologize."),
    ("agent", "This call is recorded. I am calling about the outstanding payment of 27,500 rupees on your credit card. Is this a good time to talk?"),
)
CALL_0009 = lab(
    ("agent", "Good afternoon. This is Prit Jhavikar calling from Shahah, Finley's regarding Mr. Karan Malhotra."),
    ("agent", "Am I speaking with someone from his office?"),
    ("borrower", "Yes, I am his colleague. May I know why are you calling?"),
    ("agent", "I am calling because Karan has an outstanding loan of 55,000 rupees to the company."),
)


def opening(labelled, name=None, company=None, purpose=None):
    data = response([s.speaker for s in labelled], opening=opening_of(
        [[s.seq for s in turn] for turn in ua.first_agent_turns(labelled)], name or comp(), company or comp(), purpose or comp()))
    diagnostics: dict = {}
    violation, held = ua._evaluate_opening(data, labelled, diagnostics)
    return violation, held, diagnostics


def test_call_0001_a_garbled_company_is_recognised_and_the_unclear_name_is_held_not_charged():
    # The model credited only the purpose; the company ("का इनली से") and name were garbled.
    violation, held, d = opening(CALL_0001, purpose=comp(True, 1, "आपके आर्मिजय की सबूपिशन"))
    assert violation is None and held is not None
    assert d["r6"]["components"] == {"name": "unclear", "company": "stated", "purpose": "stated"}
    assert d["r6"]["company_match"] == "core"


def test_call_0005_the_purpose_stated_in_turn_two_is_recognised():
    violation, held, d = opening(CALL_0005, name=comp(True, 0, "this is Preet Javekar"), company=comp(True, 0, "Shahafinlees"))
    assert violation is None and held is None and d["r6"]["purpose_recognised_by"] == "matcher"


def test_call_0009_the_purpose_stated_in_turn_two_is_recognised():
    violation, held, d = opening(CALL_0009, name=comp(True, 0, "This is Prit Jhavikar"), company=comp(True, 0, "Shahah, Finley's"))
    assert violation is None and held is None and d["r6"]["purpose_recognised_by"] == "matcher"


# ---------------------------------------------------------------------------------------------- what must still be a problem
def segments(*texts, first="agent"):
    rows = [(first if i == 0 else "agent", t) for i, t in enumerate(texts)]
    return lab(*rows)


def test_a_company_match_alone_does_not_make_the_opening_compliant():
    # Company named, but no agent name and no purpose.
    violation, held, _ = opening(segments("Shaha Finlease se."), company=comp(True, 0, "Shaha Finlease"))
    assert violation is not None and "agent name" in violation.explanation and "call purpose" in violation.explanation
    assert "company name" not in violation.explanation


def test_the_matcher_recognises_the_company_even_when_the_model_did_not():
    violation, _, d = opening(segments("Hello, this is Priya from Shah Finlease. Am I speaking with Karan?"),
                              name=comp(True, 0, "this is Priya"))
    assert d["r6"]["company_match"] == "phonetic"
    assert violation is not None and "company name" not in violation.explanation  # only the purpose is missing


def test_a_missing_agent_name_still_causes_an_r6_issue():
    violation, held, _ = opening(segments("Calling from Shaha Finlease about your loan payment."),
                                 company=comp(True, 0, "Shaha Finlease"), purpose=comp(True, 0, "about your loan payment"))
    assert violation is not None and violation.explanation.startswith("Mandatory opening missing required element(s): agent name")


def test_a_missing_call_purpose_still_causes_an_r6_issue():
    violation, _, _ = opening(segments("Hello, this is Priya from Shaha Finlease. Am I speaking with Karan?"),
                              name=comp(True, 0, "this is Priya"), company=comp(True, 0, "Shaha Finlease"))
    assert violation is not None and "call purpose" in violation.explanation


def test_an_unrelated_company_is_still_a_missing_company():
    violation, _, _ = opening(segments("Hello, this is Priya from Bajaj Finlease about your loan payment, am I speaking with Karan?"),
                              name=comp(True, 0, "this is Priya"), purpose=comp(True, 0, "about your loan payment"))
    assert violation is not None and "company name" in violation.explanation


def test_the_purpose_needs_a_reason_for_calling_not_just_a_debt_word():
    assert os_.purpose_mentioned("Sir, aapka payment pending hai. Kab tak karenge?") is False  # a statement, no reason given
    assert os_.purpose_mentioned("Hello, this is Tarun from Shaha Finlease") is False
    assert os_.purpose_mentioned("I am calling about the loan, sir.") is True


def test_a_borrower_labelled_first_segment_still_holds_the_opening_whatever_the_company_says():
    labelled = lab(("borrower", "Hello this is Priya from Shaha Finlease about your loan payment"), ("agent", "ji"))
    violation, held, _ = opening(labelled, company=comp(True, 0, "Shaha Finlease"))
    assert violation is None and held is not None and "first segment" in held["entry"]["explanation"]
