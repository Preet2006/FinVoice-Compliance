"""
The compliance analysis, and the ONLY analysis path: ONE whole-conversation LLM call (Groq
openai/gpt-oss-120b) labels the speakers and returns the findings, the R6 opening components and the
sentiment together.

The model returns structured findings; this module VALIDATES them against the
original transcript and turns them into LabelledSegment / ComplianceResult
objects, which persistence, scoring, incident grouping, the review workflow,
the API and the UI consume.

Division of responsibility:
  LLM      - speaker attribution (with uncertainty), R2-R5 findings, the three
             R6 opening components with evidence, sentiment.
  Backend  - R1 (deterministic, from call start_time; the model is told not to
             report it), severity (always the rule default), timestamps (taken
             from the transcript segment, never from the model), quote
             verification, the definition of an agent conversational turn,
             the R6 decision, scoring and incident grouping.

Original transcript text is never modified; any normalized interpretation of
the company name stays in the model's explanation/evidence only.
"""

import json
import re
import time
from typing import Optional

from app.rules import RULES
from app.services import llm_service
from app.services import abusive_language, evidence_span_service as spans, finding_evidence, opening_service, turns
from app.services.llm_service import (
    ComplianceResult,
    LabelledSegment,
    LLMResponseValidationError,
    RawViolation,
)

COMPANY_NAME = "Shaha Finlease"
MODEL_SPEAKERS = llm_service.SPEAKER_STATES
# R1 is deterministic and R6 is decided from opening_check, so the model only
# reports findings for these rules in its `violations` list.
FINDING_RULES = {"R2", "R3", "R4", "R5"}
UNCERTAIN_CONFIDENCE_CAP = 0.5  # stored cap for segments the model flags as uncertain/mixed
R6_CONFIDENCE = opening_service.R6_CONFIDENCE

