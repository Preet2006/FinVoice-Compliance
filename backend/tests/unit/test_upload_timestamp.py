from datetime import datetime

import pytest

from app.rules import R1_OK, R1_VIOLATION, evaluate_r1
from app.services.upload_service import _try_parse_timestamp as parse


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("2026-10-04T07:30:00", datetime(2026, 10, 4, 7, 30)),
        ("2026-10-04T07:30", datetime(2026, 10, 4, 7, 30)),
        ("2026-10-04 07:30:00", datetime(2026, 10, 4, 7, 30)),
        ("2026-10-04 07:30", datetime(2026, 10, 4, 7, 30)),
        ("  2026-10-04T16:50:00  ", datetime(2026, 10, 4, 16, 50)),
        ("2026-10-04T16:50:00.250", datetime(2026, 10, 4, 16, 50, 0, 250000)),
    ],
)
def test_full_naive_timestamps_are_accepted_as_ist_wall_clock(raw, expected):
    assert parse(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("2026-10-04T02:00:00Z", datetime(2026, 10, 4, 7, 30)),
        ("2026-10-04T07:30:00+05:30", datetime(2026, 10, 4, 7, 30)),
        ("2026-10-04T07:30:00+0530", datetime(2026, 10, 4, 7, 30)),
        ("2026-10-04T13:30:00+00:00", datetime(2026, 10, 4, 19, 0)),
        ("2026-10-03T21:00:00-05:00", datetime(2026, 10, 4, 7, 30)),
    ],
)
def test_a_utc_offset_is_converted_to_ist_and_returned_naive(raw, expected):
    result = parse(raw)
    assert result == expected and result.tzinfo is None


@pytest.mark.parametrize(
    "raw",
    [
        "2026-10-04",  # a bare date would silently become 00:00
        "2026-10-04T",
        "04/10/2026 07:30",
        "10-04-2026 07:30",
        "7:30",
        "2026-10-04T7:30",
        "2026-10-04T25:00:00",
        "2026-13-04T10:00:00",
        "2026-02-30T10:00:00",
        "2026-10-04T07:30:00 IST",
        "garbage",
        "",
        "   ",
        None,
    ],
)
def test_incomplete_or_unreadable_timestamps_are_rejected_not_guessed(raw):
    assert parse(raw) is None


def test_a_bare_date_is_not_turned_into_a_fabricated_midnight_violation():
    assert parse("2026-10-04") is None  # previously datetime(2026, 10, 4, 0, 0) -> R1 "violation" at 00:00


def test_an_offset_timestamp_gets_the_ist_clock_time_r1_will_judge():
    # 04:00 UTC is 09:30 IST (in hours). Without conversion the stored clock
    # reading 04:00 would have been reported as an out-of-hours call.
    assert evaluate_r1(parse("2026-10-04T04:00:00Z")).status == R1_OK
    # 14:00 UTC is 19:30 IST (out of hours). Without conversion 14:00 passed.
    assert evaluate_r1(parse("2026-10-04T14:00:00Z")).status == R1_VIOLATION


def test_batch_rejects_a_date_only_row_as_an_invalid_timestamp(db_session, tmp_path):
    from app.services import upload_service

    csv_bytes = (
        "call_id,filename,caller_id,office_id,account_type,start_time,language\n"
        "CALL-TS-01,a.wav,u-caller1,o-01,personal_loan,2026-10-04,hi\n"
    ).encode()
    result = upload_service.process_batch(
        db=db_session, csv_bytes=csv_bytes, audio_files=[("a.wav", b"RIFF....WAVEfmt ")],
        audio_dir=tmp_path, max_upload_size_bytes=10_000_000,
    )
    assert ("CALL-TS-01", "invalid_timestamp") in [(r.identifier, r.reason) for r in result.rejected]
    assert result.accepted == []  # nothing was created from the bad row
