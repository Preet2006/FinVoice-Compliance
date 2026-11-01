"""
The isolated evaluation database builder. Every test builds into a temporary folder from a temporary stand-in for
the production database; the real production database is never opened for writing and no audio file is read.
"""

import csv
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import Base
from app.models import AppUser, Call, Office, Rule, TranscriptSegment, Violation
from app.rules import RULES, evaluate_r1
from app.services import evaluation_db as ev
from app.services import llm_service, pipeline, transcription_service

BACKEND = Path(__file__).resolve().parents[2]
DATA = BACKEND / "data"
REAL_CSV, REAL_GT, REAL_AUDIO = DATA / "metadata.csv", DATA / "ground_truth_evaluation_v1.json", DATA / "audio"
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()


@pytest.fixture()
def source_db(tmp_path):
    """A stand-in for the production database: the organisation only (offices, users, rules), no calls."""
    path = tmp_path / "stand_in_production.db"
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    callers = sorted({r["caller_id"] for r in csv.DictReader(open(REAL_CSV, encoding="utf-8"))})
    offices = sorted({r["office_id"] for r in csv.DictReader(open(REAL_CSV, encoding="utf-8"))})
    for o in offices:
        session.add(Office(id=o, name=o))
    session.flush()
    session.add(AppUser(id="u-mgr", name="Manager", role="manager", office_id=offices[0]))
    session.flush()
    for c in callers:
        session.add(AppUser(id=c, name=f"Caller {c}", role="caller", office_id=offices[0], manager_id="u-mgr"))
    for r in RULES:
        session.add(Rule(**r))
    session.commit()
    session.close()
    engine.dispose()
    return path


@pytest.fixture()
def inputs(tmp_path):
    """Editable copies of the approved files, so a test can break one without touching the real ones."""
    d = tmp_path / "inputs"
    d.mkdir()
    shutil.copy(REAL_CSV, d / "metadata.csv")
    shutil.copy(REAL_GT, d / "ground_truth_evaluation_v1.json")
    return d


def plan_for(inputs_dir, audio=REAL_AUDIO):
    return ev.validate(inputs_dir / "metadata.csv", inputs_dir / "ground_truth_evaluation_v1.json", audio)


def edit_gt(inputs_dir, fn):
    p = inputs_dir / "ground_truth_evaluation_v1.json"
    doc = json.loads(p.read_text(encoding="utf-8"))
    fn(doc)
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


# ------------------------------------------------------------------ validation (read-only)
def test_the_real_approved_files_validate():
    plan = ev.validate(REAL_CSV, REAL_GT, REAL_AUDIO)
    assert plan.ok, plan.problems
    assert [c["call_id"] for c in plan.calls] == [f"CALL-{n:04d}" for n in range(1, 21)]
    assert [c["call_id"] for c in plan.calls if c["expected"]["R1"]] == ["CALL-0008", "CALL-0018"]
    assert {c["call_id"]: c["scenario_time"] for c in plan.calls if c["call_id"] in ("CALL-0008", "CALL-0012", "CALL-0016", "CALL-0018")} == {
        "CALL-0008": "07:30", "CALL-0012": "14:00", "CALL-0016": "15:00", "CALL-0018": "20:30"}


def test_validation_reads_only_file_sizes_never_audio_content(monkeypatch):
    opened = []
    real_open = open
    monkeypatch.setattr("builtins.open", lambda f, *a, **k: (opened.append(str(f)), real_open(f, *a, **k))[1])
    monkeypatch.setattr(Path, "read_bytes", lambda self: (opened.append(str(self)), real_open(self, "rb").read())[1])
    ev.validate(REAL_CSV, REAL_GT, REAL_AUDIO)
    assert [f for f in opened if f.lower().endswith((".wav", ".mp3"))] == []


def test_a_time_that_differs_between_the_two_files_is_refused(inputs):
    edit_gt(inputs, lambda d: d["calls"]["CALL-0012"].update(scenario_time="19:15", scenario_start_time="2026-10-06T19:15:00"))
    plan = plan_for(inputs)
    assert not plan.ok and any("CALL-0012" in p and "differ" in p for p in plan.problems)


def test_an_expected_r1_that_contradicts_the_application_rule_is_refused(inputs):
    edit_gt(inputs, lambda d: d["calls"]["CALL-0016"]["violations"].update(R1=True))  # 15:00 is in hours
    plan = plan_for(inputs)
    assert not plan.ok and any("CALL-0016" in p and "R1" in p for p in plan.problems)


