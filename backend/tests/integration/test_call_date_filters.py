"""
I-06: the Call List date filter. Calls are stored as naive IST wall-clock times and a filter value
is an IST wall-clock time too (see app/services/date_filters.py); nothing goes through UTC.
"""

from datetime import datetime

import pytest

from app.models import Call
from app.services.date_filters import parse_filter_bound
from tests.conftest import HEAD_HEADERS


def add_call(db, call_id, when):
    db.add(Call(id=call_id, caller_id="u-caller1", office_id="o-01", account_type="personal_loan", start_time=when,
                duration_seconds=60, language="hi", has_audio=False, status="metadata_only"))
    db.commit()


def listed(client, **params):
    r = client.get("/api/calls", headers=HEAD_HEADERS, params={"page_size": 200, **params})
    assert r.status_code == 200, r.text
    return {c["call_id"] for c in r.json()["items"] if c["call_id"].startswith("T-")}


@pytest.fixture()
def calls(db_session):
    # around midnight and around 01:30, all on and next to 2026-10-12
    add_call(db_session, "T-1100-2359", datetime(2026, 10, 11, 23, 59, 59))
    add_call(db_session, "T-1200-0000", datetime(2026, 10, 12, 0, 0, 0))
    add_call(db_session, "T-1200-0100", datetime(2026, 10, 12, 1, 0, 0))
    add_call(db_session, "T-1200-0130", datetime(2026, 10, 12, 1, 30, 0))
    add_call(db_session, "T-1200-2359", datetime(2026, 10, 12, 23, 59, 59))
    add_call(db_session, "T-1200-2359-FRAC", datetime(2026, 10, 12, 23, 59, 59, 999999))
    add_call(db_session, "T-1300-0000", datetime(2026, 10, 13, 0, 0, 0))
    return db_session


# ------------------------------------------------------------------ the reported bug
def test_a_call_at_0100_ist_is_excluded_by_a_start_filter_of_0130_ist_and_0130_is_included(client, calls):
    found = listed(client, date_from="2026-10-12T01:30:00")
    assert "T-1200-0100" not in found and "T-1200-0130" in found
    assert {"T-1200-2359", "T-1300-0000"} <= found and "T-1100-2359" not in found


@pytest.mark.parametrize("same_instant", [
    "2026-10-11T20:00:00Z",  # 01:30 IST written in UTC
    "2026-10-11T20:00:00.000Z",  # exactly what the old UI sent (toISOString)
    "2026-10-12T01:30:00+05:30",  # 01:30 IST with its own offset
    "2026-10-11T15:00:00-05:00",  # 01:30 IST written in US Central
])
def test_a_filter_with_an_offset_means_the_same_instant_in_ist(client, calls, same_instant):
    found = listed(client, date_from=same_instant)
    assert "T-1200-0100" not in found and "T-1200-0130" in found  # the old code compared 20:00 against IST times and included 01:00


def test_the_old_ui_value_no_longer_includes_the_wrong_call(client, calls):
    # the date picked was 2026-10-12 in an IST browser: the old UI sent new Date("2026-10-12").toISOString()
    old_ui_value = "2026-10-12T00:00:00.000Z"
    assert "T-1200-0000" not in listed(client, date_from=old_ui_value)  # 00:00Z is 05:30 IST, so 00:00 IST is before it
    assert "T-1200-0000" in listed(client, date_from="2026-10-12")  # the contract the UI now uses


# ------------------------------------------------------------------ end bounds and midnight
def test_a_date_only_end_filter_covers_the_whole_day_and_stops_at_midnight(client, calls):
    found = listed(client, date_to="2026-10-12")
    assert {"T-1200-0000", "T-1200-0100", "T-1200-2359", "T-1200-2359-FRAC", "T-1100-2359"} <= found
    assert "T-1300-0000" not in found  # midnight at the end of the day belongs to the next day


def test_choosing_the_same_day_twice_returns_exactly_that_day(client, calls):
    assert listed(client, date_from="2026-10-12", date_to="2026-10-12") == {
        "T-1200-0000", "T-1200-0100", "T-1200-0130", "T-1200-2359", "T-1200-2359-FRAC",
    }


