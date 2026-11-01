"""
The one place R6 (mandatory opening) is decided.

The analysis reduces the model's answer to three
facts - was the agent's name, the company and the purpose of the call
stated within the agent's first two conversational turns - and hand them to
`evaluate_opening`. The model's own per-rule "violated" verdict for R6 and any
R6 entry in its findings list are never consulted: this structured result is
authoritative, so findings, scoring and the review screen cannot contradict it.

Each component ends in exactly one status:
  stated   - supported; nothing to report.
  missing  - not stated and nothing suggests it was attempted. A definite
             R6 violation that counts toward the score.
  unclear  - only for the agent's NAME: the agent clearly introduces
             themselves ("... bol raha hoon", "this is ...") but speech
             recognition did not produce a usable name. That is a transcription
             limit, not evidence the agent skipped the introduction, so it is
             held for a manager (stored as an unverified finding, which never
             deducts) instead of being reported as a violation.

The backend, not the model, makes this call so the rule is the same every run.
"""

import re
from dataclasses import dataclass, field
from typing import Optional

from app.services.company_name_matcher import company_match

STATED = "stated"
MISSING = "missing"
UNCLEAR = "unclear"

LABELS = {"name": "agent name", "company": "company name", "purpose": "call purpose"}

KIND_OK = "ok"
KIND_VIOLATION = "violation"
KIND_HELD = "held"

R6_CONFIDENCE = 0.9

# Words after "this is" / "I am" that show it is not a self-introduction.
_NOT_A_NAME = (
    r"(?:a|an|the|to|about|regarding|in|not|your|my|it|just|only|for|what|why|how|very|really|"
    r"important|urgent|calling|writing|speaking|sorry|here|going|trying|afraid|sure|glad|happy|aware)"
)

# Generic phrasings of introducing oneself in English, Hinglish and Hindi. This
# detects THAT an introduction was attempted, never what the name is. "bol
# raha hoon" ("I am speaking") is the standard Hindi opening; speech
# recognition also renders its final consonant as र for ल, so both are accepted.
_SELF_INTRODUCTION_CUES = [
    re.compile(rf"\bthis is (?!{_NOT_A_NAME}\b)[^\W\d_]+", re.IGNORECASE),
    re.compile(r"\bmy name is\b", re.IGNORECASE),
    re.compile(r"\bmyself\b", re.IGNORECASE),
    re.compile(rf"\bi(?:'m| am) (?!{_NOT_A_NAME}\b)[^\W\d_]+(?:\s+[^\W\d_]+)?\s+(?:from|calling|speaking|here)\b", re.IGNORECASE),
    re.compile(r"\bmera naam\b", re.IGNORECASE),
    re.compile(r"\bbol\s*(?:raha|rahi|rha|rhi)\s*(?:hoon|hun|hu)\b", re.IGNORECASE),
    re.compile(r"\bspeaking from\b", re.IGNORECASE),
    re.compile("मेरा नाम"),
    re.compile(r"बो[लर]\s*(?:रहा|रही)\s*(?:हूं|हूँ|हु)"),
    # Common Hindi ASR substitutions can change "bol" to "dol" and join the
    # consonant cluster to the following word.  This still requires the
    # surrounding self-introduction construction; it does not infer a name.
    re.compile(r"(?:बोल|डोल)\s*्?\s*(?:रहाँ|रहां|रहाँ)"),
    re.compile(r"(?:बोल|डोल)\s*्?\s*(?:रहा|रही|रह)\s*(?:हूं|हूँ|हु|हू)"),
    re.compile("میرا نام"),
    re.compile(r"بول\s*(?:رہا|رہی)\s*ہوں"),  # Urdu script: "bol raha/rahi hoon"
]


def has_self_introduction_cue(text: str) -> bool:
    """True when `text` contains a phrase by which a speaker introduces
    themselves. Says nothing about whether a usable name was transcribed."""
    return bool(text) and any(p.search(text) for p in _SELF_INTRODUCTION_CUES)


_NAME_CUE_MAX_GAP = 25  # characters between the cited name and the phrase that introduces it
_MIN_NAME_TOKEN = 3  # a name has at least one word of this many characters


def _fold(text: str) -> str:
    return " ".join(text.lower().split())