_SYSTEM_PROMPT = """You are a compliance analyst for {company}, an RBI-regulated debt-collection company in India. Analyse ONE complete call transcript as a whole conversation and report compliance findings objectively. Decide only from what the transcript supports: do not assume a violation, or a clean call, because the call resembles something familiar.

INPUT
Call metadata, then the transcript as numbered speech-to-text segments: `[index] (start-end s) 0:word 1:word 2:word ...`. Each word is shown with its position number and a colon (`n:`, counting from 0 in each segment); the number is only a position marker and is not part of the text. The transcript is automatic ASR output: words may be wrong, missing or merged, a segment may contain more than one speaker, and Hindi, English and Hinglish may appear in Devanagari or Latin script. No speaker labels are supplied.

SPEAKERS
Assign every segment a speaker - agent (the company's collector), borrower (the person the debt belongs to), third_party (anyone else), unknown (no reliable role assignment), or mixed (multiple speakers in one segment) - from the whole conversation: turn-taking, question-and-answer structure, who is addressed, who introduces themselves or the company, who requests or promises payment. Do not decide from a segment's wording or grammatical form alone. When the evidence is insufficient (very short fragments, a segment that mixes speakers, unclear roles), use `unknown` or `mixed` as appropriate, set `uncertain` or `mixed_speakers`, and give a low confidence instead of a confident guess. Your confidence is a self-estimate, not proof of correctness.

LABELLING RUNS
The recogniser often splits one person's turn into many short segments and merges lines of both speakers. Label EACH segment by what it says and who it addresses, never by the previous segment's label. A collector demands or chases payment, sets deadlines, threatens, scolds or insults, and introduces the company; a borrower asks for time, explains hardship, objects or complains. If four or more consecutive segments would all get one label, re-check each of them individually, and set `uncertain` true for any whose content could belong to either speaker.

COMPANY
The company is "{company}". ASR may respell, split, merge or transliterate a name in any script. Treat a phrase as the company only when the context shows the agent is naming their own employer (typically while introducing themselves) and the phrase is a plausible phonetic rendering of the name. Do not accept an unrelated organisation or a vaguely similar word. When quoting, copy the transcript exactly; never correct its spelling.

CONVERSATIONAL TURNS
A turn is an uninterrupted stretch of one speaker: consecutive segments by the same speaker are ONE turn, and a new agent turn begins only after another speaker has spoken.

RULES
{rules}
- R1 is evaluated by the backend from the call start time. Never report R1.
- Only the AGENT can breach R2-R6. Words spoken by the borrower or a third party - including repeating, quoting, questioning or refusing something - are not violations.
- R2: clearly abusive, insulting or foul language in any language or script (Hindi in Devanagari or Urdu script, Roman Hindi, English), including insults aimed at the borrower such as calling them a fool or good-for-nothing (bewakoof, nikamma, idiot) or rhetorical forms such as asking whether they are a fool or have a head full of straw ("dimaag mein bhoosa"). Neutral, quoted, negated or hypothetical uses are not abuse. R3: threats, intimidation or coercive conduct. R4: disclosing the borrower's debt or personal information to anyone other than the borrower, or shaming through such disclosure. R5: asking the borrower to provide, tell or read out an OTP, PIN, CVV, password or similar authentication credential.
- R3 versus R4: classify by the nature and target of the conduct. Disclosure-only coercion (threatening to tell relatives, workplace contacts, an office or the public about a debt) is R4, not automatically R3. R3 requires separate physical, legal, police/jail or independently supported intimidation evidence. intimidation alone does not establish R4. Where both genuinely apply, report each with its own evidence. Where the classification is genuinely ambiguous, report the best-fitting rule, name the alternative in the explanation and set `uncertain` true.
- R6 is judged only through `opening_check` (never put R6 in `violations`): within the agent's first TWO TURNS, did the agent give (a) their own name, (b) the company name, (c) the purpose of the call - why they are calling, such as an outstanding payment or loan, not merely whom they wish to speak to? Judge each separately and quote its evidence. A name counts as given when the agent clearly introduces themselves, even if ASR garbled the name itself.

EVIDENCE
- Cite each violation's evidence BY POSITION, not by re-typing it: give the `segment_index` and the `word_start` and `word_end` positions (both inclusive, the numbers shown before the words) of the words that show the breach, within exactly one segment. The system reads the words from the transcript itself. `quote` is optional; if you give it, copy the words exactly as written in the transcript, in the same script, without the position markers. Never invent, paraphrase, merge or complete text, speakers or timestamps.
- Report each distinct occurrence once, with its own evidence. Do not repeat the same evidence, and do not merge separate incidents just because they share a rule.
- Report only what the words clearly support. If the words are unclear, malformed or uncertain, do not call them abusive or threatening; set `uncertain` true so a human can review.
- Benign, hypothetical, quoted or misheard statements are not violations.
- Any quote you write (a finding's optional `quote`, or an `evidence_quote` for the opening) must contain only the words, never the `n:` position markers.

OUTPUT: respond with ONLY one JSON object, exactly this shape:
{{"segments": [{{"index": <int>, "speaker": "agent"|"borrower"|"third_party"|"unknown"|"mixed", "confidence": <0.0-1.0>, "uncertain": <bool>, "mixed_speakers": <bool>}}, ... one entry for EVERY segment index],
 "violations": [{{"rule_code": "R2"|"R3"|"R4"|"R5", "segment_index": <int>, "word_start": <int>, "word_end": <int>, "quote": "<optional: the same words, copied exactly>", "speaker": "agent"|"borrower"|"third_party"|"unknown"|"mixed", "explanation": "<one sentence>", "confidence": <0.0-1.0>, "uncertain": <bool>}}],
 "rule_outcomes": {{"R2": {{"violated": <bool>, "uncertain": <bool>, "note": "<short>"}}, "R3": {{...}}, "R4": {{...}}, "R5": {{...}}, "R6": {{...}}}},
 "opening_check": {{"agent_turns_considered": [[<segment indices of agent turn 1>], [<segment indices of agent turn 2>]],
   "name": {{"stated": <bool>, "segment_index": <int|null>, "evidence_quote": "<verbatim|null>"}},
   "company": {{"stated": <bool>, "segment_index": <int|null>, "evidence_quote": "<verbatim|null>"}},
   "purpose": {{"stated": <bool>, "segment_index": <int|null>, "evidence_quote": "<verbatim|null>"}},
   "uncertain": <bool>}},
 "sentiment": {{"borrower_overall": "positive"|"neutral"|"negative", "agent_stayed_calm": <bool>}},
 "review_recommended": <bool>, "review_reasons": ["<short reason>", ...]}}
Use "third_party" for any person other than the agent and the borrower. `violations` may be empty."""


