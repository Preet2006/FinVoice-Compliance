from datetime import datetime

import pytest

from app.config import settings
from app.models import Call
from tests.conftest import HEAD_HEADERS, MGR1_HEADERS, CALLER1_HEADERS, CALLER3_HEADERS

AUDIO_BYTES = b"RIFF" + b"\x00" * 1020  # 1024 bytes, enough for range tests


@pytest.fixture()
def scratch_audio_dir(tmp_path, monkeypatch):
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    monkeypatch.setattr(settings, "audio_dir", str(audio_dir))
    return audio_dir


def test_full_file_returned_with_accept_ranges_header(client, db_session, scratch_audio_dir):
    (scratch_audio_dir / "c3.wav").write_bytes(AUDIO_BYTES)

    # Point CALL-C3-01 (has_audio=True in the fixture) at our scratch file.
    call = db_session.query(Call).filter_by(id="CALL-C3-01").first()
    call.audio_path = "data/audio/c3.wav"
    db_session.commit()

    resp = client.get("/api/media/CALL-C3-01", headers=CALLER3_HEADERS)
    assert resp.status_code == 200
    assert resp.headers["accept-ranges"] == "bytes"
    assert resp.content == AUDIO_BYTES


def test_range_request_returns_206_partial_content(client, db_session, scratch_audio_dir):
    (scratch_audio_dir / "c3.wav").write_bytes(AUDIO_BYTES)
    call = db_session.query(Call).filter_by(id="CALL-C3-01").first()
    call.audio_path = "data/audio/c3.wav"
    db_session.commit()

    resp = client.get(
        "/api/media/CALL-C3-01",
        headers={**CALLER3_HEADERS, "Range": "bytes=10-19"},
    )
    assert resp.status_code == 206
    assert resp.content == AUDIO_BYTES[10:20]
    assert resp.headers["content-range"] == f"bytes 10-19/{len(AUDIO_BYTES)}"


def test_open_ended_range_returns_rest_of_file(client, db_session, scratch_audio_dir):
    (scratch_audio_dir / "c3.wav").write_bytes(AUDIO_BYTES)
    call = db_session.query(Call).filter_by(id="CALL-C3-01").first()
    call.audio_path = "data/audio/c3.wav"
    db_session.commit()

    resp = client.get("/api/media/CALL-C3-01", headers={**CALLER3_HEADERS, "Range": "bytes=1000-"})
    assert resp.status_code == 206
    assert resp.content == AUDIO_BYTES[1000:]


def test_out_of_bounds_range_returns_416(client, db_session, scratch_audio_dir):
    (scratch_audio_dir / "c3.wav").write_bytes(AUDIO_BYTES)
    call = db_session.query(Call).filter_by(id="CALL-C3-01").first()
    call.audio_path = "data/audio/c3.wav"
    db_session.commit()

    resp = client.get(
        "/api/media/CALL-C3-01",
        headers={**CALLER3_HEADERS, "Range": "bytes=99999-100010"},
    )
    assert resp.status_code == 416


def test_caller_cannot_access_another_callers_media(client, db_session, scratch_audio_dir):
    (scratch_audio_dir / "c3.wav").write_bytes(AUDIO_BYTES)
    call = db_session.query(Call).filter_by(id="CALL-C3-01").first()
    call.audio_path = "data/audio/c3.wav"
    db_session.commit()

    resp = client.get("/api/media/CALL-C3-01", headers=CALLER1_HEADERS)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "MEDIA_NOT_FOUND"


def test_manager_outside_team_gets_404(client, db_session, scratch_audio_dir):
    (scratch_audio_dir / "c3.wav").write_bytes(AUDIO_BYTES)
    call = db_session.query(Call).filter_by(id="CALL-C3-01").first()
    call.audio_path = "data/audio/c3.wav"
    db_session.commit()

    resp = client.get("/api/media/CALL-C3-01", headers=MGR1_HEADERS)
    assert resp.status_code == 404


def test_nonexistent_call_404(client, db_session):
    resp = client.get("/api/media/CALL-DOES-NOT-EXIST", headers=HEAD_HEADERS)
    assert resp.status_code == 404


def test_metadata_only_call_has_no_media(client, db_session):
    # CALL-C1-01 in the fixture DB has has_audio=False.
    resp = client.get("/api/media/CALL-C1-01", headers=HEAD_HEADERS)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "MEDIA_NOT_FOUND"


def test_audio_file_missing_on_disk_returns_404(client, db_session, scratch_audio_dir):
    # audio_path points somewhere, but the file was never actually written.
    call = db_session.query(Call).filter_by(id="CALL-C3-01").first()
    call.audio_path = "data/audio/does_not_exist.wav"
    db_session.commit()

    resp = client.get("/api/media/CALL-C3-01", headers=CALLER3_HEADERS)
    assert resp.status_code == 404


def test_path_traversal_in_audio_path_is_rejected(client, db_session, scratch_audio_dir, tmp_path):
    # Write a secret file OUTSIDE the configured audio_dir...
    secret = tmp_path / "secret.wav"
    secret.write_bytes(b"TOP SECRET")
    # ...and simulate a corrupted/malicious audio_path trying to reach it.
    call = db_session.query(Call).filter_by(id="CALL-C3-01").first()
    call.audio_path = "data/audio/../secret.wav"
    db_session.commit()

    resp = client.get("/api/media/CALL-C3-01", headers=CALLER3_HEADERS)
    # Path.name strips directory components, so this resolves to
    # "secret.wav" inside audio_dir (missing) rather than escaping it --
    # either way, the secret file outside audio_dir must never be served.
    assert resp.status_code == 404
