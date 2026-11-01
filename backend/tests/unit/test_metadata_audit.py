"""
Metadata reconciliation and the R1 dry run. The data below are the real values
found in the production audit (stored times carry the seed script's stray
seconds and microseconds), so these tests pin the verified discrepancy.
"""

import hashlib
import importlib.util
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.services import metadata_audit as ma

SEED_NOISE = ".155843"


def csv_row(n, start, caller="u-001", office="o-01", acct="personal_loan", lang="hi"):
    return {"call_id": f"CALL-{n:04d}", "filename": f"call_{n:02d}.wav", "caller_id": caller, "office_id": office,
            "account_type": acct, "start_time": start, "language": lang}


def db_row(n, start, caller="u-001", office="o-01", acct="personal_loan", lang="hi", has_audio=1):
    return {"id": f"CALL-{n:04d}", "caller_id": caller, "office_id": office, "account_type": acct, "language": lang,
            "start_time": start, "has_audio": has_audio, "audio_path": f"data/audio/call_{n:02d}.wav" if has_audio else None}


# the verified production rows (CSV time, stored time)
PRODUCTION = {
    1: ("2026-10-01T10:30:00", f"2026-09-29 11:10:16{SEED_NOISE}"),
    2: ("2026-10-01T15:15:00", f"2026-10-04 15:55:16{SEED_NOISE}"),
    3: ("2026-10-02T11:00:00", f"2026-10-09 11:40:16{SEED_NOISE}"),
    4: ("2026-10-02T14:45:00", f"2026-10-14 15:25:16{SEED_NOISE}"),
    5: ("2026-10-03T09:00:00", f"2026-10-19 09:40:16{SEED_NOISE}"),
    8: ("2026-10-04T07:30:00", f"2026-09-25 08:32:16{SEED_NOISE}"),
    12: ("2026-10-06T19:15:00", f"2026-10-03 19:40:16{SEED_NOISE}"),
    16: ("2026-10-08T20:45:00", f"2026-10-11 19:53:16{SEED_NOISE}"),
    20: ("2026-10-10T16:50:00", f"2026-10-19 21:57:16{SEED_NOISE}"),
}


@pytest.fixture()
def audio_dir(tmp_path):
    d = tmp_path / "audio"
    d.mkdir()
    for n in (1, 2, 3, 4, 5, 8):
        (d / f"call_{n:02d}.wav").write_bytes(b"RIFF")
    return d


def run(audio_dir, numbers=tuple(PRODUCTION), extra_csv=(), skip_csv=(), skip_db=()):
    csv_rows = [csv_row(n, PRODUCTION[n][0]) for n in numbers if n not in skip_csv] + list(extra_csv)
    db_rows = {f"CALL-{n:04d}": db_row(n, PRODUCTION[n][1]) for n in numbers if n not in skip_db}
    return ma.audit_calls(csv_rows, db_rows, audio_dir, [f"CALL-{n:04d}" for n in numbers])


def by_id(audits):
    return {a.call_id: a for a in audits}


# --- the root cause, as data ----------------------------------------------------------


def test_every_stored_start_time_differs_from_the_csv():
    audits = run_all = None
    audits = by_id(run(Path(".")))
    assert all(any(d.field == "start_time" for d in a.discrepancies) for a in audits.values())


def test_hard_coded_calls_are_all_shifted_by_the_same_constant_clock_offset():
    offsets = {
        ma.seed_offset(datetime.fromisoformat(PRODUCTION[n][0]), datetime.fromisoformat(PRODUCTION[n][1]))
        for n in (1, 2, 3, 4, 5)
    }
    assert offsets == {timedelta(minutes=40, seconds=16, microseconds=155843)}  # the seed run's local clock time of day


# --- R1 outcomes: current (stored time) versus proposed (CSV time) -----------------------


def test_call_8_would_change_from_in_hours_to_a_violation():
    a = by_id(run(Path(".")))["CALL-0008"]
    assert (a.r1_db.status, a.r1_csv.status) == ("ok", "violation")


def test_call_20_would_change_from_a_false_violation_to_in_hours():
    a = by_id(run(Path(".")))["CALL-0020"]
    assert (a.r1_db.status, a.r1_csv.status) == ("violation", "ok")


def test_only_calls_8_and_20_change_their_r1_outcome():
    rows = ma.r1_dry_run(run(Path(".")))
    assert {r["call_id"] for r in rows if r["verdict"] == ma.CHANGE} == {"CALL-0008", "CALL-0020"}
    assert all(r["verdict"] == ma.AGREE for r in rows if r["call_id"] not in ("CALL-0008", "CALL-0020"))


