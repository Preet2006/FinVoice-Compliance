from datetime import date, datetime, timedelta, timezone

import pytest

from app.rules import IST, R1_OK, R1_UNRESOLVED, R1_VIOLATION, check_r1_violation, evaluate_r1


def at(hour, minute=0, second=0, microsecond=0):
    return datetime(2026, 10, 4, hour, minute, second, microsecond)


# --- the boundary interpretation: permitted window is [08:00:00, 19:00:00) --------


@pytest.mark.parametrize(
    "when, expected",
    [
        (at(0, 0), R1_VIOLATION),
        (at(7, 59, 59, 999999), R1_VIOLATION),
        (at(8, 0, 0), R1_OK),  # exactly 8 AM is permitted
        (at(8, 0, 1), R1_OK),
        (at(12, 0), R1_OK),
        (at(18, 59, 59, 999999), R1_OK),
        (at(19, 0, 0), R1_VIOLATION),  # exactly 7 PM is NOT permitted
        (at(19, 0, 1), R1_VIOLATION),
        (at(23, 59, 59), R1_VIOLATION),
    ],
)
def test_boundaries(when, expected):
    assert evaluate_r1(when).status == expected


def test_the_stored_database_artifact_times_are_ordinary_clock_times():
    # Real stored values from the seed script (with their stray seconds and
    # microseconds) are judged by the clock time like any other.
    assert evaluate_r1(datetime(2026, 9, 25, 8, 32, 16, 155843)).status == R1_OK
    assert evaluate_r1(datetime(2026, 10, 19, 21, 57, 16, 155843)).status == R1_VIOLATION


# --- timezone handling -------------------------------------------------------------


def test_a_naive_time_is_taken_to_be_ist():
    assert evaluate_r1(at(7, 30)).status == R1_VIOLATION
    assert evaluate_r1(at(9, 30)).status == R1_OK


@pytest.mark.parametrize(
    "aware, expected, ist_clock",
    [
        (datetime(2026, 10, 4, 2, 0, tzinfo=timezone.utc), R1_VIOLATION, "07:30:00"),  # 07:30 IST
        (datetime(2026, 10, 4, 4, 0, tzinfo=timezone.utc), R1_OK, "09:30:00"),  # 09:30 IST
        (datetime(2026, 10, 4, 13, 30, tzinfo=timezone.utc), R1_VIOLATION, "19:00:00"),  # exactly 19:00 IST
        (datetime(2026, 10, 4, 13, 29, 59, tzinfo=timezone.utc), R1_OK, "18:59:59"),
        (datetime(2026, 10, 4, 7, 30, tzinfo=IST), R1_VIOLATION, "07:30:00"),  # already IST
        (datetime(2026, 10, 3, 21, 0, tzinfo=timezone(timedelta(hours=-5))), R1_VIOLATION, "07:30:00"),  # 02:00 UTC next day
    ],
)
def test_an_offset_is_converted_to_ist_before_the_check(aware, expected, ist_clock):
    result = evaluate_r1(aware)
    assert result.status == expected
    assert result.local_time.strftime("%H:%M:%S") == ist_clock


def test_the_same_instant_gives_the_same_answer_whatever_offset_it_is_written_in():
    instant = datetime(2026, 10, 4, 2, 0, tzinfo=timezone.utc)
    for offset in (timedelta(0), timedelta(hours=5, minutes=30), timedelta(hours=-8), timedelta(hours=9)):
        assert evaluate_r1(instant.astimezone(timezone(offset))).status == R1_VIOLATION


# --- missing or unusable start times produce an explicit, safe result ------------------


@pytest.mark.parametrize("bad", [None, "2026-10-04T07:30:00", date(2026, 10, 4), 12.5, 0, ""])
def test_a_missing_or_non_datetime_start_time_is_unresolved_not_a_pass_or_a_violation(bad):
    result = evaluate_r1(bad)
    assert result.status == R1_UNRESOLVED and result.violation is None and result.reason
    assert check_r1_violation(bad) is None


# --- the evidence ------------------------------------------------------------------------


def test_violation_carries_rule_severity_timestamp_and_no_segment():
    v = evaluate_r1(at(7, 30)).violation
    assert v["rule_code"] == "R1" and v["severity"] == "high"
    assert v["start_time_seconds"] == 0.0 and v["segment_id"] is None and v["confidence"] == 1.0
    assert v["quote"] == "Call started at 07:30:00 IST (outside permitted hours 08:00-19:00)"


def test_the_evidence_quotes_the_ist_clock_time_for_an_offset_timestamp():
    v = evaluate_r1(datetime(2026, 10, 4, 14, 0, tzinfo=timezone.utc)).violation  # 19:30 IST
    assert "19:30:00 IST" in v["quote"]


def test_no_violation_object_when_within_hours():
    assert evaluate_r1(at(10, 0)).violation is None and check_r1_violation(at(10, 0)) is None


# --- the production discrepancy, as data ---------------------------------------------------


@pytest.mark.parametrize(
    "call, csv_time, stored_time, csv_expected, stored_expected",
    [
        ("CALL-0008", datetime(2026, 10, 4, 7, 30), datetime(2026, 9, 25, 8, 32, 16, 155843), R1_VIOLATION, R1_OK),
        ("CALL-0020", datetime(2026, 10, 10, 16, 50), datetime(2026, 10, 19, 21, 57, 16, 155843), R1_OK, R1_VIOLATION),
        ("CALL-0012", datetime(2026, 10, 6, 19, 15), datetime(2026, 10, 3, 19, 40, 16, 155843), R1_VIOLATION, R1_VIOLATION),
        ("CALL-0016", datetime(2026, 10, 8, 20, 45), datetime(2026, 10, 11, 19, 53, 16, 155843), R1_VIOLATION, R1_VIOLATION),
    ],
)
def test_verified_csv_versus_stored_time_outcomes(call, csv_time, stored_time, csv_expected, stored_expected):
    assert evaluate_r1(csv_time).status == csv_expected
    assert evaluate_r1(stored_time).status == stored_expected


def test_a_coincidentally_right_verdict_still_quotes_the_wrong_time():
    # Calls 12 and 16: both times violate R1, but the evidence shown would name
    # the stored (wrong) clock time, not the time in the metadata.
    right = evaluate_r1(datetime(2026, 10, 6, 19, 15)).violation["quote"]
    shown = evaluate_r1(datetime(2026, 10, 3, 19, 40, 16, 155843)).violation["quote"]
    assert "19:15:00" in right and "19:40:16" in shown and right != shown
