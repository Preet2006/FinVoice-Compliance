"""
Processes the queued calls of the EVALUATION database through the (single) analysis pipeline, one at a time.

    cd backend
    python scripts/run_evaluation.py --db data/evaluation/evaluation_v1.db [--only CALL-0001 ...] [--pause 35]

It calls the configured providers (Groq transcription and analysis), so it is run only on purpose. Safeguards:
  * the target must be an existing SQLite file whose name contains "evaluation"; it can never be the production DB;
  * the target is set through the process environment (DATABASE_URL); no .env file is read for it or modified;
  * calls run sequentially with a pause between them to respect the provider's per-minute limits;
  * only calls still "queued" are processed (failed ones only with --include-failed); a failed call is recorded as failed
    with its reason and is not retried automatically, and a provider limit or key error stops the run (the rest stay queued);
  * nothing is invented: a call that does not finish is left failed or queued, never filled in.
"""

import argparse
import os
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

STOP_WORDS = ("rate or usage limit", "quota", "api key", "rejected", "unauthor", "authentication")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", required=True, help="the evaluation database file (name must contain 'evaluation')")
    parser.add_argument("--only", nargs="*", help="process only these call ids")
    parser.add_argument("--include-failed", action="store_true", help="also re-run calls that previously failed (an explicit, recorded retry)")
    parser.add_argument("--reprocess", action="store_true", help="also re-run already analysed calls (requires --only); their results are replaced")
    parser.add_argument("--pause", type=float, default=35.0, help="seconds to wait between calls")
    args = parser.parse_args()

    db_path = Path(args.db).resolve()
    if not db_path.is_file() or "evaluation" not in db_path.name.lower():
        print(f"refusing: {db_path} is not an existing evaluation database file")
        return 2
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"  # process-scoped; takes precedence over .env

    from app.config import settings
    from app.db import SessionLocal
    from app.models import Call
    from app.services import pipeline

    assert Path(settings.database_url.replace("sqlite:///", "")).resolve() == db_path, "the app is not pointed at the evaluation DB"
    print(f"database: {db_path}")
    print(f"provider key configured: {bool(settings.groq_api_key)}")  # never the key itself

    session = SessionLocal()
    try:
        if args.reprocess and not args.only:
            print("refusing: --reprocess requires --only with the call ids to re-run")
            return 2
        wanted = ["queued", "failed"] if args.include_failed else ["queued"]
        if args.reprocess:
            wanted.append("ready")
        ids = [c.id for c in session.query(Call).filter(Call.status.in_(wanted)).order_by(Call.id)]
        if args.only:
            ids = [i for i in ids if i in set(args.only)]
        print(f"{len(ids)} call(s) to process: {', '.join(ids)}")
        for n, call_id in enumerate(ids):
            started = time.time()
            try:
                call = pipeline.process_call(call_id, session)
            except Exception as exc:  # recorded, never hidden; the pipeline normally records failures itself
                session.rollback()
                call = session.get(Call, call_id)
                print(f"{call_id}: EXCEPTION {type(exc).__name__}: {str(exc)[:200]}")
            session.expire_all()
            call = session.get(Call, call_id)
            reason = call.failure_reason or ""
            print(f"{call_id}: status={call.status} score={call.score} seconds={time.time() - started:.0f}" + (f" failure={reason[:200]}" if reason else ""), flush=True)
            if call.status == "failed" and any(w in reason.lower() for w in STOP_WORDS):
                print("stopping: a provider limit or key error; the remaining calls stay queued")
                return 1
            if n < len(ids) - 1:
                time.sleep(args.pause)
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
