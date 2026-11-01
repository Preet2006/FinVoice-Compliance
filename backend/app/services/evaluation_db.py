"""
An isolated EVALUATION database built from the approved scenario files.

Why: the application decides R1 from `call.start_time`. The production database holds older times for some
role-play calls, and the approved scenario times are SIMULATED (not recording times). An evaluation run must
use the approved times without touching production and without pretending they are real, so it runs against
its own database file, built here.

Inputs (the approved, synchronised pair):
  backend/data/metadata.csv                      the scenario start_time of each call
  backend/data/ground_truth_evaluation_v1.json   the expected labels, the same scenario times, provenance flags

Guarantees (all enforced, all tested):
  * nothing is written unless `build()` is called; `validate()` is read-only;
  * the new file must not exist, must have 'evaluation' in its name, and can never be the production
    database, the source database or anything inside a backups folder;
  * the source database is opened read-only (SQLite mode=ro) and its hash is checked before and after;
  * audio files are only checked for existence and size. They are never opened or read;
  * the two input files must agree on every call's scenario time, and the expected R1 label must agree with
    the application's own R1 rule for that time, or the build is refused;
  * the file records that its times are simulated and that no recording time is confirmed.
"""

import csv
import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine, text

from app.config import settings
from app.db import Base
from app.rules import R1_VIOLATION, evaluate_r1
from app.services.upload_service import _try_parse_timestamp

RULES = ("R1", "R2", "R3", "R4", "R5", "R6")
EXPECTED_CALLS = 20
BATCH_ID = "evaluation-v1"
HHMM = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


class EvaluationBuildError(Exception):
    """The inputs or the target are not safe to build from. Nothing was written."""

    def __init__(self, problems):
        self.problems = list(problems) if isinstance(problems, (list, tuple)) else [str(problems)]
        super().__init__("; ".join(self.problems))


@dataclass
class Plan:
    calls: list = field(default_factory=list)  # one dict per call, in call-id order
    problems: list = field(default_factory=list)
    metadata_sha256: str = ""
    ground_truth_sha256: str = ""

    @property
    def ok(self) -> bool:
        return not self.problems


def _sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate(metadata_csv, ground_truth_json, audio_dir) -> Plan:
    """Read-only. Checks the approved files against each other, the R1 rule and the audio folder."""
    plan = Plan()
    metadata_csv, ground_truth_json, audio_dir = Path(metadata_csv), Path(ground_truth_json), Path(audio_dir)
    try:
        rows = {r["call_id"]: r for r in csv.DictReader(open(metadata_csv, encoding="utf-8"))}
        gt = json.loads(ground_truth_json.read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError) as exc:
        plan.problems.append(f"cannot read the input files: {exc}")
        return plan
    plan.metadata_sha256, plan.ground_truth_sha256 = _sha(metadata_csv), _sha(ground_truth_json)
    calls = gt.get("calls", {})
    problems = plan.problems
    if len(rows) != EXPECTED_CALLS or len(calls) != EXPECTED_CALLS:
        problems.append(f"expected {EXPECTED_CALLS} calls in both files, found {len(rows)} in the CSV and {len(calls)} in the ground truth")
    if set(rows) != set(calls):
        problems.append(f"call ids differ: only in CSV {sorted(set(rows) - set(calls))}, only in ground truth {sorted(set(calls) - set(rows))}")
    meta = gt.get("metadata", {})
    if meta.get("audio_verified") is not False or meta.get("actual_recording_times_verified") is not False:
        problems.append("the ground truth must not claim audio or recording-time verification")

    for call_id in sorted(set(rows) & set(calls)):
        row, c = rows[call_id], calls[call_id]
        start = _try_parse_timestamp(row.get("start_time"))
        if start is None:
            problems.append(f"{call_id}: the CSV start_time {row.get('start_time')!r} is not a usable timestamp")
            continue
        scenario = c.get("scenario_time", "")
        if not HHMM.fullmatch(scenario or ""):
            problems.append(f"{call_id}: scenario_time {scenario!r} is not 24-hour HH:MM")
        if start.strftime("%H:%M") != scenario or row["start_time"] != c.get("scenario_start_time"):
            problems.append(f"{call_id}: the CSV time {row['start_time']} and the ground-truth scenario time {c.get('scenario_start_time')} differ")
        if c.get("timestamp", {}).get("confirmed_recording_time") is not None:
            problems.append(f"{call_id}: confirmed_recording_time must be null")
        if c.get("audio_verified") is not False or c.get("speaker_labels_verified") is not False:
            problems.append(f"{call_id}: the call must not be marked audio-verified or speaker-verified")
        labels = c.get("violations", {})
        if set(labels) != set(RULES) or any(type(labels[r]) is not bool for r in RULES):
            problems.append(f"{call_id}: the labels must be a boolean for each of R1-R6")
        else:
            by_rule = evaluate_r1(start).status == R1_VIOLATION
            if labels["R1"] != by_rule:
                problems.append(f"{call_id}: expected R1 is {labels['R1']} but the application's R1 rule says {by_rule} for {start.strftime('%H:%M')}")
        audio = audio_dir / row["filename"]
        if not audio.is_file():
            problems.append(f"{call_id}: audio file {row['filename']} not found in {audio_dir}")
        plan.calls.append({
            "call_id": call_id, "filename": row["filename"], "caller_id": row["caller_id"], "office_id": row["office_id"],
            "account_type": row["account_type"], "language": row["language"] or None, "start_time": start,
            "scenario_time": scenario, "expected": {r: labels.get(r) for r in RULES},
            "audio_bytes": audio.stat().st_size if audio.is_file() else None,  # existence and size only: never opened
        })
    return plan


