from datetime import datetime

from app.rules import check_r1_violation


def at(hour, minute=0, second=0):
    return datetime(2026, 10, 1, hour, minute, second)


def test_before_8am_is_violation():
    assert check_r1_violation(at(7, 59)) is not None


def test_exactly_8am_is_not_violation():
    assert check_r1_violation(at(8, 0, 0)) is None


def test_exactly_7pm_is_violation():
    # "between 8 AM and 7 PM" interpreted as [08:00, 19:00) — 19:00:00 itself
    # is a violation (documented ASSUMPTION in rules.py).
    assert check_r1_violation(at(19, 0, 0)) is not None


def test_one_second_before_7pm_is_not_violation():
    assert check_r1_violation(at(18, 59, 59)) is None


def test_mid_afternoon_is_not_violation():
    assert check_r1_violation(at(14, 30)) is None


def test_late_night_is_violation():
    assert check_r1_violation(at(22, 0)) is not None


def test_violation_shape():
    v = check_r1_violation(at(20, 0))
    assert v["rule_code"] == "R1"
    assert v["severity"] == "high"
    assert v["segment_id"] is None
    assert v["confidence"] == 1.0
    assert v["start_time_seconds"] == 0.0
