from types import SimpleNamespace

import groq
import pytest

from app.services import transcription_service as ts


class FakeTranscriptions:
    def __init__(self, behaviors):
        self.behaviors = list(behaviors)
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        behavior = self.behaviors.pop(0)
        if isinstance(behavior, Exception):
            raise behavior
        return behavior


class FakeClient:
    def __init__(self, behaviors):
        self.audio = SimpleNamespace(transcriptions=FakeTranscriptions(behaviors))


def fake_response(segments):
    return SimpleNamespace(segments=segments)


def no_sleep(_seconds):
    pass


def test_transcribe_happy_path(monkeypatch):
    segments = [
        {"start": 0.0, "end": 2.5, "text": "Namaste, main Pooja bol rahi hoon"},
        {"start": 2.5, "end": 4.0, "text": "Ji haan boliye"},
    ]
    client = FakeClient([fake_response(segments)])
    monkeypatch.setattr(ts, "_get_client", lambda: client)

    result = ts.transcribe(b"fake audio bytes", "call_01.wav", sleep_fn=no_sleep)

    assert len(result) == 2
    assert result[0].seq == 0
    assert result[0].text == "Namaste, main Pooja bol rahi hoon"
    assert result[1].end == 4.0


def test_transcribe_retries_once_then_succeeds(monkeypatch):
    segments = [{"start": 0.0, "end": 1.0, "text": "hello"}]
    client = FakeClient(
        [groq.APIConnectionError(request=SimpleNamespace()), fake_response(segments)]
    )
    monkeypatch.setattr(ts, "_get_client", lambda: client)

    result = ts.transcribe(b"audio", "x.wav", sleep_fn=no_sleep)
    assert len(result) == 1


def test_transcribe_fails_after_second_error(monkeypatch):
    client = FakeClient(
        [
            groq.APIConnectionError(request=SimpleNamespace()),
            groq.APIConnectionError(request=SimpleNamespace()),
        ]
    )
    monkeypatch.setattr(ts, "_get_client", lambda: client)

    with pytest.raises(ts.TranscriptionFailedError):
        ts.transcribe(b"audio", "x.wav", sleep_fn=no_sleep)


def test_transcribe_empty_segments_raises(monkeypatch):
    client = FakeClient([fake_response([])])
    monkeypatch.setattr(ts, "_get_client", lambda: client)

    with pytest.raises(ts.TranscriptionFailedError):
        ts.transcribe(b"audio", "x.wav", sleep_fn=no_sleep)


def test_transcribe_requires_api_key(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "groq_api_key", "")
    with pytest.raises(ts.TranscriptionFailedError):
        ts.transcribe(b"audio", "x.wav", sleep_fn=no_sleep)