def test_calls_12_and_16_agree_on_the_verdict_but_not_on_the_time():
    audits = by_id(run(Path(".")))
    for call in ("CALL-0012", "CALL-0016"):
        assert audits[call].r1_db.status == audits[call].r1_csv.status == "violation"
        assert audits[call].r1_db.violation["quote"] != audits[call].r1_csv.violation["quote"]


def test_proposed_r1_matches_the_expected_labels_but_the_stored_times_do_not():
    labels = {f"CALL-{n:04d}": n in (8, 12, 16) for n in PRODUCTION}
    audits = run(Path("."))
    rows = {r["call_id"]: r for r in ma.r1_dry_run(audits, labels)}
    proposed_ok = [c for c, r in rows.items() if (r["proposed_r1"] == "violation") == labels[c]]
    current_ok = [c for c, r in rows.items() if (r["current_r1"] == "violation") == labels[c]]
    assert len(proposed_ok) == len(labels)
    assert set(labels) - set(current_ok) == {"CALL-0008", "CALL-0020"}


def test_only_the_start_time_gets_a_proposed_value():
    audits = by_id(run(Path(".")))
    for d in audits["CALL-0008"].discrepancies:
        assert (d.proposed_value is not None) == (d.field == "start_time")
    start = next(d for d in audits["CALL-0008"].discrepancies if d.field == "start_time")
    assert start.proposed_value == "2026-10-04T07:30:00" and "seed script" in start.reason


# --- missing audio versus missing metadata -------------------------------------------------


def test_a_flagged_recording_with_no_file_is_missing_audio_not_present(audio_dir):
    audits = by_id(run(audio_dir))
    assert audits["CALL-0001"].recording == ma.RECORDING_PRESENT
    for call in ("CALL-0012", "CALL-0016", "CALL-0020"):
        assert audits[call].recording == ma.RECORDING_MISSING_FILE  # has_audio=1 but no file


def test_missing_audio_does_not_stop_r1_from_being_evaluated(audio_dir):
    a = by_id(run(audio_dir))["CALL-0012"]
    assert a.recording == ma.RECORDING_MISSING_FILE and a.r1_csv.status == "violation" and a.problems == []


def test_a_record_that_says_no_recording_is_not_expected_not_missing(tmp_path):
    rows = {"CALL-0001": db_row(1, PRODUCTION[1][1], has_audio=0)}
    audit = ma.audit_calls([csv_row(1, PRODUCTION[1][0])], rows, tmp_path, ["CALL-0001"])[0]
    assert audit.recording == ma.RECORDING_NOT_EXPECTED


def test_the_has_audio_flag_alone_never_makes_a_recording_present(tmp_path):
    audit = ma.audit_calls([csv_row(1, PRODUCTION[1][0])], {"CALL-0001": db_row(1, PRODUCTION[1][1], has_audio=1)}, tmp_path, ["CALL-0001"])[0]
    assert audit.recording == ma.RECORDING_MISSING_FILE


def test_a_missing_csv_row_is_missing_metadata_while_the_recording_state_is_separate(audio_dir):
    a = by_id(run(audio_dir, skip_csv=(1,)))["CALL-0001"]
    assert any(p.startswith("missing metadata") for p in a.problems)
    assert a.recording == ma.RECORDING_PRESENT  # the recording is there; only the metadata row is not
    assert a.r1_csv.status == "unresolved" and a.r1_db.status in ("ok", "violation")


def test_a_missing_database_record_is_reported(audio_dir):
    a = by_id(run(audio_dir, skip_db=(2,)))["CALL-0002"]
    assert any(p.startswith("missing record") for p in a.problems)


def test_valid_metadata_is_kept_when_the_recording_is_unavailable(audio_dir):
    a = by_id(run(audio_dir))["CALL-0020"]
    assert a.recording == ma.RECORDING_MISSING_FILE and a.csv_row["start_time"] == "2026-10-10T16:50:00" and a.problems == []


# --- bad metadata ------------------------------------------------------------------------------


def test_duplicate_csv_rows_are_reported():
    audits = ma.audit_calls([csv_row(1, "2026-10-01T10:30:00"), csv_row(1, "2026-10-01T11:00:00")], {"CALL-0001": db_row(1, PRODUCTION[1][1])}, Path("."), ["CALL-0001"])
    assert any("duplicate metadata" in p for p in audits[0].problems)


