"""
The start-time correction, exercised ONLY on scratch databases built from the
real production values found in the audit. Nothing here touches the production
database.
"""

import hashlib
import importlib.util
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import AppUser, Call, Office, TranscriptSegment, Violation
from app.services import start_time_correction as stc

STORED = {  # call number -> (stored value in production, CSV value)
    1: ("2026-09-29 11:10:16.155843", "2026-10-01T10:30:00"), 2: ("2026-10-04 15:55:16.155843", "2026-10-01T15:15:00"),
    3: ("2026-10-09 11:40:16.155843", "2026-10-02T11:00:00"), 4: ("2026-10-14 15:25:16.155843", "2026-10-02T14:45:00"),
    5: ("2026-10-19 09:40:16.155843", "2026-10-03T09:00:00"), 6: ("2026-09-21 11:54:16.155843", "2026-10-03T12:20:00"),
    7: ("2026-09-23 12:56:16.155843", "2026-10-04T10:10:00"), 8: ("2026-09-25 08:32:16.155843", "2026-10-04T07:30:00"),
    9: ("2026-09-27 17:00:16.155843", "2026-10-05T11:45:00"), 10: ("2026-09-29 16:21:16.155843", "2026-10-05T16:00:00"),
    11: ("2026-10-01 14:31:16.155843", "2026-10-06T09:50:00"), 12: ("2026-10-03 19:40:16.155843", "2026-10-06T19:15:00"),
    13: ("2026-10-05 18:08:16.155843", "2026-10-07T13:05:00"), 14: ("2026-10-07 16:45:16.155843", "2026-10-07T10:40:00"),
    15: ("2026-10-09 10:18:16.155843", "2026-10-08T15:30:00"), 16: ("2026-10-11 19:53:16.155843", "2026-10-08T20:45:00"),
    17: ("2026-10-13 14:54:16.155843", "2026-10-09T11:15:00"), 18: ("2026-10-15 14:24:16.155843", "2026-10-09T14:00:00"),
    19: ("2026-10-17 16:29:16.155843", "2026-10-10T09:25:00"), 20: ("2026-10-19 21:57:16.155843", "2026-10-10T16:50:00"),
}
IDS = [f"CALL-{n:04d}" for n in range(1, 21)]
CSV_ROWS = [{"call_id": f"CALL-{n:04d}", "start_time": csv} for n, (_, csv) in STORED.items()]


def make_db(path: Path, revision="c41f0e7a9b53", metadata_only=5):
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(Office(id="o-01", name="Scratch"))
    db.add(AppUser(id="u-1", name="Caller", role="caller", office_id="o-01"))
    db.flush()
    for n, (stored, _) in STORED.items():
        db.add(Call(id=f"CALL-{n:04d}", caller_id="u-1", office_id="o-01", account_type="personal_loan",
                    start_time=datetime.strptime(stored, stc.DB_TIME_FORMAT), has_audio=True, audio_path=f"data/audio/call_{n:02d}.wav",
                    status="queued", is_ground_truth=True))
    for i in range(metadata_only):
        db.add(Call(id=f"CALL-{21 + i:04d}", caller_id="u-1", office_id="o-01", account_type="credit_card",
                    start_time=datetime(2026, 9, 1 + i, 10, 0, 0, 155843), status="metadata_only", is_ground_truth=False))
    db.commit()
    db.close()
    engine.dispose()
    con = sqlite3.connect(path)
    con.execute("create table alembic_version (version_num varchar(32) not null)")
    con.execute("insert into alembic_version values (?)", (revision,))
    con.commit()
    con.close()
    return path


@pytest.fixture()
def db(tmp_path):
    return make_db(tmp_path / "scratch.db")


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def backup_of(db_path, tmp_path):
    bk = tmp_path / "backup.bak"
    shutil.copy(db_path, bk)
    return bk


def ro(path):
    return sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)


def approved(path):
    """The changes in a dry-run report for this database: what would be approved."""
    return [c.__dict__ for c in stc.build_plan(ro(path), CSV_ROWS, IDS).changes]


def times(path):
    con = ro(path)
    try:
        return dict(con.execute("select id, start_time from call"))
    finally:
        con.close()


# --- the plan (what the dry run reports) ---------------------------------------------------


