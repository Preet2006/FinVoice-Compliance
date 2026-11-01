"""
Correction of call.start_time for the 20 ground-truth calls, from metadata.csv.

DEFAULT IS A READ-ONLY DRY RUN: it opens the database with SQLite `mode=ro`,
prints every before/after value and the exact SQL, and verifies that exactly
20 rows would change. It writes nothing.

    cd backend
    python scripts/correct_start_times.py                    # dry run
    python scripts/correct_start_times.py --report out.json  # also save it

Applying is deliberately hard to do by accident. It needs ALL of:
    --apply --backup <fresh backup file> --approved-report <the approved dry-run JSON>
    --confirm-rows 20 --i-have-explicit-approval
It refuses unless the backup is byte-identical to the live database AND the
changes it would make are identical to the approved dry-run report.
Do not use --apply without explicit approval of the dry-run report.
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
from app.services import start_time_correction as stc  # noqa: E402
from app.services.metadata_audit import load_csv_rows  # noqa: E402

EXPECTED_IDS = [f"CALL-{n:04d}" for n in range(1, 21)]


def resolve_database() -> Path:
    path = Path(make_url(settings.database_url).database)
    return path if path.is_absolute() else (BACKEND_DIR / path).resolve()


def main(argv=None, *, db_path=None, csv_path=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--report", help="write the dry-run report to this JSON file")
    parser.add_argument("--apply", action="store_true", help="WRITE the correction (see guards in the header)")
    parser.add_argument("--backup", help="the verified backup file (required with --apply)")
    parser.add_argument("--approved-report", help="the dry-run JSON that was approved (required with --apply)")
    parser.add_argument("--confirm-rows", type=int, help="must equal 20 (required with --apply)")
    parser.add_argument("--i-have-explicit-approval", action="store_true", help="required with --apply")
    args = parser.parse_args(argv)

    db_path = Path(db_path) if db_path else resolve_database()
    csv_path = Path(csv_path) if csv_path else BACKEND_DIR / "data" / "metadata.csv"
    if not db_path.is_file():
        print(f"Database not found: {db_path}")
        return 2
    csv_rows = load_csv_rows(csv_path)

    con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)  # cannot write
    plan = stc.build_plan(con, csv_rows, EXPECTED_IDS)
    con.close()

    print(f"Database : {db_path}\nCSV      : {csv_path}\nRevision : {plan.revision} (expected {stc.EXPECTED_REVISION})\n")
    print(f"{'call':10s} {'before (stored)':27s} {'after (from CSV)':27s} R1 before -> after")
    for c in plan.changes:
        flag = "   <-- R1 outcome changes" if c.r1_before != c.r1_after else ""
        print(f"{c.call_id:10s} {c.before:27s} {c.after:27s} {c.r1_before} -> {c.r1_after}{flag}")
    print(f"\nrows that would change: {len(plan.changes)} (required: {stc.EXPECTED_ROWS}) | already correct: {len(plan.unchanged)}")
    print("only column touched: start_time | rows outside the 20 ground-truth calls touched: 0")
    for p in plan.problems:
        print(f"BLOCKED: {p}")
    print("\nSQL that --apply would run, as one transaction:\n" + stc.render_sql(plan))

    if args.report:
        Path(args.report).write_text(json.dumps({
            "database": str(db_path), "revision": plan.revision, "ok": plan.ok, "problems": plan.problems,
            "changes": [c.__dict__ for c in plan.changes], "unchanged": plan.unchanged, "sql": stc.render_sql(plan),
        }, indent=2), encoding="utf-8")
        print(f"\nReport written to {args.report}")

    if not args.apply:
        print("\nDRY RUN ONLY: nothing was written.")
        return 0 if plan.ok else 1

    if not (args.backup and args.approved_report and args.confirm_rows == stc.EXPECTED_ROWS and args.i_have_explicit_approval):
        print("\nREFUSED: --apply needs --backup, --approved-report, --confirm-rows 20 and --i-have-explicit-approval.")
        return 3
    try:
        approved = json.loads(Path(args.approved_report).read_text(encoding="utf-8"))["changes"]
    except (OSError, ValueError, KeyError) as exc:
        print(f"\nREFUSED: cannot read the approved report: {exc}")
        return 3
    try:
        applied = stc.apply_plan(db_path, csv_rows, EXPECTED_IDS, args.backup, approved)
    except stc.CorrectionRefused as exc:
        print(f"\nREFUSED, nothing was written: {exc}")
        return 4
    print(f"\nAPPLIED: {len(applied.changes)} rows updated in one transaction.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