@pytest.mark.parametrize("bad", ["2026-10-01", "not a time", "", "01/10/2026 10:30"])
def test_an_invalid_csv_timestamp_is_reported_and_r1_is_unresolved(bad):
    a = ma.audit_calls([csv_row(1, bad)], {"CALL-0001": db_row(1, PRODUCTION[1][1])}, Path("."), ["CALL-0001"])[0]
    assert any("invalid timestamp in the CSV" in p for p in a.problems)
    assert a.r1_csv.status == "unresolved"
    assert ma.r1_dry_run([a])[0]["verdict"] == ma.UNRESOLVED  # never reported as confirmed


def test_an_unreadable_database_timestamp_is_reported_and_unresolved():
    a = ma.audit_calls([csv_row(1, "2026-10-01T10:30:00")], {"CALL-0001": db_row(1, "garbage")}, Path("."), ["CALL-0001"])[0]
    assert any("invalid timestamp in the database" in p for p in a.problems) and a.r1_db.status == "unresolved"


def test_matching_values_produce_no_discrepancy():
    a = ma.audit_calls([csv_row(1, "2026-10-01T10:30:00")], {"CALL-0001": db_row(1, "2026-10-01 10:30:00")}, Path("."), ["CALL-0001"])[0]
    assert a.discrepancies == []


def test_office_conflicts_are_found():
    rows = [csv_row(1, "2026-10-01T10:30:00", caller="u-040", office="o-01"), csv_row(2, "2026-10-01T10:30:00", caller="u-041", office="o-02")]
    conflicts = ma.office_conflicts(rows, {"u-040": "o-03", "u-041": "o-02"})
    assert [(c["call_id"], c["csv_office"], c["caller_office"]) for c in conflicts] == [("CALL-0001", "o-01", "o-03")]


# --- the audit never writes -----------------------------------------------------------------------


def load_script():
    spec = importlib.util.spec_from_file_location("audit_metadata_script", Path(__file__).parent.parent.parent / "scripts" / "audit_metadata.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_scratch_db(path):
    con = sqlite3.connect(path)
    con.execute("create table call (id text, caller_id text, office_id text, account_type text, language text, start_time text, has_audio int, audio_path text, is_ground_truth int)")
    con.execute("create table app_user (id text, office_id text)")
    for n, (_, stored) in PRODUCTION.items():
        con.execute("insert into call values (?,?,?,?,?,?,?,?,1)", (f"CALL-{n:04d}", "u-001", "o-01", "personal_loan", "hi", stored, 1, f"data/audio/call_{n:02d}.wav"))
    con.execute("insert into app_user values ('u-001','o-01')")
    con.commit()
    con.close()


def test_the_connection_the_tool_uses_cannot_write(tmp_path):
    script = load_script()
    db = tmp_path / "scratch.db"
    make_scratch_db(db)
    con = script.open_readonly(db)
    with pytest.raises(sqlite3.OperationalError):
        con.execute("update call set start_time = 'x'")
    with pytest.raises(sqlite3.OperationalError):
        con.execute("delete from call")
    con.close()


def test_running_the_tool_leaves_the_database_and_csv_byte_identical(tmp_path, capsys):
    script = load_script()
    db, csv_path = tmp_path / "scratch.db", tmp_path / "metadata.csv"
    make_scratch_db(db)
    header = "call_id,filename,caller_id,office_id,account_type,start_time,language\n"
    csv_path.write_text(header + "".join(
        f"CALL-{n:04d},call_{n:02d}.wav,u-001,o-01,personal_loan,{PRODUCTION[n][0]},hi\n" for n in PRODUCTION), encoding="utf-8")
    sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
    before = (sha(db), sha(csv_path))

    code = script.main(["--json", str(tmp_path / "report.json")], db_path=db, csv_path=csv_path, audio_dir=tmp_path, labels={})

    assert code == 0 and (sha(db), sha(csv_path)) == before
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    changed = {r["call_id"] for r in report["dry_run"] if r["verdict"] == "change_requires_approval"}
    assert "CALL-0008" in changed and "CALL-0020" in changed
    assert "R1 DRY RUN (nothing is changed)" in capsys.readouterr().out


def test_the_tool_reports_a_missing_database_instead_of_creating_one(tmp_path):
    script = load_script()
    assert script.main([], db_path=tmp_path / "nope.db", csv_path=tmp_path / "m.csv") == 2
    assert not (tmp_path / "nope.db").exists()
