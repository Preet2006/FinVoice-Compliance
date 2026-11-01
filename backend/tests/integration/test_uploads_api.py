import io
import wave

import pytest

from app.config import settings
from app.models import Call
from tests.conftest import HEAD_HEADERS, MGR1_HEADERS, MGR2_HEADERS, CALLER1_HEADERS, CALLER3_HEADERS

VALID_HEADER = "call_id,filename,caller_id,office_id,account_type,start_time,language\n"


def make_wav_bytes(duration_seconds: float = 1.0, framerate: int = 8000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(framerate)
        wf.writeframes(b"\x00\x00" * int(duration_seconds * framerate))
    return buf.getvalue()


@pytest.fixture()
def scratch_audio_dir(tmp_path, monkeypatch):
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    monkeypatch.setattr(settings, "audio_dir", str(audio_dir))
    return audio_dir


def upload(client, headers, csv_text, files):
    """files: list of (filename, content_bytes)"""
    multipart_files = [("audio_files", (name, content, "audio/wav")) for name, content in files]
    multipart_files.append(("metadata_csv", ("metadata.csv", csv_text.encode("utf-8"), "text/csv")))
    return client.post("/api/uploads/batch", headers=headers, files=multipart_files)


def test_successful_upload_and_ingestion(client, scratch_audio_dir):
    wav = make_wav_bytes()
    csv_text = VALID_HEADER + (
        "CALL-UP01,up01.wav,u-caller1,o-01,personal_loan,2026-10-15T10:00:00,hi\n"
    )
    resp = upload(client, HEAD_HEADERS, csv_text, [("up01.wav", wav)])
    assert resp.status_code == 202
    body = resp.json()
    assert len(body["accepted"]) == 1
    assert body["accepted"][0]["call_id"] == "CALL-UP01"
    assert body["rejected"] == []
    assert (scratch_audio_dir / "up01.wav").exists()

    # Call row actually exists and is visible.
    resp2 = client.get("/api/calls/CALL-UP01", headers=HEAD_HEADERS)
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "queued"
    assert resp2.json()["duration_seconds"] == 1


def test_malformed_file_disguised_as_wav_rejected(client, scratch_audio_dir):
    csv_text = VALID_HEADER + (
        "CALL-UP02,up02.wav,u-caller1,o-01,personal_loan,2026-10-15T10:00:00,hi\n"
    )
    resp = upload(client, HEAD_HEADERS, csv_text, [("up02.wav", b"this is not audio data")])
    assert resp.status_code == 202
    body = resp.json()
    assert body["accepted"] == []
    reasons = {r["reason"] for r in body["rejected"]}
    assert "invalid_audio_content" in reasons


def test_unsupported_extension_rejected(client, scratch_audio_dir):
    csv_text = VALID_HEADER + (
        "CALL-UP03,up03.exe,u-caller1,o-01,personal_loan,2026-10-15T10:00:00,hi\n"
    )
    resp = upload(client, HEAD_HEADERS, csv_text, [("up03.exe", make_wav_bytes())])
    body = resp.json()
    assert body["accepted"] == []
    reasons = {r["reason"] for r in body["rejected"]}
    assert "unsafe_or_unsupported_filename" in reasons


def test_missing_csv_column_rejects_whole_batch(client, scratch_audio_dir):
    bad_csv = "call_id,caller_id,office_id,account_type,start_time,language\nCALL-X,u-caller1,o-01,personal_loan,2026-10-15T10:00:00,hi\n"
    resp = upload(client, HEAD_HEADERS, bad_csv, [("up04.wav", make_wav_bytes())])
    body = resp.json()
    assert body["accepted"] == []
    assert body["rejected"][0]["reason"] == "invalid_csv"


def test_bad_timestamp_row_rejected_others_proceed(client, scratch_audio_dir):
    csv_text = (
        VALID_HEADER
        + "CALL-UP05,up05.wav,u-caller1,o-01,personal_loan,not-a-date,hi\n"
        + "CALL-UP06,up06.wav,u-caller1,o-01,personal_loan,2026-10-15T10:00:00,hi\n"
    )
    resp = upload(
        client,
        HEAD_HEADERS,
        csv_text,
        [("up05.wav", make_wav_bytes()), ("up06.wav", make_wav_bytes())],
    )
    body = resp.json()
    accepted_ids = {a["call_id"] for a in body["accepted"]}
    assert accepted_ids == {"CALL-UP06"}
    rejected = {r["identifier"]: r["reason"] for r in body["rejected"]}
    assert rejected["CALL-UP05"] == "invalid_timestamp"


def test_unknown_office_and_caller_rejected(client, scratch_audio_dir):
    csv_text = (
        VALID_HEADER
        + "CALL-UP07,up07.wav,u-ghost,o-01,personal_loan,2026-10-15T10:00:00,hi\n"
        + "CALL-UP08,up08.wav,u-caller1,o-ghost,personal_loan,2026-10-15T10:00:00,hi\n"
    )
    resp = upload(
        client,
        HEAD_HEADERS,
        csv_text,
        [("up07.wav", make_wav_bytes()), ("up08.wav", make_wav_bytes())],
    )
    body = resp.json()
    assert body["accepted"] == []
    rejected = {r["identifier"]: r["reason"] for r in body["rejected"]}
    assert rejected["CALL-UP07"] == "unknown_caller_id"
    assert rejected["CALL-UP08"] == "unknown_office_id"


def test_duplicate_call_id_already_in_db_rejected(client, scratch_audio_dir):
    # CALL-C1-01 already exists in the fixture DB (see tests/conftest.py).
    csv_text = VALID_HEADER + (
        "CALL-C1-01,dupe.wav,u-caller1,o-01,personal_loan,2026-10-15T10:00:00,hi\n"
    )
    resp = upload(client, HEAD_HEADERS, csv_text, [("dupe.wav", make_wav_bytes())])
    body = resp.json()
    assert body["accepted"] == []
    assert body["rejected"][0]["reason"] == "duplicate_call_id"


def test_duplicate_call_id_within_csv_rejected(client, scratch_audio_dir):
    csv_text = (
        VALID_HEADER
        + "CALL-UP09,up09.wav,u-caller1,o-01,personal_loan,2026-10-15T10:00:00,hi\n"
        + "CALL-UP09,up09b.wav,u-caller1,o-01,personal_loan,2026-10-16T10:00:00,hi\n"
    )
    resp = upload(
        client,
        HEAD_HEADERS,
        csv_text,
        [("up09.wav", make_wav_bytes()), ("up09b.wav", make_wav_bytes())],
    )
    body = resp.json()
    assert len(body["accepted"]) == 1
    reasons = [r["reason"] for r in body["rejected"]]
    assert "duplicate_call_id_in_csv" in reasons


def test_duplicate_filename_in_batch_rejected(client, scratch_audio_dir):
    csv_text = VALID_HEADER + (
        "CALL-UP10,same.wav,u-caller1,o-01,personal_loan,2026-10-15T10:00:00,hi\n"
    )
    resp = upload(
        client,
        HEAD_HEADERS,
        csv_text,
        [("same.wav", make_wav_bytes()), ("same.wav", make_wav_bytes(2.0))],
    )
    body = resp.json()
    reasons = [r["reason"] for r in body["rejected"]]
    assert "duplicate_filename_in_batch" in reasons
    assert len(body["accepted"]) == 1


def test_path_traversal_filename_rejected(client, scratch_audio_dir):
    csv_text = VALID_HEADER + (
        "CALL-UP11,../../evil.wav,u-caller1,o-01,personal_loan,2026-10-15T10:00:00,hi\n"
    )
    resp = upload(client, HEAD_HEADERS, csv_text, [("../../evil.wav", make_wav_bytes())])
    body = resp.json()
    assert body["accepted"] == []
    # Nothing escaped the scratch audio dir.
    assert not (scratch_audio_dir.parent.parent / "evil.wav").exists()


def test_reupload_matching_existing_filename_does_not_overwrite(client, scratch_audio_dir):
    (scratch_audio_dir / "existing.wav").write_bytes(b"ORIGINAL BYTES")
    csv_text = VALID_HEADER + (
        "CALL-UP12,existing.wav,u-caller1,o-01,personal_loan,2026-10-15T10:00:00,hi\n"
    )
    resp = upload(client, HEAD_HEADERS, csv_text, [("existing.wav", make_wav_bytes())])
    body = resp.json()
    assert body["accepted"] == []
    assert body["rejected"][0]["reason"] == "file_already_exists"
    assert (scratch_audio_dir / "existing.wav").read_bytes() == b"ORIGINAL BYTES"


def test_audio_file_with_no_matching_csv_row_rejected(client, scratch_audio_dir):
    csv_text = VALID_HEADER + (
        "CALL-UP13,up13.wav,u-caller1,o-01,personal_loan,2026-10-15T10:00:00,hi\n"
    )
    resp = upload(
        client,
        HEAD_HEADERS,
        csv_text,
        [("up13.wav", make_wav_bytes()), ("orphan.wav", make_wav_bytes())],
    )
    body = resp.json()
    accepted_ids = {a["call_id"] for a in body["accepted"]}
    assert accepted_ids == {"CALL-UP13"}
    rejected = {r["identifier"]: r["reason"] for r in body["rejected"]}
    assert rejected["orphan.wav"] == "no_matching_call_id_in_csv"


def test_upload_requires_valid_role_headers(client, scratch_audio_dir):
    csv_text = VALID_HEADER
    resp = client.post(
        "/api/uploads/batch",
        files=[("metadata_csv", ("m.csv", csv_text.encode(), "text/csv"))],
    )
    assert resp.status_code == 401


@pytest.mark.parametrize("headers,n", [(MGR1_HEADERS, 14), (HEAD_HEADERS, 15)])
def test_manager_and_head_can_upload(client, scratch_audio_dir, headers, n):
    csv_text = VALID_HEADER + f"CALL-UP{n},up{n}.wav,u-caller1,o-01,personal_loan,2026-10-15T10:00:00,hi\n"
    resp = upload(client, headers, csv_text, [(f"up{n}.wav", make_wav_bytes())])
    assert resp.status_code == 202
    assert len(resp.json()["accepted"]) == 1
    assert (scratch_audio_dir / f"up{n}.wav").is_file()


@pytest.mark.parametrize("headers", [CALLER1_HEADERS, CALLER3_HEADERS])
def test_caller_upload_is_refused_and_leaves_no_trace(client, scratch_audio_dir, db_session, headers):
    before = db_session.query(Call).count()
    csv_text = VALID_HEADER + "CALL-UP90,up90.wav,u-caller1,o-01,personal_loan,2026-10-15T10:00:00,hi\n"
    resp = upload(client, headers, csv_text, [("up90.wav", make_wav_bytes())])
    assert resp.status_code == 403 and resp.json()["error"]["code"] == "UPLOAD_FORBIDDEN"
    assert db_session.query(Call).count() == before and db_session.get(Call, "CALL-UP90") is None  # no call record
    assert list(scratch_audio_dir.iterdir()) == []  # no file saved


def test_caller_cannot_read_upload_progress(client, scratch_audio_dir):
    csv_text = VALID_HEADER + "CALL-UP91,up91.wav,u-caller1,o-01,personal_loan,2026-10-15T10:00:00,hi\n"
    batch_id = upload(client, MGR1_HEADERS, csv_text, [("up91.wav", make_wav_bytes())]).json()["batch_id"]
    assert client.get(f"/api/uploads/batch/{batch_id}", headers=CALLER1_HEADERS).status_code == 403
    assert client.get(f"/api/uploads/batch/{batch_id}", headers=MGR1_HEADERS).status_code == 200


def test_upload_requires_an_identity(client, scratch_audio_dir):
    csv_text = VALID_HEADER + "CALL-UP92,up92.wav,u-caller1,o-01,personal_loan,2026-10-15T10:00:00,hi\n"
    resp = upload(client, {}, csv_text, [("up92.wav", make_wav_bytes())])
    assert resp.status_code == 401 and list(scratch_audio_dir.iterdir()) == []


def test_oversized_file_rejected(client, scratch_audio_dir, monkeypatch):
    # Use a tiny limit rather than allocating a real 100MB payload.
    monkeypatch.setattr(settings, "max_upload_size_bytes", 100)
    csv_text = VALID_HEADER + (
        "CALL-UP16,up16.wav,u-caller1,o-01,personal_loan,2026-10-15T10:00:00,hi\n"
    )
    resp = upload(client, HEAD_HEADERS, csv_text, [("up16.wav", make_wav_bytes(duration_seconds=1.0))])
    body = resp.json()
    assert body["accepted"] == []
    assert body["rejected"][0]["reason"] == "file_too_large"


def test_batch_status_scoped_by_role(client, scratch_audio_dir):
    csv_text = VALID_HEADER + (
        "CALL-UP15,up15.wav,u-caller1,o-01,personal_loan,2026-10-15T10:00:00,hi\n"
    )
    resp = upload(client, HEAD_HEADERS, csv_text, [("up15.wav", make_wav_bytes())])
    batch_id = resp.json()["batch_id"]

    # Head can see it.
    resp2 = client.get(f"/api/uploads/batch/{batch_id}", headers=HEAD_HEADERS)
    assert resp2.status_code == 200
    assert resp2.json()["items"][0]["call_id"] == "CALL-UP15"

    # The manager of the uploaded call's caller can see it.
    assert client.get(f"/api/uploads/batch/{batch_id}", headers=MGR1_HEADERS).status_code == 200

    # A manager whose team does not own the uploaded call sees nothing -> 404
    # (this scoping check used a caller before callers were barred from upload endpoints).
    resp3 = client.get(f"/api/uploads/batch/{batch_id}", headers=MGR2_HEADERS)
    assert resp3.status_code == 404

    # A caller is refused outright.
    assert client.get(f"/api/uploads/batch/{batch_id}", headers=CALLER3_HEADERS).status_code == 403
