import pytest

from app.services.company_name_matcher import company_name_mentioned


@pytest.mark.parametrize(
    "text",
    [
        "Hello, this is Priya speaking from Shaha Finlease about your loan",
        "main Imran bol raha hoon, Shaf Inlees se",
        "main Rekha bol rahi hoon, Shahaf Inlees se",
        "this is Preet Javekar calling from Shahah Finley's about your account",
        "this is Preet Javekar calling from Shahafinlees about your credit card account",
        "नमस्ते मैं प्रीज जगे कर बोल रहा हूं शाफ इनलीज से",
        "मैं पूरीच घवेकर बोल रहा हूं शहाफ इनलीज से एक जरूरी बात",
        "एप्रेश विए प्रिश विए कर बोल रहा हूं शहाफ इनली से",
    ],
)
def test_known_variants_recognized(text):
    assert company_name_mentioned(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "Hello, am I speaking to Prakash Nayak?",
        "Sir, your personal loan is overdue by 35,000 rupees. You need to pay today.",
        "main Dinesh bol raha hoon",
        "aapke account mein abhi paise baaki hain kripya jaldi bhar dein",
        "",
        "   ",
    ],
)
def test_unrelated_text_not_matched(text):
    assert company_name_mentioned(text) is False


def test_correct_spelling_matches():
    assert company_name_mentioned("Shaha Finlease se baat kar rahi hoon") is True


def test_unrelated_similarly_long_word_not_matched():
    # A sanity check that the fallback similarity threshold doesn't swallow
    # an unrelated word of similar length.
    assert company_name_mentioned("Mahindra Finance services department") is False
