"""
Batch 7a: running analysis from the UI uses the existing endpoints. These
tests cover the pieces the UI relies on: failure_kind on every place a
failure note is returned, role authorization of the start action, upload
-> start -> status -> detail flow with mocked pipeline dependencies, and
that nothing starts analysis by itself.
"""

import pytest

from app.config import settings
from app.models import Call
from app.services import llm_service, provider_errors, transcription_service, unified_analysis_service
from tests.conftest import CALLER1_HEADERS, HEAD_HEADERS, MGR1_HEADERS, MGR2_HEADERS

from tests.integration.test_reprocess_api import setup_queued_call  # noqa: E402


# --- failure_kind: derived from the real safe messages ------------------------

ALL_PROVIDER_KINDS = [
    provider_errors.AUTH,
    provider_errors.RATE_LIMIT,
    provider_errors.TIMEOUT,
    provider_errors.UNAVAILABLE,
    provider_errors.REJECTED,
    provider_errors.NOT_CONFIGURED,
]


@pytest.mark.parametrize("kind", ALL_PROVIDER_KINDS)
@pytest.mark.parametrize("service", ["analysis provider", "transcription provider"])
def test_failure_kind_round_trips_every_safe_message(kind, service):
    message = provider_errors.safe_message(kind, service, status=400)
    assert provider_errors.failure_kind_from_message(message) == kind
    wrapped = f"Reprocessing failed; the previous results were kept. Transcription failed: {message}"
    assert provider_errors.failure_kind_from_message(wrapped) == kind


@pytest.mark.parametrize(
    "note, expected",
    [
        (None, None),
        ("", None),
        ("Audio file not found or unreadable: a.wav", provider_errors.AUDIO_MISSING),
        ("Unexpected processing error; see the server log.", provider_errors.INTERNAL),
        ("LLM response validation failed: not valid JSON after retry", provider_errors.INVALID_RESPONSE),
        ("Unified analysis response is missing a valid speaker for segment index(es): [3]", provider_errors.INVALID_RESPONSE),
        ("whisper-large-v3-turbo returned no segments for this audio file.", provider_errors.EMPTY_RESULT),
        ("something nobody anticipated", provider_errors.OTHER),
    ],
)
def test_failure_kind_for_pipeline_notes(note, expected):
    assert provider_errors.failure_kind_from_message(note) == expected


def test_an_invalid_key_is_not_reported_as_a_generic_rejection():
    msg = provider_errors.safe_message(provider_errors.AUTH, "analysis provider")
    assert provider_errors.failure_kind_from_message(msg) == provider_errors.AUTH


def _fail_transcription(monkeypatch, kind):
    def boom(audio_bytes, filename, sleep_fn=None):
        raise transcription_service.TranscriptionFailedError(
            "Transcription failed: " + provider_errors.safe_message(kind, "transcription provider"), kind=kind
        )

    monkeypatch.setattr(transcription_service, "transcribe", boom)


@pytest.mark.parametrize("kind", ALL_PROVIDER_KINDS)
def test_reprocess_detail_and_batch_status_all_report_the_failure_kind(client, db_session, tmp_path, monkeypatch, kind):
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    monkeypatch.setattr(settings, "audio_dir", str(audio_dir))
    call = setup_queued_call(db_session, audio_dir, monkeypatch)
    call.batch_id = "b-test"
    db_session.commit()
    _fail_transcription(monkeypatch, kind)

    started = client.post("/api/calls/CALL-REP-01/reprocess", headers=HEAD_HEADERS).json()
    detail = client.get("/api/calls/CALL-REP-01", headers=HEAD_HEADERS).json()
    batch = client.get("/api/uploads/batch/b-test", headers=HEAD_HEADERS).json()["items"][0]

    assert started["status"] == "failed"
    assert started["failure_kind"] == detail["failure_kind"] == batch["failure_kind"] == kind


def test_no_failure_means_no_failure_kind(client, db_session, tmp_path, monkeypatch):
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    monkeypatch.setattr(settings, "audio_dir", str(audio_dir))
    setup_queued_call(db_session, audio_dir, monkeypatch)

    started = client.post("/api/calls/CALL-REP-01/reprocess", headers=HEAD_HEADERS).json()
    assert started["status"] == "ready" and started["failure_kind"] is None
    assert client.get("/api/calls/CALL-REP-01", headers=HEAD_HEADERS).json()["failure_kind"] is None


def test_a_failed_reprocess_that_kept_old_results_still_reports_its_kind(client, db_session, tmp_path, monkeypatch):
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    monkeypatch.setattr(settings, "audio_dir", str(audio_dir))
    setup_queued_call(db_session, audio_dir, monkeypatch)
    assert client.post("/api/calls/CALL-REP-01/reprocess", headers=HEAD_HEADERS).json()["status"] == "ready"

    _fail_transcription(monkeypatch, provider_errors.RATE_LIMIT)
    detail = None
    client.post("/api/calls/CALL-REP-01/reprocess", headers=HEAD_HEADERS)
    detail = client.get("/api/calls/CALL-REP-01", headers=HEAD_HEADERS).json()

    assert detail["status"] == "ready" and detail["last_reprocess_failed"] is True
    assert detail["failure_kind"] == provider_errors.RATE_LIMIT
    assert detail["failure_reason"].startswith("Reprocessing failed; the previous results were kept.")


