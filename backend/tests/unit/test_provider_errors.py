import logging
from types import SimpleNamespace

import groq
import httpx
import pytest

from app.config import settings
from app.services import llm_service as llm
from app.services import provider_errors as pe
from app.services import transcription_service as ts

REQUEST = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")

LEAKY_429 = (
    "Rate limit reached for model `openai/gpt-oss-120b` in organization `org_01jyvt4pe4ehq9gp91ph3fagnv` "
    "service tier `on_demand` on tokens per day (TPD): Limit 200000, Used 199837, Requested 5039. "
    "Please try again in 35m6s. Need more tokens? Upgrade to Dev Tier today at "
    "https://console.groq.com/settings/billing"
)
SENSITIVE = ["org_01jyvt4pe4ehq9gp91ph3fagnv", "199837", "200000", "5039", "console.groq.com", "35m6s", "gsk_"]


def status_error(cls, status, text=LEAKY_429):
    return cls(text, response=httpx.Response(status, request=REQUEST, text=text), body=None)


def assert_clean(message):
    for secret in SENSITIVE:
        assert secret not in message, f"{secret!r} leaked into {message!r}"


class FakeCompletions:
    def __init__(self, behaviors):
        self.behaviors = list(behaviors)
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        behavior = self.behaviors.pop(0)
        if isinstance(behavior, Exception):
            raise behavior
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=behavior))])


def llm_client(*behaviors):
    return SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(behaviors)))


# --- redaction -----------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, gone",
    [
        ("key gsk_AbCdEfGh1234567890 rejected", "gsk_AbCdEfGh1234567890"),
        ("Authorization: Bearer abc.def-123 failed", "abc.def-123"),
        ("organization `org_01jyvt4pe4ehq9gp91ph3fagnv`", "org_01jyvt4pe4ehq9gp91ph3fagnv"),
        ("see https://console.groq.com/settings/billing now", "console.groq.com"),
        (r"cannot open C:\Users\someone\secret\audio.wav", r"C:\Users\someone"),
        ("cannot open /srv/app/data/audio/call.wav", "/srv/app/data"),
    ],
)
def test_redact_text_removes_sensitive_fragments(raw, gone):
    assert gone not in pe.redact_text(raw)


def test_redact_text_keeps_ordinary_text_and_handles_none():
    assert pe.redact_text("The provider timed out. Try again.") == "The provider timed out. Try again."
    assert pe.redact_text(None) == ""


# --- classification ---------------------------------------------------------------


@pytest.mark.parametrize(
    "exc, kind, retryable",
    [
        (status_error(groq.AuthenticationError, 401), pe.AUTH, False),
        (status_error(groq.PermissionDeniedError, 403), pe.AUTH, False),
        (status_error(groq.RateLimitError, 429), pe.RATE_LIMIT, True),
        (groq.APITimeoutError(request=REQUEST), pe.TIMEOUT, True),
        (groq.APIConnectionError(request=REQUEST), pe.UNAVAILABLE, True),
        (status_error(groq.InternalServerError, 500), pe.UNAVAILABLE, True),
        (status_error(groq.BadRequestError, 400), pe.REJECTED, False),
        (status_error(groq.NotFoundError, 404), pe.REJECTED, False),
    ],
)
def test_provider_failures_are_classified_accurately(exc, kind, retryable):
    got_kind, message = pe.classify_groq_exception(exc, "analysis provider")
    assert got_kind == kind
    assert pe.ProviderError(message, kind=got_kind).retryable is retryable
    assert_clean(message)


def test_each_kind_has_a_distinct_accurate_message():
    messages = {k: pe.safe_message(k, "analysis provider", 400) for k in (pe.AUTH, pe.RATE_LIMIT, pe.TIMEOUT, pe.UNAVAILABLE, pe.REJECTED, pe.NOT_CONFIGURED)}
    assert len(set(messages.values())) == 6
    assert "API key" in messages[pe.AUTH] and "authentication" in messages[pe.AUTH]
    assert "rate or usage limit" in messages[pe.RATE_LIMIT]
    assert "timed out" in messages[pe.TIMEOUT]
    assert "HTTP 400" in messages[pe.REJECTED]
    assert "not configured" in messages[pe.NOT_CONFIGURED]


def test_the_log_carries_redacted_detail_only(caplog):
    with caplog.at_level(logging.WARNING, logger=pe.logger.name):
        pe.classify_groq_exception(status_error(groq.RateLimitError, 429), "analysis provider")
    assert "rate_limit" in caplog.text and "status=429" in caplog.text
    # The log keeps non-identifying operational numbers (usage, retry delay)
    # for operators, but never identifiers, URLs or keys. The stored and API
    # messages are held to the stricter assert_clean standard elsewhere.
    for identifier in ("org_01jyvt4pe4ehq9gp91ph3fagnv", "console.groq.com", "gsk_"):
        assert identifier not in caplog.text


# --- analysis provider (_chat_json) --------------------------------------------------


def test_invalid_key_is_reported_as_authentication_and_not_retried():
    client = llm_client(status_error(groq.AuthenticationError, 401))
    sleeps = []
    with pytest.raises(llm.LLMProviderError) as info:
        llm._chat_json(client, [{"role": "user", "content": "x"}], sleep_fn=sleeps.append)
    assert info.value.kind == pe.AUTH and not isinstance(info.value, llm.LLMQuotaError)
    assert client.chat.completions.calls == 1 and sleeps == []  # no pointless retry
    assert "quota" not in str(info.value).lower()
    assert_clean(str(info.value))


