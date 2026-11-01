"""
The ONE decision about a finding's evidence, used by the analysis (unified_analysis_service).

A finding is judged in one of two ways, never both:
  * Reference format (it carries `word_start` or `word_end`): judged only by the reference. The quote is
    read from the stored transcript; the model's own `quote` is at most an echo that is compared with it
    (see evidence_span_service). A malformed reference is never rescued by the quote.
  * Quote-only format (neither key): the model's quote must be verbatim text of the cited segment
    (case and spacing aside). A copied `n:` position marker is accepted only if the stripped text is
    itself verbatim.

A quote-only entry's rejection reasons are in UNIFIED_REASONS.
"""

from dataclasses import dataclass
from typing import Mapping, Optional

from app.services import evidence_span_service as spans


@dataclass(frozen=True)
class Reasons:
    missing_quote: str
    not_verbatim: str


UNIFIED_REASONS = Reasons("missing quote", "quote is not verbatim text of the cited segment")


@dataclass(frozen=True)
class EvidenceDecision:
    quote: Optional[str] = None  # what is stored as the finding's evidence
    key_tail: tuple = ()  # identifies this evidence for de-duplication, within (rule, segment)
    needs_review: bool = False  # accepted, but held for a reviewer
    reject_reason: Optional[str] = None  # set when the finding is not accepted
    stats: tuple = ()  # counters for diagnostics

    @property
    def accepted(self) -> bool:
        return self.reject_reason is None


def _fold(text: str) -> str:
    return " ".join(text.strip().lower().split())


def _quote_only(entry: dict, segment_text: str, reasons: Reasons) -> EvidenceDecision:
    quote = entry.get("quote")
    if not isinstance(quote, str) or not quote.strip():
        return EvidenceDecision(reject_reason=reasons.missing_quote)
    folded_segment = _fold(segment_text)
    if _fold(quote) in folded_segment:
        return EvidenceDecision(quote=quote.strip(), key_tail=(_fold(quote),), stats=("quote_only_findings",))
    stripped = spans.strip_position_markers(quote)
    if stripped.strip() and _fold(stripped) in folded_segment:
        return EvidenceDecision(quote=stripped.strip(), key_tail=(_fold(quote),), stats=("quote_only_findings",))
    return EvidenceDecision(reject_reason=reasons.not_verbatim)


def decide(entry: dict, segment, text_by_seq: Mapping[int, str], reasons: Reasons) -> EvidenceDecision:
    """`segment` is the cited segment (seq, text); `text_by_seq` maps every segment's seq to its text."""
    if not spans.has_reference_fields(entry):
        return _quote_only(entry, segment.text, reasons)
    try:
        span = spans.resolve_reference(segment.seq, segment.text, entry.get("word_start"), entry.get("word_end"))
    except spans.InvalidReference as exc:
        return EvidenceDecision(reject_reason=str(exc), stats=("reference_invalid",))
    others = {seq: text for seq, text in text_by_seq.items() if seq != segment.seq}
    echo = spans.assess_echo(entry.get("quote"), span, segment.text, others)
    stats = ("reference_findings", f"echo_{echo.kind}")
    if echo.kind == spans.ECHO_CONFLICT:
        where = ", ".join(str(n) for n in echo.other_segments)
        return EvidenceDecision(
            reject_reason=f"the quoted words appear in segment {where}, not in the cited segment {segment.seq}", stats=stats
        )
    return EvidenceDecision(
        quote=span.quote,
        key_tail=(span.char_start, span.char_end),
        needs_review=echo.kind == spans.ECHO_OUTSIDE_SPAN,  # position uncertain: held, not deducted
        stats=stats,
    )