def test_plan_has_exactly_twenty_changes_with_exact_before_and_after_values(db):
    plan = stc.build_plan(ro(db), CSV_ROWS, IDS)
    assert plan.ok and plan.problems == [] and len(plan.changes) == 20 and plan.unchanged == []
    first = plan.changes[0]
    assert (first.call_id, first.before, first.after) == ("CALL-0001", "2026-09-29 11:10:16.155843", "2026-10-01 10:30:00.000000")
    assert all(c.before == STORED[int(c.call_id[-4:])][0] for c in plan.changes)


def test_the_plan_only_changes_the_start_time_of_ground_truth_calls(db):
    plan = stc.build_plan(ro(db), CSV_ROWS, IDS)
    assert {c.call_id for c in plan.changes} == set(IDS)
    sql = stc.render_sql(plan)
    assert sql.count("UPDATE call SET start_time") == 20 and "is_ground_truth = 1" in sql and sql.startswith("BEGIN IMMEDIATE")
    assert "DELETE" not in sql and "INSERT" not in sql and "violation" not in sql.lower()


def test_only_calls_8_and_20_change_their_r1_outcome(db):
    plan = stc.build_plan(ro(db), CSV_ROWS, IDS)
    flips = {c.call_id: (c.r1_before, c.r1_after) for c in plan.changes if c.r1_before != c.r1_after}
    assert flips == {"CALL-0008": ("ok", "violation"), "CALL-0020": ("violation", "ok")}


def test_building_the_plan_writes_nothing(db):
    before = sha(db)
    stc.build_plan(ro(db), CSV_ROWS, IDS)
    assert sha(db) == before


@pytest.mark.parametrize("revision", ["98bd1e6ee31d", "d56a1f75ef19", "zzz"])
def test_the_wrong_database_revision_blocks_the_plan(tmp_path, revision):
    plan = stc.build_plan(ro(make_db(tmp_path / "r.db", revision=revision)), CSV_ROWS, IDS)
    assert not plan.ok and any("revision" in p for p in plan.problems)


def test_a_missing_or_unparseable_csv_row_blocks_the_plan(db):
    rows = [r for r in CSV_ROWS if r["call_id"] != "CALL-0003"]
    rows[0] = {**rows[0], "start_time": "2026-10-01"}  # date only
    plan = stc.build_plan(ro(db), rows, IDS)
    assert not plan.ok
    assert any("CALL-0003: no CSV row" in p for p in plan.problems) and any("not a full date-time" in p for p in plan.problems)


def test_a_duplicate_csv_row_blocks_the_plan(db):
    plan = stc.build_plan(ro(db), CSV_ROWS + [CSV_ROWS[0]], IDS)
    assert not plan.ok and any("more than one CSV row" in p for p in plan.problems)


def test_fewer_than_twenty_changes_is_not_ok(tmp_path):
    path = make_db(tmp_path / "p.db")
    con = sqlite3.connect(path)
    con.execute("update call set start_time = '2026-10-01 10:30:00.000000' where id = 'CALL-0001'")  # already correct
    con.commit(); con.close()
    plan = stc.build_plan(ro(path), CSV_ROWS, IDS)
    assert plan.unchanged == ["CALL-0001"] and len(plan.changes) == 19 and not plan.ok
    assert any("exactly 20" in p for p in plan.problems)


def test_an_analysed_call_blocks_the_correction_because_its_r1_evidence_would_go_stale(tmp_path):
    path = make_db(tmp_path / "a.db")
    con = sqlite3.connect(path)
    con.execute("insert into violation (id, call_id, rule_code, severity, quote, created_at, needs_speaker_review, evidence_status) "
                "values ('v-CALL-0012-0000','CALL-0012','R1','high','Call started at 19:40:16 IST (outside permitted hours 08:00-19:00)', datetime('now'), 0, 'verified')")
    con.commit(); con.close()
    plan = stc.build_plan(ro(path), CSV_ROWS, IDS)
    assert not plan.ok and any("CALL-0012" in p and "stale" in p for p in plan.problems)


def test_a_non_ground_truth_call_is_never_planned(tmp_path):
    path = make_db(tmp_path / "g.db")
    con = sqlite3.connect(path)
    con.execute("update call set is_ground_truth = 0 where id = 'CALL-0005'")
    con.commit(); con.close()
    plan = stc.build_plan(ro(path), CSV_ROWS, IDS)
    assert not plan.ok and any("CALL-0005" in p and "not a ground-truth" in p for p in plan.problems)


