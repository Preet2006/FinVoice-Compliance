"""
Shared plumbing for the (single) analysis in unified_analysis_service: the Groq client, one structured-JSON
chat completion with the retry policy, the data types the analysis returns (LabelledSegment, RawViolation,
ComplianceResult), the speaker-confidence rules and the rejected-finding records. Groq's hosted
openai/gpt-oss-120b is the sole LLM and is never substituted. The analysis itself (the prompt, the speaker
labelling, the findings, R6 and the sentiment) lives in unified_analysis_service.
"""

import json
import time
from dataclasses import dataclass, field
from typing import Literal, Optional

import groq

from app.config import settings
from app.services.provider_errors import (
    NOT_CONFIGURED,
    RATE_LIMIT,
    UNAVAILABLE,
    ProviderError,
    classify_groq_exception,
    safe_message,
)

LLM_MODEL = "openai/gpt-oss-120b"


class LLMResponseValidationError(Exception):
    """Raised when the model's output can't be validated against the
    expected schema even after a corrective retry."""


class LLMProviderError(ProviderError):
    """A classified, redacted analysis-provider failure (invalid key,
    timeout, unavailable, rejected request, ...). The message is safe to
    store and return. Never caught-and-ignored by the pipeline."""


class LLMQuotaError(LLMProviderError):
    """The provider's rate or usage limit was reached."""

    kind = RATE_LIMIT


def _provider_error(kind: str, message: str) -> LLMProviderError:
    return LLMQuotaError(message) if kind == RATE_LIMIT else LLMProviderError(message, kind=kind)


def _get_client() -> groq.Groq:
    if not settings.groq_api_key:
        raise LLMProviderError(safe_message(NOT_CONFIGURED, "analysis provider"), kind=NOT_CONFIGURED)
    return groq.Groq(api_key=settings.groq_api_key)


def _chat_json(
    client: groq.Groq,
    messages: list[dict],
    sleep_fn=time.sleep,
) -> dict:
    """
    One structured-JSON chat completion with the plan's retry policy:
    - transient provider error (429/503/connection/timeout): 1 retry after
      a 10s backoff, then a classified LLMProviderError (an invalid key or
      a rejected request is not retried).
    - malformed JSON: 1 corrective retry (append the parse error to the
      conversation), then LLMResponseValidationError.
    Max 2 attempts total per §5.4's retry budget.
    """
    conversation = list(messages)
    last_provider_error: Optional[Exception] = None

    for attempt in range(2):
        try:
            response = client.chat.completions.create(
                model=LLM_MODEL,
                messages=conversation,
                response_format={"type": "json_object"},
                temperature=0,
            )
        except (groq.RateLimitError, groq.APIStatusError, groq.APIConnectionError, groq.APITimeoutError) as exc:
            kind, message = classify_groq_exception(exc, "analysis provider")
            last_provider_error = _provider_error(kind, message)
            if last_provider_error.retryable and attempt == 0:
                sleep_fn(10)
                continue
            # `from None`: the raw provider exception (which may carry
            # organisation ids or URLs) must not travel with this error.
            raise last_provider_error from None

        raw = response.choices[0].message.content or ""
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            if attempt == 0:
                conversation = conversation + [
                    {"role": "assistant", "content": raw},
                    {
                        "role": "user",
                        "content": (
                            "Your last response was not valid JSON matching the "
                            f"required schema. Parse error: {exc}. Respond again "
                            "with ONLY a valid JSON object matching the schema."
                        ),
                    },
                ]
                continue
            raise LLMResponseValidationError(
                f"LLM response validation failed: not valid JSON after retry ({exc})"
            ) from exc

    # Unreachable in practice (loop always returns or raises), but keeps
    # type-checkers happy and avoids an implicit None return.
    raise last_provider_error or LLMProviderError(safe_message(UNAVAILABLE, "analysis provider"))


# --- Speaker labels and their confidence ---------------------------------

# Below this, a speaker label is treated as uncertain for review/scoring
# purposes (see is_uncertain_confidence). This is an uncalibrated,
# heuristic cutoff on the model's own self-reported estimate — not a
# measured probability of correctness. Missing/malformed confidence is
# always uncertain regardless of this threshold.
SPEAKER_CONFIDENCE_UNCERTAIN_THRESHOLD = 0.6
SpeakerState = Literal["agent", "borrower", "third_party", "unknown", "mixed"]
SPEAKER_STATES = {"agent", "borrower", "third_party", "unknown", "mixed"}


@dataclass
class LabelledSegment:
    seq: int
    speaker: SpeakerState
    start: float
    end: float
    text: str
    # None when the model didn't provide a usable confidence value for
    # this segment's speaker label. Never defaulted to a high value.
    confidence: Optional[float] = None
    # Preserves the model's explicit uncertainty independently of its role
    # guess. A segment can therefore be `agent` and still require review.
    speaker_uncertain: bool = False


def _parse_speaker_confidence(raw) -> Optional[float]:
    """
    Returns a float in [0.0, 1.0], or None for anything that isn't a
    plain, in-range number — missing field, wrong type (including bool,
    which is a subclass of int in Python), NaN, or out-of-range. Callers
    must treat None as "unknown", never as "high confidence".
    """
    if raw is None or isinstance(raw, bool):
        return None
    if not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    if value != value:  # NaN
        return None
    if value < 0.0 or value > 1.0:
        return None
    return value


def is_uncertain_confidence(confidence: Optional[float]) -> bool:
    """True when `confidence` should not be trusted as a confident speaker
    attribution: missing/malformed (None), or below the review threshold."""
    return confidence is None or confidence < SPEAKER_CONFIDENCE_UNCERTAIN_THRESHOLD


# --- What the analysis returns, and rejected findings ----------------------

# Why a proposed finding was not accepted. Only INVALID_EVIDENCE rejections
# are worth a manager's attention (a rule was reported but its evidence
# failed validation) and are stored as unverified findings; the others are
# policy or housekeeping and are only kept as diagnostics.
KIND_INVALID_EVIDENCE = "invalid_evidence"
KIND_POLICY = "policy"
KIND_DUPLICATE = "duplicate"


def rejection(entry, reason: str, kind: str) -> dict:
    return {"entry": entry, "reason": reason, "kind": kind}


@dataclass
class RawViolation:
    rule_code: str
    segment_seq: int
    quote: str
    explanation: str
    confidence: float
    # True when the analysis itself is unsure of the finding (unclear words,
    # uncertain speaker); the pipeline then holds it for manager review.
    needs_review: bool = False


@dataclass
class ComplianceResult:
    violations: list[RawViolation]
    borrower_sentiment: str
    agent_stayed_calm: bool
    # Findings the backend refused to accept, with the reason. Not persisted;
    # kept so unsupported model output is visible instead of silently lost.
    rejected_findings: list = field(default_factory=list)
    # Non-persisted self-consistency notes about the model's output.
    diagnostics: dict = field(default_factory=dict)


def _normalize(text: str) -> str:
    return " ".join(text.strip().lower().split())