@pytest.mark.parametrize("mutation,needle", [
    (lambda d: d["calls"]["CALL-0003"]["timestamp"].update(confirmed_recording_time="2026-10-02T11:00:00"), "confirmed_recording_time"),
    (lambda d: d["calls"]["CALL-0003"].update(audio_verified=True), "audio-verified"),
    (lambda d: d["metadata"].update(audio_verified=True), "verification"),
    (lambda d: d["calls"]["CALL-0003"].update(scenario_time="11:00:00"), "HH:MM"),
    (lambda d: d["calls"]["CALL-0003"]["violations"].pop("R6"), "R1-R6"),
    (lambda d: d["calls"].pop("CALL-0020"), "expected 20 calls"),
])
def test_unsafe_or_incomplete_ground_truth_is_refused(inputs, mutation, needle):
    edit_gt(inputs, mutation)
    plan = plan_for(inputs)
    assert not plan.ok and any(needle in p for p in plan.problems)


def test_a_missing_audio_file_is_reported(inputs, tmp_path):
    empty = tmp_path / "no_audio"
    empty.mkdir()
    plan = plan_for(inputs, audio=empty)
    assert not plan.ok and len([p for p in plan.problems if "not found" in p]) == 20


def test_a_csv_time_outside_the_format_is_refused(inputs):
    p = inputs / "metadata.csv"
    p.write_text(p.read_text(encoding="utf-8").replace("2026-10-04T07:30:00", "2026-10-04"), encoding="utf-8")  # a date with no time
    plan = plan_for(inputs)
    assert not plan.ok and any("CALL-0008" in x and "usable timestamp" in x for x in plan.problems)


# ------------------------------------------------------------------ build
def build(inputs, source_db, tmp_path, name="evaluation_v1.db"):
    plan = plan_for(inputs)
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    return ev.build(plan, source_db, out / name), out / name


def test_build_creates_an_isolated_database_with_the_approved_scenario_times(inputs, source_db, tmp_path):
    before = sha(source_db)
    manifest, path = build(inputs, source_db, tmp_path)
    assert sha(source_db) == before == manifest["source_database_sha256_before_and_after"]  # the source was only read
    assert manifest["integrity_check"] == "ok" and manifest["foreign_key_violations"] == 0
    assert manifest["row_counts"]["call"] == 20 and manifest["row_counts"]["rule"] == 6
    assert manifest["row_counts"]["transcript_segment"] == manifest["row_counts"]["violation"] == manifest["row_counts"]["review"] == 0
    con = sqlite3.connect(path)
    rows = {r[0]: r for r in con.execute("select id, start_time, status, has_audio, is_ground_truth, batch_id, score, audio_path from call")}
    assert len(rows) == 20 and all(r[2] == "queued" and r[3] == 1 and r[4] == 1 and r[5] == "evaluation-v1" and r[6] is None for r in rows.values())
    csv_times = {r["call_id"]: r["start_time"].replace("T", " ") for r in csv.DictReader(open(REAL_CSV, encoding="utf-8"))}
    assert {k: v[1][:19] for k, v in rows.items()} == csv_times
    assert rows["CALL-0018"][1][11:16] == "20:30" and rows["CALL-0012"][1][11:16] == "14:00"
    assert (path.with_suffix(".manifest.json")).is_file()


def test_the_database_says_its_times_are_simulated_and_unverified(inputs, source_db, tmp_path):
    manifest, path = build(inputs, source_db, tmp_path)
    saved = json.loads(path.with_suffix(".manifest.json").read_text(encoding="utf-8"))
    assert "SIMULATED" in saved["scenario_times"] and "none is confirmed" in saved["scenario_times"]
    assert saved["audio"] == "existence and size only; no audio file was opened or processed"
    assert saved["inputs"] == {"metadata_csv_sha256": sha(inputs / "metadata.csv"), "ground_truth_sha256": sha(inputs / "ground_truth_evaluation_v1.json")}


def test_the_application_rule_applied_to_the_built_rows_matches_the_expected_r1_labels(inputs, source_db, tmp_path):
    _, path = build(inputs, source_db, tmp_path)
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    session = sessionmaker(bind=engine)()
    gt = json.loads(REAL_GT.read_text(encoding="utf-8"))["calls"]
    for call in session.query(Call).all():
        assert (evaluate_r1(call.start_time).status == "violation") == gt[call.id]["violations"]["R1"], call.id
    assert sorted(c.id for c in session.query(Call).all() if evaluate_r1(c.start_time).status == "violation") == ["CALL-0008", "CALL-0018"]