# --- applying: guards -------------------------------------------------------------------------


def test_apply_changes_exactly_the_twenty_rows_and_nothing_else(db, tmp_path):
    bk = backup_of(db, tmp_path)
    before = times(db)
    plan = stc.apply_plan(db, CSV_ROWS, IDS, bk, approved(db))
    after = times(db)

    assert len(plan.changes) == 20
    assert {i for i in before if before[i] != after[i]} == set(IDS)
    assert all(after[f"CALL-{n:04d}"] == datetime.fromisoformat(csv).strftime(stc.DB_TIME_FORMAT) for n, (_, csv) in STORED.items())
    assert {i: t for i, t in after.items() if i not in IDS} == {i: t for i, t in before.items() if i not in IDS}  # the 5 metadata-only calls untouched


def test_apply_leaves_every_other_column_unchanged(db, tmp_path):
    bk = backup_of(db, tmp_path)
    def digest(path):
        con = ro(path)
        cols = [r[1] for r in con.execute("pragma table_info(call)") if r[1] != "start_time"]
        return hashlib.sha256(repr(list(con.execute(f"select {','.join(cols)} from call order by id"))).encode()).hexdigest()
    before = digest(db)
    stc.apply_plan(db, CSV_ROWS, IDS, bk, approved(db))
    assert digest(db) == before


def test_the_corrected_values_are_read_back_correctly_by_the_application_orm(db, tmp_path):
    stc.apply_plan(db, CSV_ROWS, IDS, backup_of(db, tmp_path), approved(db))
    engine = create_engine(f"sqlite:///{Path(db).as_posix()}")
    session = sessionmaker(bind=engine)()
    try:
        assert session.get(Call, "CALL-0008").start_time == datetime(2026, 10, 4, 7, 30, 0)
        assert session.get(Call, "CALL-0020").start_time == datetime(2026, 10, 10, 16, 50, 0)
        assert session.get(Call, "CALL-0012").start_time == datetime(2026, 10, 6, 19, 15, 0)
    finally:
        session.close(); engine.dispose()


def test_apply_a_second_time_changes_nothing_and_is_refused(db, tmp_path):
    report = approved(db)
    stc.apply_plan(db, CSV_ROWS, IDS, backup_of(db, tmp_path), report)
    after_first = sha(db)
    second_backup = tmp_path / "second.bak"
    shutil.copy(db, second_backup)  # a fresh, valid backup
    # Re-using the old approved report: nothing is left to change, so it is refused.
    with pytest.raises(stc.CorrectionRefused, match="exactly 20"):
        stc.apply_plan(db, CSV_ROWS, IDS, second_backup, report)
    # And a new dry run now has nothing to approve at all.
    with pytest.raises(stc.CorrectionRefused, match="no approved dry-run report"):
        stc.apply_plan(db, CSV_ROWS, IDS, second_backup, approved(db))
    assert sha(db) == after_first


def test_a_missing_backup_is_refused_and_nothing_is_written(db, tmp_path):
    before = sha(db)
    with pytest.raises(stc.CorrectionRefused, match="backup not found"):
        stc.apply_plan(db, CSV_ROWS, IDS, tmp_path / "nope.bak", approved(db))
    assert sha(db) == before


def test_a_stale_backup_is_refused(db, tmp_path):
    bk = backup_of(db, tmp_path)
    con = sqlite3.connect(db)
    con.execute("update call set language = 'xx' where id = 'CALL-0021'")  # the database moved on after the backup
    con.commit(); con.close()
    before = sha(db)
    with pytest.raises(stc.CorrectionRefused, match="not byte-identical"):
        stc.apply_plan(db, CSV_ROWS, IDS, bk, approved(db))
    assert sha(db) == before


def test_using_the_database_itself_as_the_backup_is_refused(db):
    with pytest.raises(stc.CorrectionRefused, match="different file"):
        stc.apply_plan(db, CSV_ROWS, IDS, db, approved(db))


def test_a_corrupt_backup_is_refused(db, tmp_path):
    bk = tmp_path / "bad.bak"
    bk.write_bytes(b"not a database")
    with pytest.raises(stc.CorrectionRefused):
        stc.apply_plan(db, CSV_ROWS, IDS, bk, approved(db))


