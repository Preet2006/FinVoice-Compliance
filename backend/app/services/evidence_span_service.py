"""
Evidence by reference: the model points at words, the backend writes the quote.

A model asked to re-type a quote can change its script or spelling (it quoted a
Urdu-script threat in Devanagari, so a genuine finding failed a verbatim check).
So the model is shown each segment's words numbered, returns WHERE the
evidence is, and this module reads the words out of the stored transcript.

THE INDEXING CONVENTION (the prompt and the resolver share it by construction)
-------------------------------------------------------------------------------
* The text indexed is a segment's text EXACTLY as stored: no strip, no case
  folding, no Unicode normalization, no change of script.
* A WORD is a maximal run of non-whitespace characters (`\\S+`; whitespace is
  whatever Python's `str.isspace()` says: spaces, tabs, newlines, NBSP,
  ideographic space, ...). Zero-width joiners and other format characters are
  NOT whitespace, so they stay inside the word they belong to.
* Words are numbered from 0 in reading order. `word_end` is INCLUSIVE.
* Character offsets are Python str indices (code points, not UTF-16 units and
  not bytes). The model never sees or produces them.
* The quote is `text[start_of_word_start : end_of_word_end]`: an exact
  substring, which keeps the original spacing between the words.

`word_spans` is the ONLY tokenizer. `number_words` (what the prompt shows) and
`resolve_reference` (what the backend reads) both call it, so they cannot
disagree about what word 7 is.

Two separate questions, deliberately kept apart:
  1. Is the reference well-formed and inside the segment? -> resolve_reference
     (strict; no type coercion; no guessing).
  2. Does the model's optional echo of the words agree with where it pointed?
     -> assess_echo (a consistency signal; it never decides whether the words
     support the violation - that remains the model's judgement for a manager
     to check).
"""

import re
from dataclasses import dataclass, field
from typing import Mapping, Optional

_WORD = re.compile(r"\S+")
_POSITION_MARKER = re.compile(r"(?<!\S)\d+:(?=\S)")

# How the model's echo relates to the words it pointed at.
ECHO_ABSENT = "absent"
ECHO_MATCHES_SPAN = "matches_span"
ECHO_UNINFORMATIVE = "uninformative"  # under two words and not inside the span: proves nothing
ECHO_OUTSIDE_SPAN = "outside_span"  # found in the cited segment, but not where the reference points
ECHO_NOT_FOUND = "not_found"  # not verbatim anywhere: typically another script or spelling
ECHO_CONFLICT = "conflict"  # found in a DIFFERENT segment than the one cited


class InvalidReference(ValueError):
    """The reference is malformed or outside the segment. The message is the
    reason shown to a manager."""


@dataclass(frozen=True)
class EvidenceSpan:
    segment_seq: int
    word_start: int
    word_end: int
    char_start: int
    char_end: int
    quote: str  # exact substring of the stored segment text


@dataclass(frozen=True)
class EchoAssessment:
    kind: str
    other_segments: tuple = field(default_factory=tuple)  # set for ECHO_CONFLICT


def word_spans(text: str) -> list:
    """[(char_start, char_end), ...] for each word of `text`, in order."""
    return [(m.start(), m.end()) for m in _WORD.finditer(text)]


def number_words(text: str) -> str:
    """The text as the model sees it: `0:first 1:second 2:third`."""
    return " ".join(f"{i}:{text[a:b]}" for i, (a, b) in enumerate(word_spans(text)))


def strip_position_markers(text: str) -> str:
    """Removes `n:` markers a model may copy along with the words. Used only as
    a fallback when a quote fails verification as given, so it can never make
    an invalid quote valid: the stripped text must itself be verbatim."""
    return _POSITION_MARKER.sub("", text)


def has_reference_fields(entry: dict) -> bool:
    """True when the entry uses the reference format at all (either field is
    present, even if null or malformed). Such an entry is judged only by its
    reference and never falls back to the quote."""
    return "word_start" in entry or "word_end" in entry


def _whole_number(value) -> bool:
    return type(value) is int  # excludes bool, float (even 3.0), str, None


def resolve_reference(segment_seq: int, text: str, word_start, word_end) -> EvidenceSpan:
    """Strict. Raises InvalidReference with the reason; never coerces or clamps."""
    if word_start is None or word_end is None:
        raise InvalidReference("both word_start and word_end are required")
    if not (_whole_number(word_start) and _whole_number(word_end)):
        raise InvalidReference("word_start and word_end must be whole numbers")
    if word_start < 0 or word_end < 0:
        raise InvalidReference("word positions cannot be negative")
    if word_start > word_end:
        raise InvalidReference(f"word_start {word_start} is after word_end {word_end}")
    spans = word_spans(text)
    if not spans:
        raise InvalidReference(f"segment {segment_seq} has no words")
    if word_end >= len(spans):
        raise InvalidReference(
            f"word range {word_start}-{word_end} is outside segment {segment_seq}, which has {len(spans)} word(s) (0-{len(spans) - 1})"
        )
    char_start, char_end = spans[word_start][0], spans[word_end][1]
    return EvidenceSpan(segment_seq, word_start, word_end, char_start, char_end, text[char_start:char_end])


def _fold(text: str) -> str:
    # Same folding as the verbatim check used elsewhere: case and whitespace only.
    return " ".join(text.lower().split())


def assess_echo(echo, span: EvidenceSpan, segment_text: str, other_segments: Mapping[int, str]) -> EchoAssessment:
    """Compares the model's optional `quote` with the stored text. A signal about
    the reference, not a judgement on whether the words support the violation."""
    if not isinstance(echo, str) or not echo.strip():
        return EchoAssessment(ECHO_ABSENT)
    folded = _fold(strip_position_markers(echo))
    if not folded:
        return EchoAssessment(ECHO_ABSENT)
    if folded in _fold(span.quote):
        return EchoAssessment(ECHO_MATCHES_SPAN)
    if len(folded.split()) < 2:
        return EchoAssessment(ECHO_UNINFORMATIVE)
    if folded in _fold(segment_text):
        return EchoAssessment(ECHO_OUTSIDE_SPAN)
    elsewhere = tuple(sorted(seq for seq, text in other_segments.items() if folded in _fold(text)))
    if elsewhere:
        return EchoAssessment(ECHO_CONFLICT, elsewhere)
    return EchoAssessment(ECHO_NOT_FOUND)