def name_evidence_credible(scope_text: str, name_quote: str) -> bool:
    """Whether the words cited as the agent's NAME can be trusted as one.

    Speech recognition garbles names, and a model shown garbled text can still
    "find" a name in it - even the borrower's name, which the agent says when
    asking who they are speaking to. So a claimed name is credited only when
    (a) at least one cited word is name-sized and (b) the cited words sit right
    next to the phrase by which the agent introduces themselves ("... bol raha
    hoon", "this is ...", "my name is ..."). Otherwise it is not credited as
    established: it is held for a manager, never silently accepted and never
    turned into a deduction."""
    quote = _fold(name_quote or "")
    if not quote or not any(len(token) >= _MIN_NAME_TOKEN for token in quote.split()):
        return False
    text = _fold(scope_text)
    at = text.find(quote)
    if at < 0:
        return False
    q_start, q_end = at, at + len(quote)
    for cue in _SELF_INTRODUCTION_CUES:
        for m in cue.finditer(text):
            gap = max(m.start() - q_end, q_start - m.end(), 0)
            if gap <= _NAME_CUE_MAX_GAP:
                return True
    return False


# The purpose of the call: the agent says why they are calling ("I am calling about / because / regarding ...", "...
# ke baare mein call kiya hai") AND names the debt matter, in the same segment. Both are required, so "calling from
# Shaha Finlease" or "your payment is pending" alone is not a purpose. It only CONFIRMS a purpose the model did not
# credit; it never removes one the model found.
_PURPOSE_INTENT = re.compile(
    r"\b(?:calling|called|call)\b.{0,60}?\b(?:about|regarding|because|for|concerning)\b"
    r"|\b(?:regarding|concerning)\b|\bbaare\s+mein\b|\bbare\s+mein\b|\bke\s+liye(?:\s+call)?\b|\bke\s+sambandh\b"
    r"|\b(?:usi|uske|is)(?:\s+ke)?\s+baare\s+mein\b|\bke\s+baare\s+mein\b|\bbaat\s+(?:karni|karne|karna)\b"
    r"|के\s+बारे\s+में|(?:उसी|उसके|इस)(?:\s+के)?\s+बारे\s+में|के\s+सिलसिले|के\s+लिए(?:\s+कॉल)?|बात\s+(?:करनी|करने|करना)\b|بارے\s+میں",
    re.IGNORECASE,
)
_PURPOSE_TOPIC = re.compile(
    r"\b(?:loan|payment|outstanding|overdue|pending|dues?|emi|credit\s*card|account|balance|installments?|baaki|baki|bakaya|rupees?|amount)\b"
    r"|लोन|पेमेंट|बकाया|बाकी|क्रेडिट|कार्ड|कर्ज|रुपये|रुपए|لون|پیمنٹ|باقی|کرڈٹ|کریڈٹ|بقایا|روپ",
    re.IGNORECASE,
)


def purpose_mentioned(text: str) -> bool:
    """True when one bounded opening segment contains intent and a debt topic."""
    return bool(text) and bool(_PURPOSE_INTENT.search(text)) and bool(_PURPOSE_TOPIC.search(text))


def purpose_mentioned_across(texts) -> bool:
    """True when the bounded opening scope supplies intent and a debt topic.

    ASR commonly splits one opening turn into adjacent segments.  Combining
    only the already-selected first two agent turns keeps this a local,
    conservative support check instead of searching the whole call.
    """
    parts = [text for text in texts if text]
    return any(purpose_mentioned(text) for text in parts) or purpose_mentioned(" ".join(parts))


@dataclass
class OpeningOutcome:
    kind: str  # ok | violation | held
    statuses: dict  # component -> stated | missing | unclear
    missing: list = field(default_factory=list)  # labels of missing components
    unclear: list = field(default_factory=list)  # labels of unclear components
    anchor_seq: Optional[int] = None  # segment the finding is attached to
    quote: str = ""
    explanation: str = ""
    needs_review: bool = False
    confidence: float = R6_CONFIDENCE
    diagnostics: dict = field(default_factory=dict)
    held_reason: str = "the agent's name could not be established from the transcript"


def unreliable_opening(labelled: list, scope_segments: list, is_uncertain) -> Optional[tuple]:
    """(reason, anchor_seq) when the opening cannot be judged reliably, else None. Used by BOTH pipelines.

    Two structural reasons, neither a guess about turn lengths:
    * the call's first segment is not labelled as the agent. Outbound collection calls start with the
      agent, and speech recognition can merge both speakers' first turns into one segment, so the real
      opening may sit inside a segment labelled as the borrower.
    * a segment the opening is judged from has an uncertain speaker label (`is_uncertain(confidence)`)."""
    ordered = sorted(labelled, key=lambda s: s.seq)
    if ordered and ordered[0].speaker != "agent":
        return (
            f"the call's first segment (#{ordered[0].seq}) is labelled as the borrower, so the agent's opening may be inside it "
            "(speech recognition can merge both speakers' first turns into one segment).",
            ordered[0].seq,
        )
    shaky = sorted(s.seq for s in scope_segments if is_uncertain(s.confidence))
    if shaky:
        return (
            "the speaker label is uncertain for segment(s) " + ", ".join(f"#{n}" for n in shaky) + " of the agent's opening.",
            min(s.seq for s in scope_segments),
        )
    return None