def test_the_wrong_revision_is_refused_at_apply_time(tmp_path):
    path = make_db(tmp_path / "w.db", revision="98bd1e6ee31d")
    before = sha(path)
    with pytest.raises(stc.CorrectionRefused, match="revision"):
        stc.apply_plan(path, CSV_ROWS, IDS, backup_of(path, tmp_path), approved(path))
    assert sha(path) == before


def test_an_analysed_call_is_refused_at_apply_time_and_nothing_is_written(tmp_path):
    path = make_db(tmp_path / "an.db")
    con = sqlite3.connect(path)
    con.execute("insert into transcript_segment (id, call_id, seq, speaker, start_time, end_time, text) values ('s1','CALL-0003',0,'agent',0,1,'hi')")
    con.commit(); con.close()
    before = sha(path)
    with pytest.raises(stc.CorrectionRefused, match="analysis rows"):
        stc.apply_plan(path, CSV_ROWS, IDS, backup_of(path, tmp_path), approved(path))
    assert sha(path) == before


def test_a_value_that_changed_since_the_approved_dry_run_is_refused_not_overwritten(db, tmp_path):
    report = approved(db)  # the dry run that was approved
    con = sqlite3.connect(db)
    con.execute("update call set start_time = '2026-12-25 09:00:00.000000' where id = 'CALL-0004'")  # the row moved afterwards
    con.commit(); con.close()
    bk = tmp_path / "fresh.bak"
    shutil.copy(db, bk)
    before = sha(db)
    with pytest.raises(stc.CorrectionRefused, match="differ from the approved dry-run report"):
        stc.apply_plan(db, CSV_ROWS, IDS, bk, report)
    assert sha(db) == before  # nothing written
    stc.apply_plan(db, CSV_ROWS, IDS, bk, approved(db))  # a NEW dry run, approved, applies
    assert times(db)["CALL-0004"] == "2026-10-02 14:45:00.000000"


def test_an_approved_report_must_be_supplied(db, tmp_path):
    before = sha(db)
    for missing in (None, []):
        with pytest.raises(stc.CorrectionRefused, match="no approved dry-run report"):
            stc.apply_plan(db, CSV_ROWS, IDS, backup_of(db, tmp_path), missing)
    assert sha(db) == before


def test_a_report_with_a_wrong_after_value_is_refused(db, tmp_path):
    report = approved(db)
    report[7] = {**report[7], "after": "2026-10-04 08:30:00.000000"}  # not what the CSV says for CALL-0008
    before = sha(db)
    with pytest.raises(stc.CorrectionRefused, match="differ from the approved"):
        stc.apply_plan(db, CSV_ROWS, IDS, backup_of(db, tmp_path), report)
    assert sha(db) == before


def test_a_report_missing_a_row_or_with_an_extra_row_is_refused(db, tmp_path):
    full = approved(db)
    before = sha(db)
    for report in (full[:-1], full + [{"call_id": "CALL-0021", "before": "x", "after": "y"}]):
        with pytest.raises(stc.CorrectionRefused, match="differ from the approved"):
            stc.apply_plan(db, CSV_ROWS, IDS, _fresh_backup(db, tmp_path), report)
    assert sha(db) == before


def _fresh_backup(db, tmp_path):
    bk = tmp_path / f"b{len(list(tmp_path.glob('*.bak')))}.bak"
    shutil.copy(db, bk)
    return bk


def test_a_failure_midway_rolls_everything_back(db, tmp_path, monkeypatch):
    bk = backup_of(db, tmp_path)
    before = sha(db)
    real = stc._other_columns_digest
    calls = {"n": 0}

    def tamper(con):
        calls["n"] += 1
        if calls["n"] == 2:  # after the updates ran
            return "different"
        return real(con)

    monkeypatch.setattr(stc, "_other_columns_digest", tamper)
    with pytest.raises(stc.CorrectionRefused, match="other than start_time"):
        stc.apply_plan(db, CSV_ROWS, IDS, bk, approved(db))
    assert sha(db) == before  # rolled back: not a single row changed


# --- the script's dry-run default and its refusals -----------------------------------------------


