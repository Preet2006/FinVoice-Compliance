"""
Deterministic, conservative matcher for whether "Shaha Finlease" (the
company name) was mentioned, tolerating known speech-to-text transcription
variants observed from Groq's whisper-large-v3-turbo on this project's
recordings.

Used only to override a model-reported "company name missing" verdict for
R6 (mandatory opening) -- never used to decide whether the agent's name or
the call's purpose were stated, and the caller is responsible for scoping
the text passed in to the agent's first two turns only.

Deliberately NOT general-purpose fuzzy matching: matches the exact
spelling, a curated list of actually-observed variants, or a short
word-window (1-3 words) within a strict similarity threshold and a narrow
length window -- so an unrelated word or long unrelated sentence can never
match by accident.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from typing import Optional

# Curated, exact transcription variants observed in this project's Groq
# whisper-large-v3-turbo output (English/Urdu-script/Hindi renderings of
# "Shaha Finlease"). New variants should be added here deliberately, after
# verifying them against a real transcript, not guessed.
_RAW_KNOWN_VARIANTS = [
    "Shaha Finlease",  # correct spelling, always accepted
    "Shaf Inlees",
    "Shahaf Inlees",
    "Shahah Finley's",
    "Shahafinlees",
    "शाफ इनलीज",
    "शहाफ इनलीज",
    "शहाफ इनली",
]

_MAX_WINDOW_WORDS = 3

_NUKTA = "़"


def _normalize(text: str) -> str:
    """
    Letters, digits AND combining marks, lowercased; everything else
    (spaces, punctuation) is dropped, so spacing and apostrophe differences
    between transcription variants never matter.

    Combining marks must be kept: in Devanagari every vowel sign (ा ि
    ी ...) is a combining mark, not a letter, and removing them reduces a
    word to its consonant skeleton ("शहा पिनली" -> "शहपनल"), which both
    destroys real matches and makes unrelated words look alike. The nukta is
    folded away ("ज़" == "ज"), because speech recognition does not
    distinguish them and it never changes which word is meant.
    """
    decomposed = unicodedata.normalize("NFD", text)
    folded = unicodedata.normalize("NFC", "".join(ch for ch in decomposed if ch != _NUKTA))
    return "".join(ch.lower() for ch in folded if unicodedata.category(ch)[0] in "LNM")


_KNOWN_VARIANTS_NORMALIZED = {_normalize(v) for v in _RAW_KNOWN_VARIANTS}


def _word_windows(text: str, max_words: int = _MAX_WINDOW_WORDS) -> list[str]:
    words = text.split()
    windows = []
    for size in range(1, max_words + 1):
        for i in range(len(words) - size + 1):
            windows.append(" ".join(words[i : i + size]))
    return windows


# --- phonetic matching -----------------------------------------------------------------------------------------
# Speech recognition renders "Shaha Finlease" in Latin, Devanagari or Urdu script with changed vowels, an extra or
# missing "h", a split or merged word. Vowels and "h" are therefore ignored and the CONSONANT SKELETON of a window of
# one to three words is compared with the canonical skeleton (S f n l s). A name is accepted only when its skeleton
# is close to that AND contains the distinctive "nl" core, so an unrelated company ("Shree Finserv", "Shah
# Enterprises", "Bajaj Finlease") never matches. Only the text the caller passes (the agent's opening) is searched.

_CANONICAL_SKELETON = "Sfnls"  # "Shaha Finlease"; "S" is the sh sound, the final "s" the plural / "ease"
_SKELETON_THRESHOLD = 0.8

_DEVANAGARI_CONSONANTS = {
    "क": "k", "ख": "k", "ग": "g", "घ": "g", "च": "C", "छ": "C", "ज": "s", "झ": "s", "ट": "t", "ठ": "t", "ड": "d", "ढ": "d",
    "ण": "n", "त": "t", "थ": "t", "द": "d", "ध": "d", "न": "n", "प": "f", "फ": "f", "ब": "b", "भ": "b", "म": "m", "य": "",
    "र": "r", "ल": "l", "व": "v", "श": "S", "ष": "S", "स": "s", "ह": "",
}
_URDU_CONSONANTS = {
    "ش": "S", "س": "s", "ص": "s", "ث": "s", "ز": "s", "ذ": "s", "ض": "s", "ظ": "s", "ج": "s", "ف": "f", "ن": "n", "ں": "n",
    "ل": "l", "ک": "k", "ق": "k", "ك": "k", "گ": "g", "غ": "g", "چ": "C", "خ": "k", "د": "d", "ڈ": "d", "ت": "t", "ٹ": "t",
    "ط": "t", "ب": "b", "پ": "f", "م": "m", "ر": "r", "ڑ": "r",
}
_COMPANY_INTRO_PARTICLES = {"se", "से", "سے", "from", "mein", "में"}


def _skeleton(text: str) -> str:
    """Consonant skeleton across Latin, Devanagari and Urdu script ("Shahaf Inlees", "शहाफ इनलीज", "شاہ فنلیس" -> SFNLS)."""
    out = []
    latin = []
    for ch in _normalize_for_skeleton(text):
        if "a" <= ch <= "z":
            latin.append(ch)
            continue
        out.append(_latin_skeleton("".join(latin)))
        latin = []
        out.append(_DEVANAGARI_CONSONANTS.get(ch) or _URDU_CONSONANTS.get(ch) or "")
    out.append(_latin_skeleton("".join(latin)))
    return _collapse("".join(out))


def _normalize_for_skeleton(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text)
    folded = unicodedata.normalize("NFC", "".join(ch for ch in decomposed if ch != _NUKTA))
    return "".join(ch.lower() if ch.isascii() else ch for ch in folded if unicodedata.category(ch)[0] == "L" or ch in "ںڑ")


def _latin_skeleton(word: str) -> str:
    word = word.replace("sh", "S").replace("ch", "C").replace("ph", "f")
    word = word.replace("kh", "k").replace("gh", "g").replace("th", "t").replace("dh", "d").replace("bh", "b")
    mapping = {"z": "s", "j": "s", "q": "k", "c": "k", "w": "v", "x": "ks", "p": "f"}  # p/f: recognisers confuse them
    kept = []
    for ch in word:
        if ch in "aeiouyh":
            continue
        kept.append(mapping.get(ch, ch))
    return "".join(kept)


def _collapse(skeleton: str) -> str:
    return re.sub(r"(.)+", r"", skeleton)


def _skeleton_match(candidate: str) -> bool:
    # Must begin with the Sh/S sound of "Shaha" and carry the distinctive "nl" of "Finlease".
    if candidate[:1] not in ("S", "s") or "nl" not in candidate:
        return False
    return difflib.SequenceMatcher(None, candidate, _CANONICAL_SKELETON).ratio() >= _SKELETON_THRESHOLD


_CORE_LATIN = re.compile(r"^(?:f|ph)?in+l(?:i|ee|ea|ey|ie|y)(?:s|z|ss|se)?$")
_CORE_DEVANAGARI = re.compile(r"^(?:फ)?(?:इ|ि)?(?:न)ल(?:ी|ि|े)(?:ज|स)?$|^(?:फ)?इनल(?:ी|ि)(?:ज|स)?$")
_CORE_URDU = re.compile(r"^(?:ف|ا)?نل(?:ی|ے)(?:س|ز)?$")

_FINANCE = re.compile(r"^(?:finance|finans|fainans|finanse|फाइनेंस|फाइनांस|फिनांस|फाइनैंस|فنانس|فائنانس)$")
_SHAH = re.compile(r"^(?:sha+h?a?h?|shaa+h?|शाह|शाहा|शहा|शाफ|शाहाफ|शहाफ|शाह|شاہ|شہا|شاها)$")


def _tokens(text: str) -> list:
    return [t for t in (_strip(w) for w in text.split()) if t]


def _strip(word: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFC", word.replace(_NUKTA, "")) if unicodedata.category(ch)[0] in "LM").lower()


def company_match(text: str, introduced: bool = False) -> Optional[str]:
    """How "Shaha Finlease" was recognised in `text`, or None. `introduced` says the surrounding text shows the agent
    naming their own employer while introducing themselves; it is required for the weakest evidence ("Shaha
    Finance", and a bare "...inlee se" core), never for a close rendering of the full name."""
    if not text or not text.strip():
        return None
    words = _tokens(text)
    if any(_normalize(w) in _KNOWN_VARIANTS_NORMALIZED for w in _word_windows(text)):
        return "variant"
    for size in range(1, _MAX_WINDOW_WORDS + 1):
        for i in range(len(words) - size + 1):
            if _skeleton_match(_skeleton(" ".join(words[i : i + size]))):
                return "phonetic"
    for i, word in enumerate(words):
        following = words[i + 1] if i + 1 < len(words) else ""
        if introduced and following in _COMPANY_INTRO_PARTICLES and (
            _CORE_LATIN.match(word) or _CORE_DEVANAGARI.match(word) or _CORE_URDU.match(word)
        ):
            return "core"
        if introduced and i + 1 < len(words) and _SHAH.match(word) and _FINANCE.match(words[i + 1]):
            return "finance-variant"
    return None


def company_name_mentioned(text: str, introduced: bool = False) -> bool:
    """
    True if `text` contains a recognizable mention of "Shaha Finlease": the exact spelling, a known observed
    variant, a close phonetic rendering (see above), or - only when `introduced` - a bare "...inlee se" core or
    "Shaha Finance". Never matches an empty string or an unrelated company.

    Callers decide what text to pass in -- for R6 this must be only the agent's first two turns, never the full
    transcript.
    """
    return company_match(text, introduced) is not None
