"""
Audit of the metadata-only calls: can their stored R1 results be explained as a
seeding artifact? The synthetic rows are produced with the OLD seeder arithmetic
(intended time of day ADDED to the seeding clock), and the function must also
refuse to explain data that did not come from it.
"""

import random
from datetime import datetime, timedelta

import pytest

from app.services import metadata_audit as ma


def old_seeder_rows(n=1980, clock=datetime(2026, 10, 29, 0, 40, 16, 20495), seed=1):
    rng = random.Random(seed)
    base = clock - timedelta(days=90)
    rows = []
    for _ in range(n):
        stored = base + timedelta(days=rng.randint(0, 90), hours=rng.randint(8, 18), minutes=rng.randint(0, 59), seconds=rng.randint(0, 59))
        rows.append({"start_time": str(stored)})
    return rows


def test_the_old_arithmetic_is_recognised_and_its_violations_are_called_artifacts():
    rows = old_seeder_rows()
    result = ma.audit_metadata_only(rows)
    stored_violations = sum(1 for r in rows if datetime.fromisoformat(r["start_time"]).hour >= 19)
    assert stored_violations > 0  # the artifact produces apparent violations
    assert result["explanation_holds"] is True and result["distinct_microsecond_values"] == 1
    assert result["originally_in_permitted_hours"] == 1980
    assert result["stored_r1_violations_explained_as_artifact"] == result["r1_as_stored"]["violation"] == stored_violations


def test_the_window_for_the_clock_offset_contains_the_true_offset_and_is_narrow():
    clock = datetime(2026, 10, 29, 0, 40, 16, 20495)
    result = ma.audit_metadata_only(old_seeder_rows(clock=clock))
    low, high = (timedelta(seconds=0),) * 2
    def parse(text):
        h, m, rest = text.split(":")
        sec, _, micro = rest.partition(".")
        return timedelta(hours=int(h), minutes=int(m), seconds=int(sec), microseconds=int((micro or "0").ljust(6, "0")))
    low, high = (parse(x) for x in result["clock_offset_bounds"])
    true = timedelta(minutes=40, seconds=16, microseconds=20495)
    assert low <= true <= high and result["clock_offset_window_seconds"] <= 60


@pytest.mark.parametrize("clock", [datetime(2026, 10, 29, 0, 40, 16, 20495), datetime(2026, 10, 29, 1, 15, 3, 999), datetime(2026, 10, 29, 3, 59, 59, 500000), datetime(2026, 10, 29, 0, 0, 0, 1)])
def test_the_conclusion_does_not_depend_on_what_time_the_seeder_ran(clock):
    result = ma.audit_metadata_only(old_seeder_rows(clock=clock))
    assert result["explanation_holds"] is True and result["originally_in_permitted_hours"] == 1980


def test_genuine_times_that_are_not_shifted_are_not_explained_away():
    # Real out-of-hours calls: starts at 06:30, 21:15 and 23:00 cannot come from the artifact.
    rows = [{"start_time": f"2026-10-0{d} {h:02d}:{m:02d}:00.000000"} for d, (h, m) in enumerate([(6, 30), (21, 15), (23, 0), (10, 0), (14, 30)], start=1)]
    result = ma.audit_metadata_only(rows)
    assert result["explanation_holds"] is False
    assert result["stored_r1_violations_explained_as_artifact"] if "stored_r1_violations_explained_as_artifact" in result else True


def test_rows_from_more_than_one_clock_capture_are_not_explained():
    rows = old_seeder_rows(100) + old_seeder_rows(100, clock=datetime(2026, 10, 29, 0, 40, 16, 777777), seed=2)
    result = ma.audit_metadata_only(rows)
    assert result["distinct_microsecond_values"] == 2 and result["explanation_holds"] is False


def test_unreadable_times_are_counted_not_guessed():
    rows = old_seeder_rows(50) + [{"start_time": "garbage"}, {"start_time": None}]
    result = ma.audit_metadata_only(rows)
    assert result["unreadable_start_times"] == 2 and result["calls"] == 52


def test_no_rows_makes_no_claim():
    result = ma.audit_metadata_only([])
    assert result["calls"] == 0 and result["explanation_holds"] is False


def test_the_audit_only_reports_it_never_returns_replacement_times():
    result = ma.audit_metadata_only(old_seeder_rows())
    assert not any("replacement" in k or "proposed" in k or "corrected" in k for k in result)


def test_a_seeding_clock_late_enough_to_roll_times_past_midnight_is_not_explained():
    # After about 05:00 the old arithmetic pushes some times into the next day,
    # which this audit does not try to untangle: it makes no claim rather than guess.
    result = ma.audit_metadata_only(old_seeder_rows(clock=datetime(2026, 10, 29, 9, 30, 0, 1)))
    assert result["explanation_holds"] is False