def evaluate_opening(
    scope_segments: list,
    *,
    name_stated: bool,
    company_stated: bool,
    purpose_stated: bool,
    evidence_quote: Optional[str] = None,
    unsupported: tuple = (),
    model_uncertain: bool = False,
    name_quote: Optional[str] = None,
    unreliable: Optional[tuple] = None,
) -> OpeningOutcome:
    """
    `name_quote`, when given, is the text cited for the agent's name; a claimed
    name whose citation is not credible (see name_evidence_credible) is held,
    not credited. `unreliable` is (reason, anchor_seq) when the opening itself
    cannot be trusted - e.g. the call's first segment is not labelled as the
    agent, so a merged first turn may hide the real opening: the result is then
    always held for a manager and never a deduction, nor a clean pass.

    `scope_segments` are the segments of the agent's first two conversational
    turns (see turns.first_agent_turn_segments), in transcript order. The
    three `*_stated` flags mean "the model said so AND, where the pipeline can
    check, its evidence is in those segments". `unsupported` names components
    the model claimed but could not support.
    """
    scope_text = " ".join(s.text for s in scope_segments)
    diagnostics: dict = {}

    company_how = None if company_stated else company_match(scope_text, introduced=has_self_introduction_cue(scope_text))
    if company_how:
        diagnostics["company_recognised_by"] = "matcher"
        diagnostics["company_match"] = company_how
    company = STATED if (company_stated or company_how) else MISSING
    purpose_by_matcher = not purpose_stated and purpose_mentioned_across(seg.text for seg in scope_segments)
    if purpose_by_matcher:
        diagnostics["purpose_recognised_by"] = "matcher"
    purpose = STATED if (purpose_stated or purpose_by_matcher) else MISSING
    if name_stated and name_quote is not None and not name_evidence_credible(scope_text, name_quote):
        name = UNCLEAR
        diagnostics["name_not_credited"] = "cited words are not a credible agent name"
    elif name_stated:
        name = STATED
    elif has_self_introduction_cue(scope_text):
        name = UNCLEAR
    else:
        name = MISSING

    statuses = {"name": name, "company": company, "purpose": purpose}
    missing = [LABELS[c] for c, st in statuses.items() if st == MISSING]
    unclear = [LABELS[c] for c, st in statuses.items() if st == UNCLEAR]
    diagnostics["components"] = dict(statuses)

    if unreliable is not None:
        reason, anchor_seq = unreliable
        diagnostics["unreliable_opening"] = reason
        anchor = next((s for s in scope_segments if s.seq == anchor_seq), scope_segments[0])
        return OpeningOutcome(
            KIND_HELD, statuses, [], list(LABELS.values()), anchor_seq,
            (evidence_quote or anchor.text).strip() or anchor.text,
            "Mandatory opening could not be evaluated reliably: " + reason + " Check the audio before treating this as a violation or a pass.",
            diagnostics=diagnostics, held_reason="the opening could not be evaluated reliably",
        )

    if not missing and not unclear:
        return OpeningOutcome(KIND_OK, statuses, diagnostics=diagnostics)

    anchor = scope_segments[0]
    quote = (evidence_quote or "").strip()
    if not quote or " ".join(quote.lower().split()) not in " ".join(scope_text.lower().split()):
        quote = anchor.text

    if missing:
        explanation = "Mandatory opening missing required element(s): " + ", ".join(missing) + "."
        if unclear:
            explanation += " The " + ", ".join(unclear) + " could not be established from the transcript; check the audio."
        note = ""
        if unsupported:
            note = " (the model claimed an element was stated but its evidence is not in the agent's first two turns)"
        return OpeningOutcome(
            KIND_VIOLATION, statuses, missing, unclear, anchor.seq, quote, explanation + note,
            needs_review=model_uncertain or bool(unsupported), diagnostics=diagnostics,
        )

    explanation = (
        "Mandatory opening: the agent appears to introduce themselves, but the "
        + ", ".join(unclear)
        + " could not be established from the transcript (the speech recognition output is unclear). "
        "Company and purpose were stated. Check the audio before treating this as a violation."
    )
    return OpeningOutcome(
        KIND_HELD, statuses, missing, unclear, anchor.seq, quote, explanation, diagnostics=diagnostics,
    )
