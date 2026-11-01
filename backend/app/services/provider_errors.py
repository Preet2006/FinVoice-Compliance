"""
Classified, redacted provider failures shared by transcription and analysis.

An exception from the provider SDK can carry sensitive detail (organisation
IDs, billing URLs, usage numbers, local paths). It is never stored or returned
to the API: it is classified into a kind with a fixed, safe message, and the
redacted raw text goes only to the server log.
"""

import logging
import re

import groq

logger = logging.getLogger(__name__)

RATE_LIMIT = "rate_limit"
AUTH = "auth"
TIMEOUT = "timeout"
UNAVAILABLE = "unavailable"
REJECTED = "rejected"
NOT_CONFIGURED = "not_configured"
EMPTY_RESULT = "empty_result"

# Worth one retry. An invalid key or a rejected request will not improve.
_RETRYABLE = {RATE_LIMIT, TIMEOUT, UNAVAILABLE}

_REDACTIONS = [
    (re.compile(r"gsk_[A-Za-z0-9_\-]{6,}"), "[redacted-key]"),
    (re.compile(r"\b(?:sk|pk|api)[-_][A-Za-z0-9_\-]{12,}"), "[redacted-key]"),
    (re.compile(r"Bearer\s+[A-Za-z0-9._\-]+", re.IGNORECASE), "Bearer [redacted]"),
    (re.compile(r"\borg_[A-Za-z0-9]+"), "[redacted-org]"),
    (re.compile(r"https?://\S+"), "[redacted-url]"),
    (re.compile(r"[A-Za-z]:\\[^\s)'\"]+"), "[redacted-path]"),
    (re.compile(r"(?<![\w/])/(?:[\w.\-]+/){2,}[\w.\-]+"), "[redacted-path]"),
]


def redact_text(text) -> str:
    """Removes keys, organisation IDs, URLs and filesystem paths."""
    out = str(text) if text is not None else ""
    for pattern, replacement in _REDACTIONS:
        out = pattern.sub(replacement, out)
    return out


class ProviderError(Exception):
    """A provider failure whose message is safe to store and return."""

    kind = UNAVAILABLE

    def __init__(self, message: str = "", kind: str = None):
        super().__init__(message)
        if kind is not None:
            self.kind = kind

    @property
    def retryable(self) -> bool:
        return self.kind in _RETRYABLE


def safe_message(kind: str, service: str, status=None) -> str:
    if kind == AUTH:
        return f"The {service} rejected the API key (authentication failed). Check the configured GROQ_API_KEY."
    if kind == RATE_LIMIT:
        return f"The {service}'s rate or usage limit was reached. Try again later."
    if kind == TIMEOUT:
        return f"The {service} timed out. Try again."
    if kind == REJECTED:
        return f"The {service} rejected the request (HTTP {status})."
    if kind == NOT_CONFIGURED:
        return f"The API key for the {service} is not configured."
    return f"The {service} is unavailable (connection or server error). Try again later."


INVALID_RESPONSE = "invalid_response"
AUDIO_MISSING = "audio_missing"
INTERNAL = "internal"
OTHER = "other"

# Fixed fragments of the safe messages above and of the pipeline's own
# failure notes, checked in order (the API-key phrase before the generic
# "rejected the request"). Deriving the kind from the stored, already
# redacted message avoids a database column and keeps one source of truth.
_KIND_FRAGMENTS = [
    ("rejected the API key", AUTH),
    ("rate or usage limit", RATE_LIMIT),
    ("is not configured", NOT_CONFIGURED),
    ("timed out", TIMEOUT),
    ("rejected the request", REJECTED),
    ("is unavailable", UNAVAILABLE),
    ("returned no segments", EMPTY_RESULT),
    ("Audio file not found", AUDIO_MISSING),
    ("Unexpected processing error", INTERNAL),
    ("validation failed", INVALID_RESPONSE),
    ("analysis response", INVALID_RESPONSE),
    ("labelling response", INVALID_RESPONSE),
    ("Compliance response", INVALID_RESPONSE),
]


def failure_kind_from_message(reason):
    """The kind of failure a stored failure note describes, or None when
    there is no failure."""
    if not reason:
        return None
    for fragment, kind in _KIND_FRAGMENTS:
        if fragment in reason:
            return kind
    return OTHER


def classify_groq_exception(exc: Exception, service: str):
    """Returns (kind, safe_message) for a Groq SDK exception, logging the
    redacted detail for operators."""
    status = getattr(exc, "status_code", None)
    if isinstance(exc, groq.APITimeoutError):
        kind = TIMEOUT
    elif isinstance(exc, (groq.AuthenticationError, groq.PermissionDeniedError)) or status in (401, 403):
        kind = AUTH
    elif isinstance(exc, groq.RateLimitError) or status == 429:
        kind = RATE_LIMIT
    elif status == 408:
        kind = TIMEOUT
    elif isinstance(exc, groq.APIStatusError) and status is not None and 400 <= status < 500:
        kind = REJECTED
    else:
        kind = UNAVAILABLE
    logger.warning(
        "%s failure: kind=%s status=%s detail=%s",
        service, kind, status, redact_text(exc)[:300],
    )
    return kind, safe_message(kind, service, status)
