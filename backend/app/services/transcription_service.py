"""
Hosted speech-to-text via Groq's Whisper API (whisper-large-v3-turbo) only.
No local STT models, no Ollama, no silent substitution — per
MASTER_IMPLEMENTATION_PLAN §5.2/§5.4/§5.8.

Sarvam AI is documented as a manual, explicitly-configured fallback
(TRANSCRIPTION_PROVIDER=sarvam) but is never auto-activated; this module
does not implement it, since the assignment's primary path is Groq and no
automatic fallback is permitted anyway.
"""

import time
from dataclasses import dataclass

import groq

from app.config import settings
from app.services.provider_errors import (
    EMPTY_RESULT,
    NOT_CONFIGURED,
    ProviderError,
    classify_groq_exception,
    safe_message,
)


class TranscriptionFailedError(ProviderError):
    """Raised after retries are exhausted. Caller must mark the Call failed
    — never silently return an empty/clean transcript. The message is
    classified and redacted, so it is safe to store and return."""


@dataclass
class RawSegment:
    seq: int
    start: float
    end: float
    text: str


def _get_client() -> groq.Groq:
    if not settings.groq_api_key:
        raise TranscriptionFailedError(
            "Transcription failed: " + safe_message(NOT_CONFIGURED, "transcription provider"),
            kind=NOT_CONFIGURED,
        )
    return groq.Groq(api_key=settings.groq_api_key)


def transcribe(
    audio_bytes: bytes,
    filename: str,
    sleep_fn=time.sleep,
) -> list[RawSegment]:
    """
    Calls Groq's hosted whisper-large-v3-turbo with verbose_json output so
    segment-level timestamps come directly from the provider — these
    timestamps are carried through unchanged by every later pipeline step.

    Retry policy per §5.4: 1 retry with a 10s backoff on a transient
    provider error; a second failure raises TranscriptionFailedError, which
    the pipeline must turn into Call.status='failed' — never a fallback to
    another provider or a fabricated empty transcript.
    """
    client = _get_client()
    last_error: TranscriptionFailedError | None = None

    for attempt in range(2):
        try:
            response = client.audio.transcriptions.create(
                model=settings.groq_transcription_model,
                file=(filename, audio_bytes),
                response_format="verbose_json",
                timestamp_granularities=["segment"],
            )
            segments = getattr(response, "segments", None) or []
            if not segments:
                # A real recording with speech should produce at least one
                # segment; an empty result is treated as a failure rather
                # than silently persisted as "a call with no transcript".
                raise TranscriptionFailedError(
                    "whisper-large-v3-turbo returned no segments for this audio file.",
                    kind=EMPTY_RESULT,
                )
            return [
                RawSegment(
                    seq=i,
                    start=float(seg["start"]),
                    end=float(seg["end"]),
                    text=str(seg["text"]).strip(),
                )
                for i, seg in enumerate(segments)
            ]
        except (groq.RateLimitError, groq.APIStatusError, groq.APIConnectionError, groq.APITimeoutError) as exc:
            kind, message = classify_groq_exception(exc, "transcription provider")
            last_error = TranscriptionFailedError(f"Transcription failed: {message}", kind=kind)
            if last_error.retryable and attempt == 0:
                sleep_fn(10)
                continue
            break

    # `from None`: the raw provider exception (which may carry organisation
    # ids or URLs) must not travel with this error.
    raise last_error from None