def test_the_built_database_runs_the_real_pipeline_and_r1_follows_the_scenario_time(inputs, source_db, tmp_path, monkeypatch):
    _, path = build(inputs, source_db, tmp_path)
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    session = sessionmaker(bind=engine)()
    audio = tmp_path / "audio"
    audio.mkdir()
    monkeypatch.setattr(settings, "audio_dir", str(audio))
    for call in session.query(Call).filter(Call.id.in_(["CALL-0012", "CALL-0018"])):
        (audio / call.audio_path.split("/")[-1]).write_bytes(b"RIFFdummy")  # stand-in bytes; the real recordings are never used
    monkeypatch.setattr(transcription_service, "transcribe", lambda b, f, sleep_fn=None: [transcription_service.RawSegment(0, 0.0, 3.0, "Namaste main Pooja bol rahi hoon Shaha Finlease se loan payment ke baare mein")])
    from app.services import unified_analysis_service

    monkeypatch.setattr(unified_analysis_service, "analyse_call", lambda raw, call, **kw: (
        [llm_service.LabelledSegment(0, "agent", 0.0, 3.0, raw[0].text, 0.95)],
        llm_service.ComplianceResult(violations=[], borrower_sentiment="neutral", agent_stayed_calm=True)))
    for cid in ("CALL-0012", "CALL-0018"):
        pipeline.process_call(cid, session)
    r1 = {v.call_id: v.quote for v in session.query(Violation).filter(Violation.rule_code == "R1")}
    assert r1 == {"CALL-0018": "Call started at 20:30:00 IST (outside permitted hours 08:00-19:00)"}  # none for 14:00
    assert session.get(Call, "CALL-0018").score == 85 and session.get(Call, "CALL-0012").score == 100


def test_the_source_database_is_opened_read_only(inputs, source_db, tmp_path, monkeypatch):
    uris = []
    real = sqlite3.connect

    def spy(target, *a, **k):
        uris.append(str(target))
        return real(target, *a, **k)

    monkeypatch.setattr(ev.sqlite3, "connect", spy)
    build(inputs, source_db, tmp_path)
    source_opens = [u for u in uris if "stand_in_production" in u]
    assert source_opens and all("mode=ro" in u for u in source_opens)


# ------------------------------------------------------------------ refusals: nothing is written
def refused(inputs, source_db, out, **kw):
    with pytest.raises(ev.EvaluationBuildError) as exc:
        ev.build(plan_for(inputs), source_db, out, **kw)
    return exc.value.problems


def test_it_never_overwrites_an_existing_file(inputs, source_db, tmp_path):
    existing = tmp_path / "evaluation_old.db"
    existing.write_bytes(b"keep me")
    assert any("already exists" in p for p in refused(inputs, source_db, existing))
    assert existing.read_bytes() == b"keep me"


def test_it_refuses_the_source_database_and_any_protected_database(inputs, source_db, tmp_path):
    assert any("source or a protected" in p for p in refused(inputs, source_db, source_db))
    protected = tmp_path / "evaluation_prod.db"
    assert any("protected" in p for p in refused(inputs, source_db, protected, extra_protected=[protected]))
    assert not protected.exists()


def test_it_refuses_the_real_production_database_path(inputs, source_db):
    real = DATA / "compliance.db"
    before = sha(real)
    assert refused(inputs, source_db, real, extra_protected=[real])
    assert sha(real) == before


def test_the_target_name_must_say_evaluation(inputs, source_db, tmp_path):
    assert any("'evaluation'" in p for p in refused(inputs, source_db, tmp_path / "scratch.db"))


def test_it_refuses_a_backups_folder_and_a_missing_folder(inputs, source_db, tmp_path):
    (tmp_path / "backups").mkdir()
    assert any("backups" in p for p in refused(inputs, source_db, tmp_path / "backups" / "evaluation_x.db"))
    assert any("does not exist" in p for p in refused(inputs, source_db, tmp_path / "nope" / "evaluation_x.db"))


def test_a_caller_missing_from_the_source_is_refused_before_anything_is_written(inputs, tmp_path):
    empty = tmp_path / "empty_source.db"
    engine = create_engine(f"sqlite:///{empty.as_posix()}")
    Base.metadata.create_all(bind=engine)
    engine.dispose()
    out = tmp_path / "evaluation_x.db"
    with pytest.raises(ev.EvaluationBuildError) as exc:
        ev.build(plan_for(inputs), empty, out)
    assert "no such caller or office" in str(exc.value) and not out.exists()


def test_an_invalid_plan_cannot_be_built(inputs, source_db, tmp_path):
    edit_gt(inputs, lambda d: d["calls"].pop("CALL-0020"))
    out = tmp_path / "evaluation_x.db"
    with pytest.raises(ev.EvaluationBuildError):
        ev.build(plan_for(inputs), source_db, out)
    assert not out.exists() and not out.with_suffix(".manifest.json").exists()


# ------------------------------------------------------------------ the command line
def test_the_command_line_defaults_to_a_dry_run_that_writes_nothing(capsys, tmp_path, monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("build_evaluation_db", BACKEND / "scripts" / "build_evaluation_db.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    before = {p.name for p in DATA.iterdir()}
    assert mod.main([]) == 0
    out = capsys.readouterr().out
    assert "validation: OK" in out and "DRY RUN: nothing was written." in out
    assert {p.name for p in DATA.iterdir()} == before
    assert mod.main(["--build"]) == 2  # --build without --out is refused