def test_calls_around_midnight_stay_on_their_own_date(client, calls):
    assert listed(client, date_from="2026-10-11", date_to="2026-10-11") == {"T-1100-2359"}
    assert listed(client, date_from="2026-10-13", date_to="2026-10-13") == {"T-1300-0000"}
    assert "T-1200-0000" not in listed(client, date_to="2026-10-11") and "T-1100-2359" not in listed(client, date_from="2026-10-12")


def test_a_datetime_end_filter_is_inclusive_at_the_boundary(client, calls):
    assert "T-1200-0130" in listed(client, date_to="2026-10-12T01:30:00")
    assert "T-1200-0130" not in listed(client, date_to="2026-10-12T01:29:59")
    assert "T-1200-2359" in listed(client, date_to="2026-10-12T23:59:59")
    assert "T-1200-2359-FRAC" not in listed(client, date_to="2026-10-12T23:59:59")  # 0.999999 s later


def test_an_end_filter_with_an_offset_is_converted_to_ist(client, calls):
    assert "T-1200-0130" in listed(client, date_to="2026-10-11T20:00:00Z")  # 01:30 IST
    assert "T-1200-0130" not in listed(client, date_to="2026-10-11T19:59:59Z")


# ------------------------------------------------------------------ the rest of the list behaves as before
def test_empty_values_are_ignored(client, calls):
    assert listed(client, date_from="", date_to="") == listed(client)


@pytest.mark.parametrize("bad", ["yesterday", "2026-13-45", "2026-10-12T25:00:00", "12/10/2026", "2026-10"])
@pytest.mark.parametrize("name", ["date_from", "date_to"])
def test_invalid_values_are_rejected_with_a_clear_error(client, calls, bad, name):
    r = client.get("/api/calls", headers=HEAD_HEADERS, params={name: bad})
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_DATE_FILTER"


def test_date_filters_combine_with_the_other_filters_sorting_and_pagination(client, calls):
    base = {"date_from": "2026-10-12", "date_to": "2026-10-12", "analysis_status": "completed"}
    assert listed(client, **base) == set()  # these are metadata-only calls, so the status filter still applies
    p1 = client.get("/api/calls", headers=HEAD_HEADERS, params={"date_from": "2026-10-12", "date_to": "2026-10-12", "page_size": 2, "page": 1, "sort": "date_asc"}).json()
    p3 = client.get("/api/calls", headers=HEAD_HEADERS, params={"date_from": "2026-10-12", "date_to": "2026-10-12", "page_size": 2, "page": 3, "sort": "date_asc"}).json()
    assert p1["total"] == 5 and [c["call_id"] for c in p1["items"]] == ["T-1200-0000", "T-1200-0100"]
    assert [c["call_id"] for c in p3["items"]] == ["T-1200-2359-FRAC"]  # 5 calls, pages of 2: the last page holds the 5th
    q = client.get("/api/calls", headers=HEAD_HEADERS, params={"date_from": "2026-10-12", "date_to": "2026-10-12", "q": "T-1200-0100"}).json()
    assert [c["call_id"] for c in q["items"]] == ["T-1200-0100"]


# ------------------------------------------------------------------ the parser on its own
@pytest.mark.parametrize("value,upper,expected", [
    ("2026-10-12", False, datetime(2026, 10, 12, 0, 0, 0)),
    ("2026-10-12", True, datetime(2026, 10, 12, 23, 59, 59, 999999)),
    ("2026-10-12T01:30:00", False, datetime(2026, 10, 12, 1, 30)),
    ("2026-10-12T01:30:00", True, datetime(2026, 10, 12, 1, 30)),  # a datetime is used exactly, as a lower or an upper bound
    ("2026-10-11T20:00:00Z", False, datetime(2026, 10, 12, 1, 30)),
    ("2026-10-12T01:30:00+05:30", True, datetime(2026, 10, 12, 1, 30)),
    ("2026-10-12T00:00:00-08:00", False, datetime(2026, 10, 12, 13, 30)),
    ("  2026-10-12  ", False, datetime(2026, 10, 12)),
    ("", False, None), (None, True, None), ("   ", True, None),
])
def test_parse_filter_bound(value, upper, expected):
    assert parse_filter_bound(value, upper=upper) == expected


def test_parse_filter_bound_rejects_garbage():
    for bad in ("tomorrow", "2026-02-30", "2026-10-12T99:00"):
        with pytest.raises(ValueError):
            parse_filter_bound(bad, upper=False)