def test_batch_status_redacts_provider_internals(client, db_session, tmp_path, monkeypatch):
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    monkeypatch.setattr(settings, "audio_dir", str(audio_dir))
    call = setup_queued_call(db_session, audio_dir, monkeypatch)
    call.batch_id = "b-leak"
    call.status = "failed"
    call.failure_reason = "failed: org_01abcdefg gsk_ABCDEFGH12345678 https://console.example.com/billing"
    db_session.commit()

    item = client.get("/api/uploads/batch/b-leak", headers=HEAD_HEADERS).json()["items"][0]
    for secret in ("org_01abcdefg", "gsk_", "console.example.com"):
        assert secret not in item["failure_reason"]


# --- the start action: who may run it, and that nothing starts by itself ------


def test_listing_and_viewing_calls_never_start_analysis(client, db_session, tmp_path, monkeypatch):
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    monkeypatch.setattr(settings, "audio_dir", str(audio_dir))
    setup_queued_call(db_session, audio_dir, monkeypatch)

    def must_not_run(*a, **k):
        raise AssertionError("analysis started without an explicit start action")

    monkeypatch.setattr(transcription_service, "transcribe", must_not_run)
    monkeypatch.setattr(unified_analysis_service, "analyse_call", must_not_run)

    for headers in (HEAD_HEADERS, MGR1_HEADERS, CALLER1_HEADERS):
        client.get("/api/calls", headers=headers)
        client.get("/api/calls/CALL-REP-01", headers=headers)
        client.get("/api/calls/CALL-REP-01/transcript", headers=headers)
        client.get("/api/calls/CALL-REP-01/violations", headers=headers)
        client.get("/api/uploads/batch/anything", headers=headers)
    db_session.expire_all()
    assert db_session.get(Call, "CALL-REP-01").status == "queued"


@pytest.mark.parametrize("headers, expected", [(CALLER1_HEADERS, 403), (MGR1_HEADERS, 200), (HEAD_HEADERS, 200)])
def test_only_manager_and_head_may_start_analysis(client, db_session, tmp_path, monkeypatch, headers, expected):
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    monkeypatch.setattr(settings, "audio_dir", str(audio_dir))
    setup_queued_call(db_session, audio_dir, monkeypatch)
    assert client.post("/api/calls/CALL-REP-01/reprocess", headers=headers).status_code == expected


def test_a_manager_outside_the_team_cannot_start_it(client, db_session, tmp_path, monkeypatch):
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    monkeypatch.setattr(settings, "audio_dir", str(audio_dir))
    setup_queued_call(db_session, audio_dir, monkeypatch)
    assert client.post("/api/calls/CALL-REP-01/reprocess", headers=MGR2_HEADERS).status_code == 404


def test_starting_an_already_running_call_is_a_409_not_a_second_run(client, db_session, tmp_path, monkeypatch):
    from app.services import pipeline

    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    monkeypatch.setattr(settings, "audio_dir", str(audio_dir))
    setup_queued_call(db_session, audio_dir, monkeypatch)
    pipeline._ACTIVE_CALLS.add("CALL-REP-01")
    try:
        resp = client.post("/api/calls/CALL-REP-01/reprocess", headers=HEAD_HEADERS)
    finally:
        pipeline._ACTIVE_CALLS.discard("CALL-REP-01")
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "ALREADY_PROCESSING"


# --- the whole flow the UI drives: start -> status -> outcome -> review --------


def test_full_flow_start_then_outcome_and_sentiment_are_available_for_review(client, db_session, tmp_path, monkeypatch):
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    monkeypatch.setattr(settings, "audio_dir", str(audio_dir))
    call = setup_queued_call(db_session, audio_dir, monkeypatch)
    call.batch_id = "b-flow"
    db_session.commit()

    before = client.get("/api/calls/CALL-REP-01", headers=MGR1_HEADERS).json()
    assert before["status"] == "queued" and before["analysis_outcome"] == "not_analyzed"
    assert before["sentiment"] is None and before["agent_stayed_calm"] is None

    started = client.post("/api/calls/CALL-REP-01/reprocess", headers=MGR1_HEADERS).json()
    assert started["status"] == "ready"

    after = client.get("/api/calls/CALL-REP-01", headers=MGR1_HEADERS).json()
    assert after["status"] == "ready"
    assert after["analysis_outcome"] in ("violations_found", "pending_review", "incomplete", "clean")
    assert after["sentiment"] in ("positive", "neutral", "negative")
    assert isinstance(after["agent_stayed_calm"], bool)
    assert client.get("/api/uploads/batch/b-flow", headers=MGR1_HEADERS).json()["items"][0]["status"] == "ready"
    assert client.get("/api/calls/CALL-REP-01/violations", headers=MGR1_HEADERS).status_code == 200