def load_script():
    spec = importlib.util.spec_from_file_location("correct_start_times_script", Path(__file__).parent.parent.parent / "scripts" / "correct_start_times.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_csv(path):
    header = "call_id,filename,caller_id,office_id,account_type,start_time,language\n"
    path.write_text(header + "".join(f"CALL-{n:04d},call_{n:02d}.wav,u-1,o-01,personal_loan,{csv},hi\n" for n, (_, csv) in STORED.items()), encoding="utf-8")
    return path


def test_the_script_defaults_to_a_dry_run_that_writes_nothing(db, tmp_path, capsys):
    script = load_script()
    csv_path = write_csv(tmp_path / "m.csv")
    before = sha(db)
    assert script.main([], db_path=db, csv_path=csv_path) == 0
    out = capsys.readouterr().out
    assert sha(db) == before and "DRY RUN ONLY: nothing was written." in out and "rows that would change: 20" in out


def dry_run_report(script, db, csv_path, tmp_path):
    report = tmp_path / "approved.json"
    assert script.main(["--report", str(report)], db_path=db, csv_path=csv_path) == 0
    return report


@pytest.mark.parametrize(
    "extra",
    [["--apply"], ["--apply", "--backup", "x.bak"], ["--apply", "--backup", "x.bak", "--confirm-rows", "20"],
     ["--apply", "--backup", "x.bak", "--approved-report", "r.json", "--confirm-rows", "20"],
     ["--apply", "--backup", "x.bak", "--approved-report", "r.json", "--i-have-explicit-approval"],
     ["--apply", "--approved-report", "r.json", "--confirm-rows", "20", "--i-have-explicit-approval"],
     ["--apply", "--backup", "x.bak", "--approved-report", "r.json", "--confirm-rows", "19", "--i-have-explicit-approval"]],
)
def test_apply_without_every_required_flag_is_refused_and_writes_nothing(db, tmp_path, extra):
    script = load_script()
    csv_path = write_csv(tmp_path / "m.csv")
    before = sha(db)
    assert script.main(extra, db_path=db, csv_path=csv_path) == 3
    assert sha(db) == before


def test_apply_with_a_stale_backup_is_refused(db, tmp_path):
    script = load_script()
    csv_path = write_csv(tmp_path / "m.csv")
    report = dry_run_report(script, db, csv_path, tmp_path)
    bk = tmp_path / "x.bak"
    bk.write_bytes(b"old")
    before = sha(db)
    code = script.main(["--apply", "--backup", str(bk), "--approved-report", str(report), "--confirm-rows", "20", "--i-have-explicit-approval"], db_path=db, csv_path=csv_path)
    assert code == 4 and sha(db) == before


def test_apply_with_an_unreadable_report_is_refused(db, tmp_path):
    script = load_script()
    csv_path = write_csv(tmp_path / "m.csv")
    before = sha(db)
    code = script.main(["--apply", "--backup", str(backup_of(db, tmp_path)), "--approved-report", str(tmp_path / "nope.json"), "--confirm-rows", "20", "--i-have-explicit-approval"], db_path=db, csv_path=csv_path)
    assert code == 3 and sha(db) == before


def test_apply_with_a_report_from_a_different_state_is_refused(db, tmp_path):
    script = load_script()
    csv_path = write_csv(tmp_path / "m.csv")
    report = dry_run_report(script, db, csv_path, tmp_path)
    con = sqlite3.connect(db)
    con.execute("update call set start_time = '2026-12-25 09:00:00.000000' where id = 'CALL-0002'")
    con.commit(); con.close()
    before = sha(db)
    code = script.main(["--apply", "--backup", str(backup_of(db, tmp_path)), "--approved-report", str(report), "--confirm-rows", "20", "--i-have-explicit-approval"], db_path=db, csv_path=csv_path)
    assert code == 4 and sha(db) == before


def test_apply_with_everything_in_order_changes_exactly_twenty_rows(db, tmp_path):
    script = load_script()
    csv_path = write_csv(tmp_path / "m.csv")
    report = dry_run_report(script, db, csv_path, tmp_path)
    bk = backup_of(db, tmp_path)
    code = script.main(["--apply", "--backup", str(bk), "--approved-report", str(report), "--confirm-rows", "20", "--i-have-explicit-approval"], db_path=db, csv_path=csv_path)
    assert code == 0 and times(db)["CALL-0008"] == "2026-10-04 07:30:00.000000"
