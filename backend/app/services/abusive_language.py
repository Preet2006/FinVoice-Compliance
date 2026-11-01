"""
Contextual abusive-language check (R2) used as SUPPORTING EVIDENCE next to the model's own judgement.

The model decides R2 from the whole conversation. This module re-reads the stored transcript with a small, curated
multilingual vocabulary (English, Hindi in Devanagari, Hindi/Urdu in Urdu script, and Roman Hindi, with common
transliteration and recogniser variants) and reports only what the words clearly show:

  * A STRONG term (a slur or profanity) counts wherever it is used as abuse.
  * A WEAK term ("idiot", "bewakoof", "nikamma") counts only when it is aimed at the listener: a second-person
    word ("you", "aap", "tum") in the same segment, or "ho" right after it. "I am an idiot" or "that idea is
    stupid" is not abuse of the listener.
  * Nothing counts when the context says the speaker is not abusing anyone: negation ("you are not an idiot"),
    quoting or discussing a word ("don't use the word idiot", "he called me a fool"), or a hypothetical ("if I said
    you are an idiot").
  * A two-word idiom ("bhoosa bhara" - a head full of straw) counts only when "dimaag"/"head" is in the same or the
    previous segment.

It never decides WHO spoke. Whether a hit is attributed to the agent is the caller's job (speaker labels), and the
caller treats this module's hits as findings for a human to confirm, never as automatic deductions. The evidence of
a hit is a word range inside ONE stored segment; the quote is read from that segment, never typed by this module.

This is deliberately not a keyword list of every offensive word: short ambiguous words are absent, and a weak term
alone is never enough.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

from app.services import evidence_span_service as spans

_NUKTA = "़"
_STRIP_MARKS = re.compile("[ً-ٰٟ‌‍॑-॔]")  # Arabic diacritics, ZWNJ/ZWJ, Vedic marks


def fold(token: str) -> str:
    """One comparable form of a word across scripts and spellings: lowercase, no punctuation, no diacritics or
    nukta, Latin 'w'->'v', 'q'->'k', doubled letters collapsed, long vowels shortened. Devanagari and Urdu keep
    their own letters (they are listed in their own script)."""
    text = unicodedata.normalize("NFC", token.replace(_NUKTA, ""))
    text = _STRIP_MARKS.sub("", text).lower()
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] in "LM")
    text = text.replace("ي", "ی").replace("ك", "ک").replace("ہ", "ه").replace("ھ", "ه").replace("ۓ", "ے")
    if text.isascii():
        text = text.replace("ph", "f").replace("w", "v").replace("q", "k").replace("z", "j")
        text = re.sub(r"(.)\1+", r"\1", text)  # "bewakoooof" -> "bevakof"
        text = text.replace("ee", "i").replace("oo", "u")
    return text


# --- the vocabulary ----------------------------------------------------------------------------------------------
# Surface forms are folded with `fold` at import, so spelling variants collapse (bewakoof / bevakoof / bewakuf).
# Latin entries tolerate small recogniser errors (difflib ratio >= 0.84, words of 5+ letters); Devanagari and Urdu
# entries are matched exactly after folding, because short words in those scripts are too easy to confuse.
_STRONG = {
    "slur": [
        "madarchod", "behenchod", "bhenchod", "bhosdike", "bhosdi", "chutiya", "chutiye", "gandu", "randi",
        "harami", "haramkhor", "haramzada", "kamina", "kamine", "kutta", "kutte", "kuttiya", "saale kutte",
        "bastard", "asshole", "bitch", "motherfucker", "fuck", "fucking", "dickhead", "scum", "bullshit",
        "मादरचोद", "बहनचोद", "भोसड़ीके", "चूतिया", "गांडू", "रांड", "हरामी", "हरामखोर", "हरामज़ादा", "कमीना", "कमीने", "कुत्ता", "कुत्ते",
        "مادرچود", "بہنچود", "چوتیا", "گانڈو", "رنڈی", "حرامی", "حرامخور", "کمینہ", "کمینے", "کتا", "کتے",
    ],
}
_WEAK = [
    "idiot", "stupid", "moron", "fool", "dumb", "useless fellow", "shut up", "good for nothing",
    "bewakoof", "bevakoof", "bevkoof", "bewkoof", "bewakuf", "bekuf", "bekooof", "nikamma", "nikamme", "nikama", "nalayak", "nalayik", "ullu", "gadha", "gadhe",
    "pagal", "jahil", "badtameez", "besharam", "bekar",
    "बेवकूफ", "बेवकुफ", "बे वकूफ", "बेकूफ", "निकम्मा", "निकम्मे", "नालायक", "नालाइक", "उल्लू", "गधा", "गधे", "पागल", "जाहिल", "बदतमीज़", "बेशर्म",
    "بیوقوف", "بیوکوف", "بے وقوف", "بے وکوف", "نکما", "نکمے", "نالائق", "الو", "گدھا", "گدھے", "پاگل", "جاہل", "بدتمیز", "بے شرم",
]
_COMPOUND_WEAK = [  # (head, partner, needs a head/mind word nearby)
    ("bhosa", "bhara"), ("bhusa", "bhara"), ("bhoosa", "bhara"), ("भूसा", "भरा"), ("بھوسا", "بھرا"), ("بھوسا", "بھرائے"),
]
_MIND_WORDS = {"dimaag", "dimag", "dmag", "mind", "brain", "sar", "head", "दिमाग", "दिमाग़", "सर", "دماغ", "سر"}

_SECOND_PERSON = {
    "you", "your", "youre", "u", "ur", "aap", "ap", "aapko", "aapka", "aapke", "aapki", "aaplog", "tum", "tumhe", "tumhara",
    "tumhare", "tumhari", "tu", "tujhe", "tera", "tere", "teri", "आप", "आपको", "आपका", "आपके", "आपकी", "तुम", "तुम्हें", "तुम्हारा", "तू", "तुझे",
    "آپ", "آپکو", "آپکا", "تم", "تمہیں", "تمہارا", "تجھے", "تیرا",
}
_SECOND_PERSON_COPULA = {"ho", "हो", "ہو"}
_FIRST_PERSON = {"main", "mein", "mai", "i", "im", "me", "myself", "hoon", "hun", "hu", "मैं", "हूं", "हूँ", "میں", "ہوں", "hoo"}
_NEGATION = {"not", "never", "no", "dont", "isnt", "arent", "wasnt", "nahi", "nahin", "nhi", "mat", "na", "नहीं", "नही", "मत", "न", "نہیں", "نہ", "مت", "nahee"}
_QUOTE_OR_REPORT = {
    "word", "words", "called", "calls", "calling you", "said", "says", "say", "saying", "example", "meaning", "means", "term", "language",
    "kehna", "kehte", "kahte", "kaha", "kahna", "bolna", "bolte", "bola", "lafz", "shabd", "matlab",
    "कहना", "कहते", "कहा", "बोला", "शब्द", "मतलब", "जैसे", "لفظ", "کہنا", "کہتے", "کہا", "بولا", "مطلب", "جیسے",
}
_HYPOTHETICAL = {"if", "suppose", "imagine", "would", "might", "hypothetically", "agar", "agr", "warna", "अगर", "यदि", "اگر"}
_QUOTE_CHARS = "\"“”‘’«»「」"


@dataclass(frozen=True)
class AbuseHit:
    segment_seq: int
    word_start: int
    word_end: int
    quote: str  # exact stored text of the cited words
    term: str  # the vocabulary entry that matched
    strength: str  # "strong" | "weak" | "idiom"
    note: str  # why it counts, for the reviewer


def _entries():
    for kind, items in _STRONG.items():
        for item in items:
            yield "strong", item
    for item in _WEAK:
        yield "weak", item


def _compile():
    table: dict = {}
    for strength, item in _entries():
        words = tuple(fold(w) for w in item.split())
        table.setdefault(len(words), {}).setdefault(" ".join(words), (strength, item))
    return table


_TABLE = _compile()
_FUZZY_MIN_LEN = 6  # shorter Latin words must match exactly


def _lookup(folded: str, ascii_word: bool) -> Optional[tuple]:
    for entries in _TABLE.values():
        hit = entries.get(folded)
        if hit:
            return hit
    if ascii_word and len(folded) >= _FUZZY_MIN_LEN:
        import difflib

        best = None
        for entries in _TABLE.values():
            for key, value in entries.items():
                if key.isascii() and len(key) >= _FUZZY_MIN_LEN and abs(len(key) - len(folded)) <= 2:
                    ratio = difflib.SequenceMatcher(None, key, folded).ratio()
                    if ratio >= 0.84 and (best is None or ratio > best[0]):
                        best = (ratio, value)
        if best:
            return best[1]
    return None


def _tokens(text: str) -> list:
    return [m.group(0) for m in re.finditer(r"\S+", text)]


def _around(folded_tokens: list, start: int, end: int, before: int, after: int) -> list:
    return folded_tokens[max(0, start - before) : start] + folded_tokens[end + 1 : end + 1 + after]


def _suppressed(raw_tokens: list, folded: list, start: int, end: int) -> Optional[str]:
    """The reason this hit is NOT abuse of the listener, or None."""
    text = " ".join(raw_tokens[start : end + 1])
    if any(ch in text for ch in _QUOTE_CHARS) or (
        start > 0 and raw_tokens[start - 1][-1:] in _QUOTE_CHARS
    ) or (end + 1 < len(raw_tokens) and raw_tokens[end + 1][:1] in _QUOTE_CHARS):
        return "the word is in quotation marks"
    if any(t in _NEGATION for t in _around(folded, start, end, 3, 2)):
        return "the word is negated"
    if any(t in _HYPOTHETICAL for t in folded[:end]):
        return "the sentence is hypothetical"
    if any(t in _QUOTE_OR_REPORT for t in folded):
        return "the word is being quoted or discussed"
    return None


def _aimed_at_listener(folded: list, start: int, end: int) -> bool:
    if any(t in _SECOND_PERSON for t in folded):
        return True
    return any(t in _SECOND_PERSON_COPULA for t in folded[end + 1 : end + 3])


def detect_in_segment(seq: int, text: str, previous_text: str = "") -> list:
    """Hits in ONE segment. `previous_text` (the segment before it) is used only to support a two-word idiom."""
    raw = _tokens(text)
    folded = [fold(t) for t in raw]
    hits: list = []
    used = set()
    for i in range(len(raw)):
        if i in used:
            continue
        for width in (2, 1):
            if i + width > len(raw):
                continue
            window = " ".join(folded[i : i + width])
            if not window.strip():
                continue
            found = _lookup(window, width == 1 and raw[i].isascii())  # tolerant spelling only for a single word
            if not found:
                continue
            strength, item = found
            start, end = i, i + width - 1
            reason = _suppressed(raw, folded, start, end)
            if reason:
                break
            if strength == "weak":
                if any(t in _FIRST_PERSON for t in folded[end + 1 : end + 3]) and not any(t in _SECOND_PERSON for t in folded):
                    break  # "bilkul nikamma main hoon": self-reference, not abuse of the listener
                if not _aimed_at_listener(folded, start, end):
                    break
                note = f'"{item}" is aimed at the listener'
            else:
                note = f'"{item}" is a slur or profanity'
            used.update(range(start, end + 1))
            hits.append(_hit(seq, text, start, end, item, strength, note))
            break
    # two-word idiom ("bhoosa bhara"): the head word and its partner, plus a head/mind word nearby
    for head, partner in _COMPOUND_WEAK:
        fh, fp = fold(head), fold(partner)
        for i in range(len(folded) - 1):
            if folded[i] == fh and folded[i + 1].startswith(fp[: max(3, len(fp) - 2)]) and i not in used:
                context = set(folded) | {fold(w) for w in _tokens(previous_text)}
                if not (context & {fold(w) for w in _MIND_WORDS}):
                    continue
                if _suppressed(raw, folded, i, i + 1):
                    continue
                used.update({i, i + 1})
                hits.append(_hit(seq, text, i, i + 1, f"{head} {partner}", "idiom", f'"{head} {partner}" (head full of straw) said with "dimaag"/head nearby'))
    return sorted(hits, key=lambda h: h.word_start)


def _hit(seq: int, text: str, start: int, end: int, term: str, strength: str, note: str) -> AbuseHit:
    span = spans.resolve_reference(seq, text, start, end)
    return AbuseHit(seq, start, end, span.quote, term, strength, note)


def detect(segments: list) -> list:
    """Hits across a transcript. `segments`: objects with .seq and .text, in order."""
    ordered = sorted(segments, key=lambda s: s.seq)
    out: list = []
    previous = ""
    for s in ordered:
        out.extend(detect_in_segment(s.seq, s.text, previous))
        previous = s.text
    return out