def test_rate_limit_is_reported_as_quota_after_one_retry_with_redacted_text():
    client = llm_client(status_error(groq.RateLimitError, 429), status_error(groq.RateLimitError, 429))
    sleeps = []
    with pytest.raises(llm.LLMQuotaError) as info:
        llm._chat_json(client, [{"role": "user", "content": "x"}], sleep_fn=sleeps.append)
    assert info.value.kind == pe.RATE_LIMIT
    assert client.chat.completions.calls == 2 and sleeps == [10]
    assert_clean(str(info.value))
    assert info.value.__cause__ is None and info.value.__suppress_context__  # raw exception is not chained


def test_timeout_is_reported_as_timeout_after_one_retry():
    client = llm_client(groq.APITimeoutError(request=REQUEST), groq.APITimeoutError(request=REQUEST))
    with pytest.raises(llm.LLMProviderError) as info:
        llm._chat_json(client, [{"role": "user", "content": "x"}], sleep_fn=lambda s: None)
    assert info.value.kind == pe.TIMEOUT and "timed out" in str(info.value)
    assert client.chat.completions.calls == 2


def test_server_error_is_reported_as_unavailable_after_one_retry():
    client = llm_client(status_error(groq.InternalServerError, 500), status_error(groq.InternalServerError, 500))
    with pytest.raises(llm.LLMProviderError) as info:
        llm._chat_json(client, [{"role": "user", "content": "x"}], sleep_fn=lambda s: None)
    assert info.value.kind == pe.UNAVAILABLE
    assert_clean(str(info.value))


def test_rejected_request_is_not_retried_and_names_the_status():
    client = llm_client(status_error(groq.BadRequestError, 400))
    with pytest.raises(llm.LLMProviderError) as info:
        llm._chat_json(client, [{"role": "user", "content": "x"}], sleep_fn=lambda s: None)
    assert info.value.kind == pe.REJECTED and "HTTP 400" in str(info.value)
    assert client.chat.completions.calls == 1


def test_transient_error_then_success_still_works():
    client = llm_client(status_error(groq.InternalServerError, 503), '{"ok": true}')
    assert llm._chat_json(client, [{"role": "user", "content": "x"}], sleep_fn=lambda s: None) == {"ok": True}


def test_missing_api_key_is_a_configuration_error_not_quota(monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "")
    with pytest.raises(llm.LLMProviderError) as info:
        llm._get_client()
    assert info.value.kind == pe.NOT_CONFIGURED and "not configured" in str(info.value)
    assert not isinstance(info.value, llm.LLMQuotaError)


def test_quota_error_remains_catchable_as_the_provider_error_base():
    assert issubclass(llm.LLMQuotaError, llm.LLMProviderError) and issubclass(llm.LLMProviderError, pe.ProviderError)
    assert llm.LLMQuotaError("custom").kind == pe.RATE_LIMIT  # existing call sites keep working


# --- transcription provider --------------------------------------------------------------


class FakeTranscriptions:
    def __init__(self, behaviors):
        self.behaviors, self.calls = list(behaviors), 0

    def create(self, **kwargs):
        self.calls += 1
        behavior = self.behaviors.pop(0)
        if isinstance(behavior, Exception):
            raise behavior
        return behavior


def ts_client(*behaviors):
    return SimpleNamespace(audio=SimpleNamespace(transcriptions=FakeTranscriptions(behaviors)))


def test_transcription_invalid_key_is_authentication_and_not_retried(monkeypatch):
    client = ts_client(status_error(groq.AuthenticationError, 401))
    monkeypatch.setattr(ts, "_get_client", lambda: client)
    sleeps = []
    with pytest.raises(ts.TranscriptionFailedError) as info:
        ts.transcribe(b"audio", "x.wav", sleep_fn=sleeps.append)
    assert info.value.kind == pe.AUTH and client.audio.transcriptions.calls == 1 and sleeps == []
    assert_clean(str(info.value))


def test_transcription_rate_limit_is_redacted_after_one_retry(monkeypatch):
    client = ts_client(status_error(groq.RateLimitError, 429), status_error(groq.RateLimitError, 429))
    monkeypatch.setattr(ts, "_get_client", lambda: client)
    with pytest.raises(ts.TranscriptionFailedError) as info:
        ts.transcribe(b"audio", "x.wav", sleep_fn=lambda s: None)
    assert info.value.kind == pe.RATE_LIMIT and client.audio.transcriptions.calls == 2
    assert str(info.value).startswith("Transcription failed:")
    assert_clean(str(info.value))
    assert info.value.__cause__ is None


def test_transcription_timeout_is_classified(monkeypatch):
    client = ts_client(groq.APITimeoutError(request=REQUEST), groq.APITimeoutError(request=REQUEST))
    monkeypatch.setattr(ts, "_get_client", lambda: client)
    with pytest.raises(ts.TranscriptionFailedError) as info:
        ts.transcribe(b"audio", "x.wav", sleep_fn=lambda s: None)
    assert info.value.kind == pe.TIMEOUT


def test_transcription_missing_key_is_a_configuration_error(monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "")
    with pytest.raises(ts.TranscriptionFailedError) as info:
        ts.transcribe(b"audio", "x.wav", sleep_fn=lambda s: None)
    assert info.value.kind == pe.NOT_CONFIGURED


def test_empty_transcript_is_not_retried_and_keeps_its_own_kind(monkeypatch):
    client = ts_client(SimpleNamespace(segments=[]))
    monkeypatch.setattr(ts, "_get_client", lambda: client)
    with pytest.raises(ts.TranscriptionFailedError) as info:
        ts.transcribe(b"audio", "x.wav", sleep_fn=lambda s: None)
    assert info.value.kind == pe.EMPTY_RESULT and client.audio.transcriptions.calls == 1
