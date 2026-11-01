import io
import wave

import pytest

from app.services.upload_service import (
    CsvFormatError,
    parse_metadata_csv,
    sanitize_filename,
    sniff_audio_content,
    wav_duration_seconds,
)


def make_wav_bytes(duration_seconds: float = 1.0, framerate: int = 8000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(framerate)
        n_frames = int(duration_seconds * framerate)
        wf.writeframes(b"\x00\x00" * n_frames)
    return buf.getvalue()


# --- sanitize_filename --------------------------------------------------

def test_sanitize_filename_accepts_normal_name():
    assert sanitize_filename("call_01.wav") == "call_01.wav"


@pytest.mark.parametrize(
    "bad_name",
    [
        "../call_01.wav",
        "../../etc/passwd.wav",
        "sub/call_01.wav",
        "sub\\call_01.wav",
        "C:\\Windows\\call_01.wav",
        "..",
        "",
        ".hidden.wav",
        "call 01.wav",  # space not in allow-list
        "call_01.exe",
        "call_01",  # no extension
        "call_01.WAV.exe",
    ],
)
def test_sanitize_filename_rejects_unsafe_or_unsupported(bad_name):
    assert sanitize_filename(bad_name) is None


def test_sanitize_filename_accepts_mp3():
    assert sanitize_filename("call_02.mp3") == "call_02.mp3"


# --- sniff_audio_content -------------------------------------------------

def test_sniff_audio_content_accepts_real_wav_bytes():
    assert sniff_audio_content("x.wav", make_wav_bytes()) is True


def test_sniff_audio_content_rejects_text_disguised_as_wav():
    assert sniff_audio_content("x.wav", b"not actually audio data at all") is False


def test_sniff_audio_content_accepts_id3_mp3():
    assert sniff_audio_content("x.mp3", b"ID3" + b"\x00" * 20) is True


def test_sniff_audio_content_rejects_empty_mp3():
    assert sniff_audio_content("x.mp3", b"") is False


# --- wav_duration_seconds --------------------------------------------------

def test_wav_duration_seconds_matches_real_header():
    content = make_wav_bytes(duration_seconds=2.0, framerate=8000)
    assert wav_duration_seconds(content) == 2


def test_wav_duration_seconds_none_for_garbage():
    assert wav_duration_seconds(b"not a wav file") is None


# --- parse_metadata_csv ----------------------------------------------------

VALID_HEADER = "call_id,filename,caller_id,office_id,account_type,start_time,language\n"


def test_parse_metadata_csv_happy_path():
    csv_bytes = (
        VALID_HEADER + "CALL-X01,call_x01.wav,u-caller1,o-01,personal_loan,2026-10-01T10:00:00,hi\n"
    ).encode("utf-8")
    rows = parse_metadata_csv(csv_bytes)
    assert len(rows) == 1
    assert rows[0].call_id == "CALL-X01"
    assert rows[0].filename == "call_x01.wav"
    assert rows[0].start_time is not None


def test_parse_metadata_csv_missing_column_raises():
    csv_bytes = b"call_id,caller_id,office_id,account_type,start_time,language\n"
    with pytest.raises(CsvFormatError):
        parse_metadata_csv(csv_bytes)


def test_parse_metadata_csv_bad_timestamp_leaves_start_time_none():
    csv_bytes = (
        VALID_HEADER + "CALL-X02,call_x02.wav,u-caller1,o-01,personal_loan,not-a-date,hi\n"
    ).encode("utf-8")
    rows = parse_metadata_csv(csv_bytes)
    assert rows[0].start_time is None