def _refuse_unsafe_target(out_path: Path, source_db: Path, extra_protected=()):
    problems = []
    out = out_path.resolve()
    protected = {Path(source_db).resolve(), *(Path(p).resolve() for p in extra_protected)}
    if out in protected:
        problems.append("the target is the source or a protected database")
    if "evaluation" not in out.name.lower():
        problems.append("the target file name must contain 'evaluation'")
    if any(part.lower() == "backups" for part in out.parts):
        problems.append("the target must not be inside a backups folder")
    if out.exists():
        problems.append("the target already exists; an existing database is never overwritten")
    if not out.parent.is_dir():
        problems.append(f"the target folder {out.parent} does not exist")
    if problems:
        raise EvaluationBuildError(problems)
    return out


def build(plan: Plan, source_db, out_path, extra_protected=()) -> dict:
    """Creates the evaluation database. Raises EvaluationBuildError, having written nothing, if anything is unsafe."""
    if not plan.ok:
        raise EvaluationBuildError(plan.problems)
    source_db = Path(source_db)
    out = _refuse_unsafe_target(Path(out_path), source_db, extra_protected)
    before = _sha(source_db)
    src = sqlite3.connect(f"file:{source_db.as_posix()}?mode=ro", uri=True)  # cannot write
    try:
        org = {t: src.execute(f"select * from {t}").fetchall() for t in ("office", "app_user", "rule")}
        cols = {t: [r[1] for r in src.execute(f"pragma table_info({t})")] for t in org}
    finally:
        src.close()
    callers = {r[cols["app_user"].index("id")] for r in org["app_user"]}
    offices = {r[cols["office"].index("id")] for r in org["office"]}
    missing = sorted({c["caller_id"] for c in plan.calls} - callers) + sorted({c["office_id"] for c in plan.calls} - offices)
    if missing:
        raise EvaluationBuildError([f"the source database has no such caller or office: {missing}"])

    engine = create_engine(f"sqlite:///{out.as_posix()}")
    try:
        Base.metadata.create_all(bind=engine)
        with engine.begin() as conn:
            for table in ("office", "app_user", "rule"):
                rows = sorted(org[table], key=lambda r: (r[cols[table].index("manager_id")] is not None) if table == "app_user" else 0)
                names = cols[table]
                conn.execute(text(f"insert into {table} ({', '.join(names)}) values ({', '.join(':' + n for n in names)})"), [dict(zip(names, r)) for r in rows])
            now = datetime.utcnow()
            for c in plan.calls:
                conn.execute(text(
                    "insert into call (id, caller_id, office_id, account_type, start_time, duration_seconds, language, has_audio, audio_path, "
                    "status, is_ground_truth, batch_id, created_at) values (:id, :caller, :office, :acct, :start, NULL, :lang, 1, :path, "
                    "'queued', 1, :batch, :now)"),
                    {"id": c["call_id"], "caller": c["caller_id"], "office": c["office_id"], "acct": c["account_type"], "start": c["start_time"],
                     "lang": c["language"], "path": f"data/audio/{c['filename']}", "batch": BATCH_ID, "now": now})
        with engine.connect() as conn:
            integrity = conn.execute(text("pragma integrity_check")).scalar()
            fk = len(conn.execute(text("pragma foreign_key_check")).fetchall())
            counts = {t: conn.execute(text(f"select count(*) from {t}")).scalar() for t in ("office", "app_user", "rule", "call", "transcript_segment", "violation", "review")}
    finally:
        engine.dispose()
    after = _sha(source_db)
    if before != after:
        raise EvaluationBuildError(["the source database changed during the build"])  # cannot happen with mode=ro; checked anyway
    manifest = {
        "kind": "evaluation database (isolated, never the production database)",
        "database": str(out), "batch_id": BATCH_ID,
        "scenario_times": "SIMULATED role-play scenario times (from metadata.csv, identical in the ground truth). Not recording times; none is confirmed.",
        "inputs": {"metadata_csv_sha256": plan.metadata_sha256, "ground_truth_sha256": plan.ground_truth_sha256},
        "source_database_sha256_before_and_after": before,
        "integrity_check": integrity, "foreign_key_violations": fk, "row_counts": counts,
        "calls": [{"call_id": c["call_id"], "scenario_time": c["scenario_time"], "expected_R1": c["expected"]["R1"], "audio_bytes": c["audio_bytes"]} for c in plan.calls],
        "audio": "existence and size only; no audio file was opened or processed",
    }
    out.with_suffix(".manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest
