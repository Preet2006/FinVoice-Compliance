"""
READ-ONLY metadata reconciliation and R1 dry run for the 20 role-play calls.

Opens the database with SQLite `mode=ro` (it cannot write), reads
data/metadata.csv, and prints a mismatch report and an R1 table. It proposes
corrections; it never applies them.

    cd backend
    python scripts/audit_metadata.py            # text report
    python scripts/audit_metadata.py --json out.json
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy.engine import make_url  # noqa: E402

from app.config import settings  # noqa: E402
from app.services import metadata_audit as ma  # noqa: E402


def resolve_database() -> Path:
    path = Path(make_url(settings.database_url).database)
    return path if path.is_absolute() else (BACKEND_DIR / path).resolve()


def load_labels() -> dict:
    """Script-derived R1 expectations (NOT audio-verified). Calls 6-10 come from
    the proposed file because ground_truth.json still holds placeholders there."""
    labels = {}
    gt = json.loads((BACKEND_DIR / "data" / "ground_truth.json").read_text(encoding="utf-8"))["calls"]
    for call_id, entry in gt.items():
        labels[call_id] = entry["violations"]["R1"]
    proposed = BACKEND_DIR / "data" / "ground_truth_proposed_calls_06_10.json"
    if proposed.exists():
        for call_id, entry in json.loads(proposed.read_text(encoding="utf-8"))["calls"].items():
            labels[call_id] = entry["violations"]["R1"]
    return labels


def open_readonly(db_path: Path) -> sqlite3.Connection:
    """A connection that cannot write: any INSERT/UPDATE/DELETE raises."""
    con = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def main(argv=None, *, db_path=None, csv_path=None, audio_dir=None, labels=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", help="also write the full report to this file")
    args = parser.parse_args(argv)

    db_path = Path(db_path) if db_path else resolve_database()
    csv_path = Path(csv_path) if csv_path else BACKEND_DIR / "data" / "metadata.csv"
    audio_dir = Path(audio_dir) if audio_dir else Path(settings.audio_dir)
    if not db_path.is_file():
        print(f"Database not found: {db_path}")
        return 2
    con = open_readonly(db_path)

    expected = [f"CALL-{n:04d}" for n in range(1, 21)]
    db_rows = {r["id"]: dict(r) for r in con.execute("select * from call where is_ground_truth=1")}
    user_offices = {r["id"]: r["office_id"] for r in con.execute("select id, office_id from app_user")}
    csv_rows = ma.load_csv_rows(csv_path)

    audits = ma.audit_calls(csv_rows, db_rows, audio_dir, expected)
    dry_run = ma.r1_dry_run(audits, labels if labels is not None else load_labels())

    print(f"Database : {db_path}\nCSV      : {csv_path}\nAudio dir: {audio_dir}\n")
    print("MISMATCHES (CSV vs database)")
    counts = {}
    for a in audits:
        for d in a.discrepancies:
            counts[d.field] = counts.get(d.field, 0) + 1
    print("  calls differing per field:", counts or "none")
    for a in audits:
        for p in a.problems:
            print(f"  {a.call_id}: {p}")
    conflicts = ma.office_conflicts(csv_rows, user_offices)
    print(f"  CSV rows whose office is not the caller's own office: {len(conflicts)}")

    print("\nR1 DRY RUN (nothing is changed)")
    print(f"  {'call':10s} {'database start':27s} {'csv start':20s} {'recording':13s} {'now':10s} {'proposed':10s} {'verdict':26s} label")
    for r in dry_run:
        print(f"  {r['call_id']:10s} {str(r['db_start'])[:26]:27s} {str(r['csv_start']):20s} {r['recording']:13s} {r['current_r1']:10s} {r['proposed_r1']:10s} {r['verdict']:26s} {r['expected_label']}")

    metadata_only = [dict(r) for r in con.execute("select start_time from call where is_ground_truth=0")]
    only = ma.audit_metadata_only(metadata_only) if metadata_only else None
    if only:
        print("\nMETADATA-ONLY CALLS (no CSV row; read-only, nothing is proposed or changed)")
        print(f"  calls: {only['calls']} | R1 as stored: {only['r1_as_stored']}")
        print(f"  one clock capture (distinct microsecond values): {only.get('distinct_microsecond_values')} | seed clock offset window: {only.get('clock_offset_bounds')} ({only.get('clock_offset_window_seconds')}s)")
        if only["explanation_holds"]:
            print(f"  seed-artifact explanation HOLDS: all {only['originally_in_permitted_hours']} were originally in permitted hours; {only['stored_r1_violations_explained_as_artifact']} stored R1 violations are artifacts")
        else:
            print("  seed-artifact explanation does NOT hold: stored times cannot be explained that way; no claim is made")

    if args.json:
        payload = {
            "database": str(db_path),
            "dry_run": dry_run,
            "discrepancies": {a.call_id: [d.__dict__ for d in a.discrepancies] for a in audits},
            "problems": {a.call_id: a.problems for a in audits if a.problems},
            "office_conflicts": conflicts,
            "metadata_only_audit": only,
        }
        Path(args.json).write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        print(f"\nFull report written to {args.json}")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
