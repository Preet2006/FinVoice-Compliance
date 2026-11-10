"""Build an isolated evaluation database for the company's metadata format.

This command is intentionally separate from the frozen V4 builder. It reads
``call_metadata_.csv``, ``answer_key.csv`` and the audio directory, then creates
a new evaluation database. It never writes the source database and never calls
an external provider.

Example (validation only):
    python scripts/build_company_evaluation_db.py

Example (create a new database; the target must not already exist):
    python scripts/build_company_evaluation_db.py --build \
      --out data/evaluation/company_v1.db
"""

import argparse
import csv
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import Base  # noqa: E402
from app.models import AppUser, Call, Office, Rule  # noqa: E402
from app.rules import R1_VIOLATION, evaluate_r1  # noqa: E402
from app.services.upload_service import _try_parse_timestamp, sanitize_filename  # noqa: E402

COMPANY_COLUMNS = {
    "call_id", "audio_file", "caller_id", "caller_name", "office",
    "manager_id", "manager_name", "start_time", "language",
}
RULES = ("R1", "R2", "R3", "R4", "R5", "R6")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_db() -> Path:
    raw = settings.database_url.replace("sqlite:///", "", 1)
    path = Path(raw)
    return path if path.is_absolute() else (BACKEND_DIR / path).resolve()


def read_inputs(metadata_path: Path, answer_path: Path, audio_dir: Path, account_type: str):
    problems: list[str] = []
    rows = list(csv.DictReader(metadata_path.open(encoding="utf-8-sig", newline="")))
    if not rows:
        problems.append("metadata CSV has no data rows")
    fieldnames = set(rows[0]) if rows else set()
    missing = sorted(COMPANY_COLUMNS - fieldnames)
    if missing:
        problems.append(f"metadata CSV missing columns: {', '.join(missing)}")

    answer_rows = list(csv.DictReader(answer_path.open(encoding="utf-8-sig", newline="")))
    expected_ids = {r.get("call_id", "").strip() for r in rows}
    answer_ids = {r.get("call_id", "").strip() for r in answer_rows}
    if expected_ids != answer_ids:
        problems.append(f"metadata/answer-key IDs differ: metadata-only={sorted(expected_ids - answer_ids)}, answer-only={sorted(answer_ids - expected_ids)}")

    answers = {call_id: {rule: False for rule in RULES} for call_id in expected_ids if call_id}
    answer_details = {call_id: [] for call_id in expected_ids if call_id}
    for item in answer_rows:
        call_id = item.get("call_id", "").strip()
        rule = item.get("rule_code", "").strip().upper()
        if rule == "NONE":
            continue
        if rule not in RULES:
            problems.append(f"{call_id}: unsupported answer-key rule {rule!r}")
            continue
        answers.setdefault(call_id, {r: False for r in RULES})[rule] = True
        answer_details.setdefault(call_id, []).append(dict(item))

    seen = set()
    calls = []
    for line, row in enumerate(rows, start=2):
        call_id = (row.get("call_id") or "").strip()
        filename = (row.get("audio_file") or "").strip()
        if not call_id or call_id in seen:
            problems.append(f"metadata line {line}: missing or duplicate call_id")
            continue
        seen.add(call_id)
        if sanitize_filename(filename) is None:
            problems.append(f"{call_id}: unsafe or unsupported audio_file {filename!r}")
        audio = audio_dir / filename
        if not audio.is_file():
            problems.append(f"{call_id}: audio file not found: {audio}")
        start = _try_parse_timestamp(row.get("start_time"))
        if start is None:
            problems.append(f"{call_id}: invalid start_time {row.get('start_time')!r}")
        if not row.get("caller_id", "").strip() or not row.get("caller_name", "").strip():
            problems.append(f"{call_id}: caller identity is incomplete")
        if not row.get("manager_id", "").strip() or not row.get("manager_name", "").strip():
            problems.append(f"{call_id}: manager identity is incomplete")
        if not row.get("office", "").strip() or not row.get("language", "").strip():
            problems.append(f"{call_id}: office or language is empty")
        if start is not None and call_id in answers:
            r1 = evaluate_r1(start).status == R1_VIOLATION
            if answers[call_id]["R1"] != r1:
                problems.append(f"{call_id}: answer key R1={answers[call_id]['R1']} disagrees with start_time rule ({r1})")
        calls.append({
            "call_id": call_id,
            "filename": filename,
            "caller_id": row.get("caller_id", "").strip(),
            "caller_name": row.get("caller_name", "").strip(),
            "office_name": row.get("office", "").strip(),
            "manager_id": row.get("manager_id", "").strip(),
            "manager_name": row.get("manager_name", "").strip(),
            "language": row.get("language", "").strip(),
            "start_time": start,
            "account_type": account_type,
            "audio_bytes": audio.stat().st_size if audio.is_file() else None,
        })
    return calls, answers, answer_details, problems


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "office"


