from app.config import settings
from app.services import llm_service, transcription_service
from tests.conftest import HEAD_HEADERS, MGR1_HEADERS, CALLER1_HEADERS


def setup_queued_call(db_session, scratch_audio, monkeypatch, call_id="CALL-REP-01"):
    from datetime import datetime
    from app.models import Call

    call = Call(
        id=call_id,
        caller_id="u-caller1",
        office_id="o-01",
        account_type="personal_loan",
        start_time=datetime(2026, 10, 15, 10, 0, 0),
        has_audio=True,
        audio_path="data/audio/rep.wav",
        status="queued",
        is_ground_truth=False,
    )
    db_session.add(call)
    db_session.commit()
    (scratch_audio / "rep.wav").write_bytes(b"RIFF....")

    monkeypatch.setattr(
        transcription_service,
        "transcribe",
        lambda audio_bytes, filename, sleep_fn=None: [
            transcription_service.RawSegment(seq=0, start=0.0, end=2.0, text="hello there")
        ],
    )
    from app.services import unified_analysis_service

    monkeypatch.setattr(
        unified_analysis_service,
        "analyse_call",
        lambda raw, call, **kw: (
            [llm_service.LabelledSegment(seq=0, speaker="agent", start=0.0, end=2.0, text="hello there")],
            llm_service.ComplianceResult(violations=[], borrower_sentiment="neutral", agent_stayed_calm=True),
        ),
    )
    return call


def test_reprocess_queued_call_succeeds(client, db_session, tmp_path, monkeypatch):
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    monkeypatch.setattr(settings, "audio_dir", str(audio_dir))
    setup_queued_call(db_session, audio_dir, monkeypatch)

    resp = client.post("/api/calls/CALL-REP-01/reprocess", headers=HEAD_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["score"] == 100


def test_reprocess_not_visible_to_other_manager_is_404(client, db_session, tmp_path, monkeypatch):
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    monkeypatch.setattr(settings, "audio_dir", str(audio_dir))
    setup_queued_call(db_session, audio_dir, monkeypatch)

    # u-caller1 reports to u-mgr1 (see tests/conftest.py fixture org chart),
    # so a different manager must not see or trigger processing on it.
    from tests.conftest import MGR2_HEADERS

    resp = client.post("/api/calls/CALL-REP-01/reprocess", headers=MGR2_HEADERS)
    assert resp.status_code == 404


def test_reprocess_metadata_only_call_rejected(client, db_session):
    # CALL-C1-01 in the fixture DB has has_audio=False.
    resp = client.post("/api/calls/CALL-C1-01/reprocess", headers=HEAD_HEADERS)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "CALL_NOT_ELIGIBLE"


def test_reprocess_already_processing_returns_409(client, db_session, tmp_path, monkeypatch):
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    monkeypatch.setattr(settings, "audio_dir", str(audio_dir))
    call = setup_queued_call(db_session, audio_dir, monkeypatch)
    call.status = "analyzing"
    db_session.commit()

    from app.services import pipeline

    pipeline._ACTIVE_CALLS.add("CALL-REP-01")  # genuinely being processed right now
    try:
        resp = client.post("/api/calls/CALL-REP-01/reprocess", headers=HEAD_HEADERS)
    finally:
        pipeline._ACTIVE_CALLS.discard("CALL-REP-01")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "ALREADY_PROCESSING"


# --- Authorization: reprocessing is a manager / head action -------------------

def _processing_must_not_run(monkeypatch):
    from app.services import pipeline

    def boom(*a, **k):
        raise AssertionError("pipeline ran for a forbidden request")

    monkeypatch.setattr(pipeline, "process_call", boom)


def test_caller_cannot_reprocess_even_their_own_call(client, db_session, tmp_path, monkeypatch):
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    monkeypatch.setattr(settings, "audio_dir", str(audio_dir))
    setup_queued_call(db_session, audio_dir, monkeypatch)
    _processing_must_not_run(monkeypatch)

    resp = client.post("/api/calls/CALL-REP-01/reprocess", headers=CALLER1_HEADERS)

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "REPROCESS_FORBIDDEN"
    db_session.expire_all()
    from app.models import Call

    assert db_session.get(Call, "CALL-REP-01").status == "queued"  # untouched


def test_caller_reprocessing_someone_elses_call_is_404_not_403(client, db_session, tmp_path, monkeypatch):
    from tests.conftest import CALLER3_HEADERS

    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    monkeypatch.setattr(settings, "audio_dir", str(audio_dir))
    setup_queued_call(db_session, audio_dir, monkeypatch)
    _processing_must_not_run(monkeypatch)

    resp = client.post("/api/calls/CALL-REP-01/reprocess", headers=CALLER3_HEADERS)
    assert resp.status_code == 404  # existence is not revealed to a caller outside the scope


def test_manager_of_the_team_and_head_may_reprocess(client, db_session, tmp_path, monkeypatch):
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    monkeypatch.setattr(settings, "audio_dir", str(audio_dir))
    setup_queued_call(db_session, audio_dir, monkeypatch)

    assert client.post("/api/calls/CALL-REP-01/reprocess", headers=MGR1_HEADERS).status_code == 200
    assert client.post("/api/calls/CALL-REP-01/reprocess", headers=HEAD_HEADERS).status_code == 200


def test_reprocess_response_never_exposes_provider_internals(client, db_session, tmp_path, monkeypatch):
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    monkeypatch.setattr(settings, "audio_dir", str(audio_dir))
    call = setup_queued_call(db_session, audio_dir, monkeypatch)

    def leaky(audio_bytes, filename, sleep_fn=None):
        raise transcription_service.TranscriptionFailedError(
            "Transcription failed: org_01abcdefg gsk_ABCDEFGH12345678 https://console.example.com/billing"
        )

    monkeypatch.setattr(transcription_service, "transcribe", leaky)
    body = client.post("/api/calls/CALL-REP-01/reprocess", headers=HEAD_HEADERS).json()
    detail = client.get("/api/calls/CALL-REP-01", headers=HEAD_HEADERS).json()

    for payload in (body["failure_reason"], detail["failure_reason"]):
        assert "org_01abcdefg" not in payload and "gsk_" not in payload and "console.example.com" not in payload
    assert body["status"] == "failed"