def _rules_text() -> str:
    return "\n".join(
        f"{r['code']} [{r['default_severity']}] {r['name']}: {r['description']}"
        for r in RULES
    )


def build_messages(raw_segments: list, call) -> list:
    system = _SYSTEM_PROMPT.format(company=COMPANY_NAME, rules=_rules_text())
    transcript = "\n".join(
        f"[{s.seq}] ({s.start:.1f}-{s.end:.1f}s) {spans.number_words(s.text)}" for s in raw_segments
    )
    start = getattr(call, "start_time", None)
    meta = (
        f"CALL METADATA: start_time={start.isoformat() if start else 'unknown'} (IST), "
        f"account_type={getattr(call, 'account_type', 'unknown')}, "
        f"language={getattr(call, 'language', None) or 'unknown'}\n\nTRANSCRIPT:\n"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": meta + transcript}]


# --- turn logic (backend-owned; the prompt describes the same definition) ---

# One definition of a turn (see turns.py).
conversational_turns = turns.conversational_turns
first_agent_turns = turns.first_agent_turns


# --- validation ---------------------------------------------------------------

def _flag(value) -> bool:
    return value is True


def _parse_segments(data: dict, raw_segments: list) -> list:
    entries = data.get("segments")
    if not isinstance(entries, list):
        raise LLMResponseValidationError("Unified analysis response is missing the 'segments' list.")
    by_index: dict = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        idx, speaker = entry.get("index"), entry.get("speaker")
        if isinstance(idx, int) and not isinstance(idx, bool) and speaker in MODEL_SPEAKERS:
            by_index[idx] = entry
    expected = {s.seq for s in raw_segments}
    missing = sorted(expected - by_index.keys())
    if missing:
        raise LLMResponseValidationError(
            f"Unified analysis response is missing a valid speaker for segment index(es): {missing}"
        )

    labelled = []
    for s in raw_segments:
        e = by_index[s.seq]
        confidence = llm_service._parse_speaker_confidence(e.get("confidence"))
        state = "mixed" if _flag(e.get("mixed_speakers")) else e["speaker"]
        state_requires_review = state in {"unknown", "mixed"}
        if confidence is not None and (state_requires_review or _flag(e.get("uncertain"))):
            confidence = min(confidence, UNCERTAIN_CONFIDENCE_CAP)
        labelled.append(
            LabelledSegment(
                seq=s.seq,
                speaker=state,
                start=s.start,
                end=s.end,
                text=s.text,
                confidence=confidence,
                speaker_uncertain=state_requires_review or _flag(e.get("uncertain")) or llm_service.is_uncertain_confidence(confidence),
            )
        )
    return labelled


def _record(stats: Optional[dict], key: str) -> None:
    if stats is not None:
        stats[key] = stats.get(key, 0) + 1


def _validate_findings(data: dict, labelled: list, declared_speakers: dict, stats: Optional[dict] = None):
    entries = data.get("violations")
    if not isinstance(entries, list):
        raise LLMResponseValidationError("Unified analysis response is missing the 'violations' list.")
    by_seq = {s.seq: s for s in labelled}
    accepted, rejected, seen = [], [], set()
    for entry in entries:
        if not isinstance(entry, dict):
            rejected.append(llm_service.rejection(entry, "not an object", llm_service.KIND_POLICY))
            continue
        rule, idx = entry.get("rule_code"), entry.get("segment_index")
        if rule not in FINDING_RULES:
            reason = (
                "R6 is evaluated only via opening_check" if rule == "R6"
                else "R1 is evaluated by the backend" if rule == "R1"
                else "unknown rule code"
            )
            rejected.append(llm_service.rejection(entry, reason, llm_service.KIND_POLICY))
            continue
        # type() check: 0.0 and True would otherwise hash-match segments 0 and 1.
        seg = by_seq.get(idx) if type(idx) is int else None
        if seg is None:
            rejected.append(llm_service.rejection(entry, "segment_index does not exist", llm_service.KIND_INVALID_EVIDENCE))
            continue

        decision = finding_evidence.decide(entry, seg, {k: v.text for k, v in by_seq.items()}, finding_evidence.UNIFIED_REASONS)
        for stat in decision.stats:
            _record(stats, stat)
        if not decision.accepted:
            rejected.append(llm_service.rejection(entry, decision.reject_reason, llm_service.KIND_INVALID_EVIDENCE))
            continue
        quote, needs_review, key = decision.quote, decision.needs_review, (rule, idx) + decision.key_tail

        if key in seen:
            rejected.append(
                llm_service.rejection(entry, "duplicate of an earlier finding with identical evidence", llm_service.KIND_DUPLICATE)
            )
            continue
        seen.add(key)
        confidence = entry.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0.0 <= float(confidence) <= 1.0:
            confidence = 0.5
        claimed = entry.get("speaker")
        stored_agent = seg.speaker == "agent"
        speaker_mismatch = claimed in MODEL_SPEAKERS and (claimed == "agent") != stored_agent
        accepted.append(
            RawViolation(
                rule_code=rule,
                segment_seq=idx,
                quote=quote,
                explanation=str(entry.get("explanation") or "")[:500],
                confidence=float(confidence),
                needs_review=_flag(entry.get("uncertain")) or speaker_mismatch or needs_review,
            )
        )
    return accepted, rejected


def _component_supported(component, scope: dict) -> tuple:
    """Returns (stated_and_supported, claimed_but_unsupported, evidence_quote)."""
    if not isinstance(component, dict) or component.get("stated") is not True:
        return False, False, None
    idx, quote = component.get("segment_index"), component.get("evidence_quote")
    seg = scope.get(idx) if type(idx) is int else None
    if seg is not None and isinstance(quote, str) and quote.strip():
        folded_segment = llm_service._normalize(seg.text)
        if llm_service._normalize(quote) in folded_segment:
            return True, False, quote.strip()
        # Numbered words in the transcript may be copied with their `n:` markers;
        # accepted only if the stripped text is itself verbatim.
        stripped = spans.strip_position_markers(quote)
        if stripped.strip() and llm_service._normalize(stripped) in folded_segment:
            return True, False, stripped.strip()
    return False, True, None


def _unreliable_opening(labelled: list, scope: dict):
    """(reason, anchor_seq) when the opening cannot be judged reliably, else None."""
    return opening_service.unreliable_opening(labelled, list(scope.values()), llm_service.is_uncertain_confidence)


def _evaluate_opening(data: dict, labelled: list, diagnostics: dict):
    """
    R6 from the structured opening_check only. Returns (violation, held):
    a definite RawViolation, or a rejection entry that stores an unclear
    (garbled) agent name as an unverified, non-deducting finding, or neither.
    The model's own rule_outcomes["R6"] and any R6 in its findings list are
    never consulted.
    """
    agent_turns = first_agent_turns(labelled)
    if not agent_turns:
        diagnostics["r6"] = {"decided_by": "opening_check", "note": "no agent segments; R6 not evaluated"}
        return None, None
    scope = {s.seq: s for turn in agent_turns for s in turn}

    check = data.get("opening_check")
    if not isinstance(check, dict):
        raise LLMResponseValidationError("Unified analysis response is missing 'opening_check'.")

    declared = check.get("agent_turns_considered")
    backend_turns = [[s.seq for s in turn] for turn in agent_turns]
    if declared != backend_turns:
        diagnostics["turn_definition_mismatch"] = {"model": declared, "backend": backend_turns}

    name_ok, name_bad, name_q = _component_supported(check.get("name"), scope)
    company_ok, company_bad, company_q = _component_supported(check.get("company"), scope)
    purpose_ok, purpose_bad, purpose_q = _component_supported(check.get("purpose"), scope)

    unreliable = _unreliable_opening(labelled, scope)
    outcome = opening_service.evaluate_opening(
        [seg for turn in agent_turns for seg in turn],
        name_stated=name_ok,
        company_stated=company_ok,
        purpose_stated=purpose_ok,
        evidence_quote=next((q for q in (name_q, company_q, purpose_q) if q), None),
        unsupported=tuple(
            label for label, bad in (("name", name_bad), ("company", company_bad), ("purpose", purpose_bad)) if bad
        ),
        model_uncertain=_flag(check.get("uncertain")),
        name_quote=name_q,
        unreliable=unreliable,
    )
    diagnostics["r6"] = {"decided_by": "opening_check", **outcome.diagnostics}
    if "company_recognised_by" in outcome.diagnostics:
        diagnostics["company_recognised_by"] = outcome.diagnostics["company_recognised_by"]
    model_r6 = (data.get("rule_outcomes") or {}).get("R6") if isinstance(data.get("rule_outcomes"), dict) else None
    if isinstance(model_r6, dict) and isinstance(model_r6.get("violated"), bool):
        backend_violated = outcome.kind == opening_service.KIND_VIOLATION
        if model_r6["violated"] != backend_violated:
            diagnostics["r6"]["model_outcome_overridden"] = {"model_said_violated": model_r6["violated"], "backend_decided": outcome.kind}

    if outcome.kind == opening_service.KIND_VIOLATION:
        return (
            RawViolation(
                rule_code="R6",
                segment_seq=outcome.anchor_seq,
                quote=outcome.quote,
                explanation=outcome.explanation,
                confidence=outcome.confidence,
                needs_review=outcome.needs_review,
            ),
            None,
        )
    if outcome.kind == opening_service.KIND_HELD:
        return None, llm_service.rejection(
            {"rule_code": "R6", "segment_index": outcome.anchor_seq, "quote": None, "explanation": outcome.explanation},
            outcome.held_reason,
            llm_service.KIND_INVALID_EVIDENCE,
        )
    return None, None


def _asserted_without_evidence(data: dict, accepted: list, rejected: list) -> list:
    """Rules the model reported as violated for which nothing verifiable
    survived validation. They are kept as unverified findings with no
    segment: the evidence is not invented and is not attached to an
    unrelated segment."""
    outcomes = data.get("rule_outcomes")
    if not isinstance(outcomes, dict):
        return []
    have = {v.rule_code for v in accepted}
    have |= {
        r["entry"].get("rule_code")
        for r in rejected
        if r["kind"] == llm_service.KIND_INVALID_EVIDENCE and isinstance(r["entry"], dict)
    }
    extra = []
    for code in sorted(FINDING_RULES):
        outcome = outcomes.get(code)
        if isinstance(outcome, dict) and outcome.get("violated") is True and code not in have:
            entry = {"rule_code": code, "segment_index": None, "quote": None, "explanation": str(outcome.get("note") or "")[:500]}
            extra.append(
                llm_service.rejection(
                    entry, "the analysis reported this rule as violated but returned no verifiable evidence",
                    llm_service.KIND_INVALID_EVIDENCE,
                )
            )
    return extra


SUSPECT_RUN = 4  # consecutive same-label segments, with the other speaker silent, that are implausible in a two-party call


def _suspect_borrower_runs(labelled: list) -> dict:
    """seq -> run length for segments labelled borrower inside a run of SUSPECT_RUN or more consecutive borrower
    segments. A collector's line can hide in such a run when the speaker labels are wrong."""
    out: dict = {}
    run: list = []
    for seg in sorted(labelled, key=lambda s: s.seq) + [None]:
        if seg is not None and seg.speaker == "borrower":
            run.append(seg.seq)
            continue
        if len(run) >= SUSPECT_RUN:
            out.update({seq: len(run) for seq in run})
        run = []
    return out


def _add_abusive_language_support(labelled: list, accepted: list, rejected: list, diagnostics: dict) -> None:
    """R2 supporting evidence (abusive_language.py). The model decides R2; this adds a HELD finding for a clear
    hit the model did not report, so a manager can confirm it from the audio. A held finding never deducts and is
    never presented as a confirmed breach. Hits in segments the model already reported on are not repeated, and a
    hit labelled borrower counts only inside an implausibly long borrower run (the label may be wrong)."""
    hits = abusive_language.detect(labelled)
    if not hits:
        return
    reported = {v.segment_seq for v in accepted if v.rule_code == "R2"}
    reported |= {
        r["entry"].get("segment_index") for r in rejected
        if isinstance(r["entry"], dict) and r["entry"].get("rule_code") == "R2"
    }
    by_seq = {seg.seq: seg for seg in labelled}
    runs = _suspect_borrower_runs(labelled)
    added = ignored = covered = 0
    for hit in hits:
        if hit.segment_seq in reported:
            covered += 1
            continue
        seg = by_seq[hit.segment_seq]
        if seg.speaker == "agent":
            why = "the analysis did not report it"
        elif hit.segment_seq in runs:
            why = (
                f"the segment is labelled borrower but sits in a run of {runs[hit.segment_seq]} consecutive borrower "
                "segments with no agent turn between them, so the speaker label may be wrong"
            )
        else:
            ignored += 1
            continue
        accepted.append(
            RawViolation(
                rule_code="R2",
                segment_seq=hit.segment_seq,
                quote=hit.quote,
                explanation=f"Possible abusive language: {hit.note}. Found by the backend's abusive-language check; {why}. Confirm from the audio who said it.",
                confidence=0.6,
                needs_review=True,
            )
        )
        added += 1
    diagnostics["abusive_language"] = {"hits": len(hits), "added_held": added, "already_reported": covered, "ignored_borrower_segments": ignored}


_DISCLOSURE_TOPIC = re.compile(
    r"\b(?:loan|payment|outstanding|overdue|pending|dues?|emi|credit\s*card|account|balance|installments?|"
    r"baaki|baki|bakaya|rupees?|amount)\b"
    r"|लोन|पेमेंट|बकाया|बाकी|क्रेडिट|कार्ड|कर्ज|रुपये|रुपए|ईएमआई|खाता"
    r"|لون|پیمنٹ|باقی|بقایا|قسط|رقم|اکاؤنٹ",
    re.IGNORECASE,
)


def _has_debt_disclosure_topic(text: str) -> bool:
    """Conservative language support for debt/account information."""
    return bool(text) and bool(_DISCLOSURE_TOPIC.search(text))


def _third_party_disclosure_segments(labelled: list) -> list:
    """Return agent segments immediately following an explicit third-party turn.

    The bounded role context avoids treating ordinary agent/borrower calls as
    disclosures. A borrower turn ends the recipient context; unknown/mixed
    roles do not create a disclosure inference.
    """
    candidates = []
    last_third_party = None
    for seg in sorted(labelled, key=lambda s: s.seq):
        if seg.speaker == "third_party":
            last_third_party = seg.seq
            continue
        if seg.speaker == "borrower":
            last_third_party = None
            continue
        if seg.speaker in {"unknown", "mixed"}:
            continue
        if seg.speaker == "agent" and last_third_party is not None and seg.seq - last_third_party <= 3:
            if _has_debt_disclosure_topic(seg.text):
                candidates.append(seg)
    return candidates


def _add_third_party_disclosure_support(labelled: list, accepted: list, diagnostics: dict) -> None:
    """Add held R4 support when explicit role context surrounds debt disclosure.

    This is supporting evidence only. It never decides a speaker role, never
    confirms a finding, and remains subject to the normal reviewer workflow.
    """
    candidates = _third_party_disclosure_segments(labelled)
    if not candidates:
        return
    reported = {v.segment_seq for v in accepted if v.rule_code == "R4"}
    added = 0
    for seg in candidates:
        if seg.seq in reported:
            continue
        accepted.append(
            RawViolation(
                rule_code="R4",
                segment_seq=seg.seq,
                quote=seg.text.strip(),
                explanation=(
                    "Possible third-party debt disclosure: the agent discussed debt or account information "
                    "after a third party spoke. Confirm the recipient and speaker from the audio."
                ),
                confidence=0.6,
                needs_review=True,
            )
        )
        added += 1
    diagnostics["third_party_disclosure"] = {"candidates": len(candidates), "added_held": added}


def _consistency(data: dict, accepted: list, diagnostics: dict) -> None:
    """Compares the model's per-rule verdicts with what the backend validated.
    R6 is excluded: it is decided only from opening_check (see
    _evaluate_opening), so the model's R6 verdict cannot contradict it."""
    outcomes = data.get("rule_outcomes")
    if not isinstance(outcomes, dict):
        diagnostics["rule_outcomes"] = "missing"
        return
    found = {v.rule_code for v in accepted}
    problems = {}
    for code in ("R2", "R3", "R4", "R5"):
        outcome = outcomes.get(code)
        if not isinstance(outcome, dict) or not isinstance(outcome.get("violated"), bool):
            problems[code] = "outcome missing or malformed"
        elif outcome["violated"] != (code in found):
            problems[code] = f"model said violated={outcome['violated']} but backend validated={code in found}"
    if problems:
        diagnostics["rule_outcome_inconsistencies"] = problems
    if data.get("review_recommended") is True:
        diagnostics["review_recommended"] = [str(r)[:160] for r in (data.get("review_reasons") or [])][:5]


def parse_and_validate(data: dict, raw_segments: list):
    """Raises LLMResponseValidationError on a structurally unusable response;
    unsupported individual findings are rejected into rejected_findings."""
    if not isinstance(data, dict):
        raise LLMResponseValidationError("Unified analysis response is not a JSON object.")
    sentiment = data.get("sentiment")
    if not isinstance(sentiment, dict) or sentiment.get("borrower_overall") not in ("positive", "neutral", "negative") or not isinstance(sentiment.get("agent_stayed_calm"), bool):
        raise LLMResponseValidationError("Unified analysis response has an invalid 'sentiment' object.")

    labelled = _parse_segments(data, raw_segments)
    evidence_stats: dict = {}
    accepted, rejected = _validate_findings(data, labelled, {}, evidence_stats)
    rejected += _asserted_without_evidence(data, accepted, rejected)
    diagnostics: dict = {}
    if evidence_stats:
        diagnostics["evidence"] = evidence_stats  # reference/echo counts, for validating the mechanism
    opening, held = _evaluate_opening(data, labelled, diagnostics)
    if opening is not None:
        accepted.append(opening)
    if held is not None:
        rejected.append(held)
    _consistency(data, [v for v in accepted if v.rule_code != "R6"], diagnostics)
    _add_abusive_language_support(labelled, accepted, rejected, diagnostics)
    _add_third_party_disclosure_support(labelled, accepted, diagnostics)

    return labelled, ComplianceResult(
        violations=accepted,
        borrower_sentiment=sentiment["borrower_overall"],
        agent_stayed_calm=sentiment["agent_stayed_calm"],
        rejected_findings=rejected,
        diagnostics=diagnostics,
    )


def analyse_call(raw_segments: list, call, sleep_fn=time.sleep):
    """One whole-conversation analysis. Returns (labelled_segments, ComplianceResult)."""
    if not raw_segments:
        raise LLMResponseValidationError("No transcript segments to analyse.")
    client = llm_service._get_client()
    conversation = build_messages(raw_segments, call)
    last_error: Optional[Exception] = None
    for _ in range(2):  # one corrective retry on a schema failure
        data = llm_service._chat_json(client, conversation, sleep_fn=sleep_fn)
        try:
            return parse_and_validate(data, raw_segments)
        except LLMResponseValidationError as exc:
            last_error = exc
            conversation = conversation + [
                {"role": "assistant", "content": json.dumps(data, ensure_ascii=False)[:6000]},
                {"role": "user", "content": f"That response failed validation: {exc} Respond again with ONLY the corrected JSON object."},
            ]
    raise last_error  # type: ignore[misc]