def build(calls, answers, answer_details, source: Path, out: Path, metadata: Path, answer_key: Path, audio_dir: Path):
    if out.exists():
        raise ValueError(f"refusing to overwrite existing database: {out}")
    if "evaluation" not in out.name.lower():
        raise ValueError("output database name must contain 'evaluation'")
    if out.resolve() == source.resolve():
        raise ValueError("output cannot be the source database")

    before = sha256(source)
    src_engine = create_engine(f"sqlite:///{source.resolve()}")
    Src = sessionmaker(bind=src_engine)
    src_session = Src()
    try:
        offices = src_session.query(Office).all()
        users = src_session.query(AppUser).all()
        rules = src_session.query(Rule).all()
        office_copies = [Office(id=o.id, name=o.name, created_at=o.created_at) for o in offices]
        user_copies = [AppUser(id=u.id, name=u.name, role=u.role, office_id=u.office_id, manager_id=u.manager_id,
                               password_hash=u.password_hash, is_active=u.is_active, created_at=u.created_at) for u in users]
        rule_copies = [Rule(code=r.code, name=r.name, description=r.description, default_severity=r.default_severity) for r in rules]
    finally:
        src_session.close()
        src_engine.dispose()

    engine = create_engine(f"sqlite:///{out.resolve()}")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        db.add_all(office_copies)
        db.add_all(user_copies)
        db.add_all(rule_copies)
        db.flush()
        by_office = {o.name.casefold(): o for o in db.query(Office).all()}
        for item in calls:
            office = by_office.get(item["office_name"].casefold())
            if office is None:
                office = Office(id=f"company-office-{slug(item['office_name'])}", name=item["office_name"])
                db.add(office)
                db.flush()
                by_office[item["office_name"].casefold()] = office
            manager = db.get(AppUser, item["manager_id"])
            if manager is None:
                manager = AppUser(id=item["manager_id"], name=item["manager_name"], role="manager", office_id=office.id)
                db.add(manager)
                db.flush()
            elif manager.role != "manager" or manager.name != item["manager_name"] or manager.office_id != office.id:
                raise ValueError(f"manager conflict for {item['call_id']}: {item['manager_id']}")
            caller = db.get(AppUser, item["caller_id"])
            if caller is None:
                caller = AppUser(id=item["caller_id"], name=item["caller_name"], role="caller", office_id=office.id, manager_id=manager.id)
                db.add(caller)
                db.flush()
            elif caller.role != "caller" or caller.name != item["caller_name"] or caller.office_id != office.id or caller.manager_id != manager.id:
                raise ValueError(f"caller conflict for {item['call_id']}: {item['caller_id']}")
            db.add(Call(id=item["call_id"], caller_id=caller.id, office_id=office.id, account_type=item["account_type"],
                        start_time=item["start_time"], language=item["language"], has_audio=True,
                        audio_path=f"data/audio/{item['filename']}", status="queued", is_ground_truth=True,
                        batch_id="company-v1"))
        db.commit()
        with engine.connect() as conn:
            conn.execute(text("create table if not exists alembic_version (version_num varchar(32) not null)"))
            conn.execute(text("delete from alembic_version"))
            conn.execute(text("insert into alembic_version(version_num) values ('7c4d9a1e2b6f')"))
            conn.commit()
            integrity = conn.execute(text("pragma integrity_check")).scalar()
            fk = conn.execute(text("pragma foreign_key_check")).fetchall()
            counts = {t: conn.execute(text(f"select count(*) from {t}")).scalar() for t in ("office", "app_user", "rule", "call")}
    finally:
        db.close()
        engine.dispose()
    after = sha256(source)
    if before != after:
        raise ValueError("source database changed during build")
    manifest = {
        "kind": "isolated company evaluation database",
        "database": str(out.resolve()), "batch_id": "company-v1",
        "metadata_sha256": sha256(metadata), "answer_key_sha256": sha256(answer_key),
        "source_database_sha256": before, "account_type_default": calls[0]["account_type"] if calls else None,
        "integrity_check": integrity, "foreign_key_violations": len(fk), "row_counts": counts,
        "audio": "validated by existence and size only; not opened or processed",
        "answer_key": answers, "answer_details": answer_details,
        "calls": [{"call_id": c["call_id"], "filename": c["filename"], "audio_bytes": c["audio_bytes"],
                   "scenario_time": c["start_time"].strftime("%H:%M")} for c in calls],
    }
    out.with_suffix(".manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", default=str(BACKEND_DIR / "data/company_v1/call_metadata_.csv"))
    parser.add_argument("--answer-key", default=str(BACKEND_DIR / "data/company_v1/answer_key.csv"))
    parser.add_argument("--audio-dir", default=str(BACKEND_DIR / "data/company_v1"))
    parser.add_argument("--out", default=str(BACKEND_DIR / "data/evaluation/company_v1.db"))
    parser.add_argument("--account-type", choices=("personal_loan", "credit_card"), default="personal_loan")
    parser.add_argument("--build", action="store_true")
    args = parser.parse_args(argv)
    metadata, answer_key, audio_dir = Path(args.metadata), Path(args.answer_key), Path(args.audio_dir)
    calls, answers, details, problems = read_inputs(metadata, answer_key, audio_dir, args.account_type)
    print(f"calls: {len(calls)}; metadata: {metadata}; answer key: {answer_key}; audio: {audio_dir}")
    for problem in problems:
        print(f"PROBLEM: {problem}")
    if problems:
        print("validation: FAILED")
        return 1
    print("validation: OK")
    if not args.build:
        print("DRY RUN: nothing was written.")
        return 0
    try:
        manifest = build(calls, answers, details, source_db(), Path(args.out), metadata, answer_key, audio_dir)
    except (OSError, ValueError) as exc:
        print(f"REFUSED: {exc}")
        return 2
    print(f"BUILT: {manifest['database']} {manifest['row_counts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

