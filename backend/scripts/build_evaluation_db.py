"""
Builds the isolated EVALUATION database from the approved metadata.csv and ground_truth_evaluation_v1.json.

    cd backend
    python scripts/build_evaluation_db.py                      # DRY RUN: validates and prints the plan, writes nothing
    python scripts/build_evaluation_db.py --build --out data/evaluation/evaluation_v1.db

The production database is only ever READ (SQLite mode=ro) to copy offices, users and rules, and the build refuses
to write to it. No audio file is opened. No provider is called. Do not run --build without approval.
"""

import argparse
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy.engine import make_url  # noqa: E402

from app.config import settings  # noqa: E402
from app.services import evaluation_db  # noqa: E402


def production_db() -> Path:
    path = Path(make_url(settings.database_url).database)
    return path if path.is_absolute() else (BACKEND_DIR / path).resolve()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--build", action="store_true", help="actually create the evaluation database (default: dry run)")
    p.add_argument("--out", help="the new evaluation database file (must not exist; name must contain 'evaluation')")
    args = p.parse_args(argv)
    data = BACKEND_DIR / "data"
    plan = evaluation_db.validate(data / "metadata.csv", data / "ground_truth_evaluation_v1.json", data / "audio")
    print(f"{'call':10s} {'scenario':8s} expected R1   audio")
    for c in plan.calls:
        print(f"{c['call_id']:10s} {c['scenario_time']:8s} {str(c['expected']['R1']):11s} {c['audio_bytes']} bytes")
    for problem in plan.problems:
        print("PROBLEM:", problem)
    print("\nvalidation:", "OK" if plan.ok else "FAILED")
    if not args.build:
        print("DRY RUN: nothing was written.")
        return 0 if plan.ok else 1
    if not (plan.ok and args.out):
        print("REFUSED: --build needs a valid plan and --out.")
        return 2
    try:
        manifest = evaluation_db.build(plan, production_db(), Path(args.out), extra_protected=[production_db()])
    except evaluation_db.EvaluationBuildError as exc:
        print("REFUSED, nothing was written:", exc)
        return 3
    print("BUILT:", manifest["database"], manifest["row_counts"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
