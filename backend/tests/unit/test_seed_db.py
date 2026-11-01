"""
Regression tests for the seeder's start times. They call the real seeding
functions against SCRATCH in-memory databases, with fake clocks. The seeder's
main(), which wipes a database, is never called, and the production database
is never opened.
"""

import hashlib
import importlib.util
import random
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Call
from app.rules import evaluate_r1
from app.services.metadata_audit import load_csv_rows
from app.services.upload_service import _try_parse_timestamp

BACKEND = Path(__file__).resolve().parent.parent.parent
REAL_CSV = BACKEND / "data" / "metadata.csv"


def load_seeder():
    spec = importlib.util.spec_from_file_location("seed_db_under_test", BACKEND / "scripts" / "seed_db.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # defines functions only: main() is behind __main__
    return module


seed = load_seeder()


@pytest.fixture()
def scratch():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    offices = seed.seed_offices(db)
    users, _ = seed.seed_users(db, offices)
    callers = [u for u in users if u.role == "caller"]
    yield db, callers, offices
    db.close()
    engine.dispose()


def fake_clock(monkeypatch, moment):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(moment.year, moment.month, moment.day, moment.hour, moment.minute, moment.second, moment.microsecond)

    monkeypatch.setattr(seed, "datetime", Clock)


def gt_calls(db):
    return {c.id: c for c in db.query(Call).filter(Call.is_ground_truth.is_(True)).all()}


# --- the helper that fixes the root cause ---------------------------------------------------------------


@pytest.mark.parametrize("clock", [datetime(2026, 10, 29, 0, 40, 16, 155843), datetime(2026, 10, 29, 23, 59, 59), datetime(2026, 10, 29, 12, 0, 0)])
def test_the_time_of_day_is_set_not_added_to_the_seeding_clock(clock):
    when = seed.at_time_of_day(clock, -30, 10, 30)
    assert (when.hour, when.minute, when.second, when.microsecond) == (10, 30, 0, 0)  # NOT 11:10:16.155843
    assert when.date() == (clock + timedelta(days=-30)).date()


def test_the_original_bug_is_what_the_helper_no_longer_does():
    clock = datetime(2026, 10, 29, 0, 40, 16, 155843)
    buggy = clock + timedelta(days=-30, hours=10, minutes=30)  # the old arithmetic
    assert buggy.time().isoformat() == "11:10:16.155843"  # the value found in production
    assert seed.at_time_of_day(clock, -30, 10, 30).time().isoformat() == "10:30:00"


# --- the 20 ground-truth calls take their validated CSV timestamps -------------------------------------------------


def test_every_ground_truth_call_uses_its_csv_timestamp_exactly(scratch):
    db, callers, offices = scratch
    seed.seed_ground_truth_calls(db, callers, offices)
    calls = gt_calls(db)
    csv_times = {r["call_id"]: _try_parse_timestamp(r["start_time"]) for r in load_csv_rows(REAL_CSV)}
    assert len(calls) == 20 and set(calls) == {f"CALL-{n:04d}" for n in range(1, 21)}
    assert {i: c.start_time for i, c in calls.items()} == csv_times


def test_csv_times_give_the_expected_r1_results_for_the_role_play_calls(scratch):
    db, callers, offices = scratch
    seed.seed_ground_truth_calls(db, callers, offices)
    violating = {i for i, c in gt_calls(db).items() if evaluate_r1(c.start_time).status == "violation"}
    # The approved simulated scenario times: only CALL-0008 (07:30) and CALL-0018 (20:30) are out of hours.
    # (Before the scenario times were synchronised this set was 0008, 0012 and 0016, from placeholder times.)
    assert violating == {"CALL-0008", "CALL-0018"}


def test_the_seeding_clock_does_not_affect_ground_truth_times(scratch, monkeypatch):
    db, callers, offices = scratch
    fake_clock(monkeypatch, datetime(2027, 3, 3, 0, 40, 16, 155843))
    seed.seed_ground_truth_calls(db, callers, offices)
    assert gt_calls(db)["CALL-0008"].start_time == datetime(2026, 10, 4, 7, 30)


def test_a_missing_or_invalid_csv_row_falls_back_for_that_call_only(scratch, tmp_path):
    db, callers, offices = scratch
    csv_path = tmp_path / "m.csv"
    rows = ["call_id,filename,caller_id,office_id,account_type,start_time,language"]
    for r in load_csv_rows(REAL_CSV):
        if r["call_id"] == "CALL-0003":
            continue  # missing row
        start = "2026-10-02" if r["call_id"] == "CALL-0004" else r["start_time"]  # date only: invalid
        rows.append(",".join([r["call_id"], r["filename"], r["caller_id"], r["office_id"], r["account_type"], start, r["language"]]))
    csv_path.write_text("\n".join(rows) + "\n", encoding="utf-8")

    seed.seed_ground_truth_calls(db, callers, offices, metadata_csv=csv_path)
    calls = gt_calls(db)
    assert calls["CALL-0001"].start_time == datetime(2026, 10, 1, 10, 30)  # still from the CSV
    assert (calls["CALL-0003"].start_time.hour, calls["CALL-0003"].start_time.minute) == (11, 0)  # intended time of day
    assert (calls["CALL-0004"].start_time.hour, calls["CALL-0004"].start_time.minute) == (14, 45)
    assert calls["CALL-0003"].start_time.second == 0 and calls["CALL-0003"].start_time.microsecond == 0


def test_without_a_csv_the_fallback_still_sets_the_intended_times_of_day(scratch, monkeypatch, tmp_path):
    db, callers, offices = scratch
    fake_clock(monkeypatch, datetime(2026, 10, 29, 0, 40, 16, 155843))
    seed.seed_ground_truth_calls(db, callers, offices, metadata_csv=tmp_path / "does_not_exist.csv")
    calls = gt_calls(db)
    intended = {1: (10, 30), 2: (15, 15), 3: (11, 0), 4: (14, 45), 5: (9, 0)}
    for n, hm in intended.items():
        t = calls[f"CALL-{n:04d}"].start_time
        assert (t.hour, t.minute, t.second, t.microsecond) == (*hm, 0, 0)


# --- metadata-only calls follow the business-hours policy ----------------------------------------------------------------


@pytest.mark.parametrize("clock", [datetime(2026, 10, 29, 0, 40, 16, 155843), datetime(2026, 10, 29, 23, 59, 59), datetime(2026, 10, 29, 12, 0, 0)])
def test_no_metadata_only_call_is_outside_permitted_hours_whatever_the_seeding_clock(scratch, monkeypatch, clock):
    db, callers, offices = scratch
    fake_clock(monkeypatch, clock)
    random.seed(12345)
    seed.seed_metadata_calls(db, callers, offices)
    calls = db.query(Call).filter(Call.is_ground_truth.is_(False)).all()
    assert len(calls) == 1980
    outside = [c.id for c in calls if evaluate_r1(c.start_time).status != "ok"]
    assert outside == []  # the old arithmetic put about 6% outside hours
    assert all(8 <= c.start_time.hour <= 18 and c.start_time.microsecond == 0 for c in calls)


def test_metadata_only_dates_stay_within_the_last_ninety_days(scratch, monkeypatch):
    db, callers, offices = scratch
    clock = datetime(2026, 10, 29, 9, 0, 0)
    fake_clock(monkeypatch, clock)
    random.seed(7)
    seed.seed_metadata_calls(db, callers, offices)
    dates = [c.start_time.date() for c in db.query(Call).filter(Call.is_ground_truth.is_(False))]
    assert min(dates) >= (clock - timedelta(days=90)).date() and max(dates) <= clock.date()


def test_metadata_only_calls_keep_their_other_columns_policy(scratch):
    db, callers, offices = scratch
    random.seed(3)
    seed.seed_metadata_calls(db, callers, offices)
    caller_ids = {c.id: c.office_id for c in callers}
    for call in db.query(Call).filter(Call.is_ground_truth.is_(False)).limit(200):
        assert call.office_id == caller_ids[call.caller_id] and call.status == "metadata_only" and call.has_audio is False
        assert call.id.startswith("CALL-") and int(call.id[-4:]) >= 21


# --- safety ----------------------------------------------------------------------------------------------------------------


def test_importing_the_seeder_runs_nothing_and_leaves_production_untouched():
    prod = BACKEND / "data" / "compliance.db"
    before = hashlib.sha256(prod.read_bytes()).hexdigest() if prod.exists() else None
    module = load_seeder()
    assert module.__name__ != "__main__" and callable(module.main)
    assert (hashlib.sha256(prod.read_bytes()).hexdigest() if prod.exists() else None) == before
