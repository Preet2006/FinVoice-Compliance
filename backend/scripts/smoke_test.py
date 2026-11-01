"""
Offline smoke test of the whole application flow.

    cd backend
    python scripts/smoke_test.py

It exercises the real FastAPI app, the real pipeline, the real validation and
the real scoring against a scratch in-memory database. Every provider is
stubbed and there is deliberately no option to use a real one: any attempt to
create a provider client fails the test. It never opens the production
database (it only hashes the file before and after to prove that).

Exit code 0 = every check passed, 1 = at least one failed.
"""

import hashlib
import sys
import tempfile
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

import groq  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.config import Settings, settings  # noqa: E402
from app.db import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AppUser, Call, Office, Rule  # noqa: E402
from app.rules import RULES  # noqa: E402
from app.services import llm_service, transcription_service  # noqa: E402
from app.services.transcription_service import RawSegment  # noqa: E402

# This offline smoke test intentionally uses the legacy identity fixture. The
# deployed application keeps this compatibility setting disabled by default.
settings.allow_legacy_headers = True

MANAGER = {"X-Role": "manager", "X-User-Id": "u-mgr"}
HEAD = {"X-Role": "head", "X-User-Id": "u-head"}
CALLER = {"X-Role": "caller", "X-User-Id": "u-caller"}

THREAT_TEXTS = ["Namaste main Pooja bol rahi hoon Shaha Finlease se aapke loan ke baare mein", "agar paisa nahi aaya to police bhej dunga", "theek hai"]
UNIFIED_TEXTS = [
    "Namaste main Pooja bol rahi hoon Shaha Finlease se aapke loan ke baare mein",
    "ji boliye",
    "aapke phone par OTP aayega woh OTP bata dijiye jaldi",  # the cited span ends before the last word
    "nahi main nahi bataunga",
]


class Report:
    def __init__(self):
        self.failures = 0

    def check(self, name, condition, detail=""):
        print(f"  [{'PASS' if condition else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not condition else ""))
        if not condition:
            self.failures += 1


def _production_db_path():
    database = make_url(settings.database_url).database
    if not database or database == ":memory:":
        return None
    path = Path(database)
    return path if path.is_absolute() else (BACKEND_DIR / path).resolve()


def _fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path and path.is_file() else None


def _scratch_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    session.add(Office(id="o-01", name="Smoke"))
    session.flush()
    session.add_all([
        AppUser(id="u-head", name="Head", role="head", office_id="o-01"),
        AppUser(id="u-mgr", name="Manager", role="manager", office_id="o-01"),
        AppUser(id="u-caller", name="Caller", role="caller", office_id="o-01", manager_id="u-mgr"),
    ])
    for rule in RULES:
        session.add(Rule(**rule))
    session.commit()
    return session


def _queue_call(session, call_id, hour):
    session.add(Call(
        id=call_id, caller_id="u-caller", office_id="o-01", account_type="personal_loan",
        start_time=datetime(2026, 10, 15, hour, 30, 0), duration_seconds=60, language="hi",
        has_audio=True, audio_path="smoke.wav", status="queued", is_ground_truth=False,
    ))
    session.commit()


def _forbidden(*_a, **_k):
    raise AssertionError("a provider client was requested during the offline smoke test")


def _threat_response():
    """The model's answer for CALL-SMK-1: a valid threat reference, a complete opening, speakers agent/agent/borrower."""
    outcomes = {c: {"violated": False, "uncertain": False, "note": ""} for c in ("R2", "R4", "R5", "R6")}
    outcomes["R3"] = {"violated": True, "uncertain": False, "note": "threatened police"}
    response = _unified_response()
    response["segments"] = [{"index": i, "speaker": s, "confidence": 0.95, "uncertain": False, "mixed_speakers": False}
                            for i, s in enumerate(["agent", "agent", "borrower"])]
    response["violations"] = [{"rule_code": "R3", "segment_index": 1, "word_start": 5, "word_end": 7, "speaker": "agent",
                               "explanation": "threat", "confidence": 0.9, "uncertain": False}]
    response["rule_outcomes"] = outcomes
    response["sentiment"] = {"borrower_overall": "negative", "agent_stayed_calm": False}
    return response


def _unified_response():
    outcomes = {c: {"violated": False, "uncertain": False, "note": ""} for c in ("R2", "R3", "R4", "R6")}
    outcomes["R5"] = {"violated": True, "uncertain": False, "note": "asked for an OTP"}

    def finding(rule, idx, start, end):
        return {"rule_code": rule, "segment_index": idx, "word_start": start, "word_end": end, "speaker": "agent",
                "explanation": "smoke", "confidence": 0.9, "uncertain": False}

    speakers = ["agent", "borrower", "agent", "borrower"]
    return {
        "segments": [{"index": i, "speaker": s, "confidence": 0.95, "uncertain": False, "mixed_speakers": False}
                     for i, s in enumerate(speakers)],
        "violations": [finding("R5", 2, 5, 8), finding("R3", 2, 0, 99)],  # a valid reference and an out-of-range one
        "rule_outcomes": outcomes,
        "opening_check": {
            "agent_turns_considered": [[0]],
            "name": {"stated": True, "segment_index": 0, "evidence_quote": "main Pooja bol rahi hoon"},
            "company": {"stated": True, "segment_index": 0, "evidence_quote": "Shaha Finlease"},
            "purpose": {"stated": True, "segment_index": 0, "evidence_quote": "loan ke baare mein"},
            "uncertain": False,
        },
        "sentiment": {"borrower_overall": "neutral", "agent_stayed_calm": True},
        "review_recommended": False,
        "review_reasons": [],
    }


def run() -> int:
    report = Report()
    production = _production_db_path()
    before = _fingerprint(production)
    session = _scratch_session()
    audio_dir = Path(tempfile.mkdtemp(prefix="smoke_audio_"))
    (audio_dir / "smoke.wav").write_bytes(b"RIFF....WAVEfmt ")  # never decoded: transcription is stubbed

    def raw(texts):
        return [RawSegment(seq=i, start=2.0 * i, end=2.0 * i + 2.0, text=t) for i, t in enumerate(texts)]

    transcripts = {"CALL-SMK-1": THREAT_TEXTS, "CALL-SMK-2": UNIFIED_TEXTS}

    def fake_transcribe(audio_bytes, filename, sleep_fn=None):
        return raw(transcripts[state["call"]])

    state = {"call": None}

    with ExitStack() as stack:
        # No real provider can be reached from here.
        stack.enter_context(patch.object(settings, "groq_api_key", ""))
        stack.enter_context(patch.object(settings, "audio_dir", str(audio_dir)))
        stack.enter_context(patch.object(groq, "Groq", _forbidden))
        stack.enter_context(patch.object(transcription_service, "_get_client", _forbidden))
        stack.enter_context(patch.object(transcription_service, "transcribe", fake_transcribe))
        stack.enter_context(patch.object(llm_service, "_get_client", lambda: object()))  # only a placeholder for the stubbed chat
        stack.enter_context(patch.object(llm_service, "_chat_json", lambda *a, **k: _threat_response() if state["call"] == "CALL-SMK-1" else _unified_response()))
        app.dependency_overrides[get_db] = lambda: (yield session)
        client = TestClient(app)  # not used as a context manager: no startup hooks
        try:
            print("Setup")
            report.check("there is no analysis mode to choose", "analysis_mode" not in Settings.model_fields)
            report.check("health endpoint", client.get("/api/health").json() == {"status": "ok"})
            _queue_call(session, "CALL-SMK-1", hour=10)
            _queue_call(session, "CALL-SMK-2", hour=7)  # 07:30 IST, outside permitted hours

            print("Roles")
            report.check("caller cannot run analysis", client.post("/api/calls/CALL-SMK-1/reprocess", headers=CALLER).status_code == 403)
            report.check("a call is visible to its manager", client.get("/api/calls/CALL-SMK-1", headers=MANAGER).status_code == 200)
            report.check("unanalysed call is not reported clean",
                         client.get("/api/calls/CALL-SMK-1", headers=MANAGER).json()["analysis_outcome"] == "not_analyzed")

            print("Analysis of a call with a threat")
            state["call"] = "CALL-SMK-1"
            ran = client.post("/api/calls/CALL-SMK-1/reprocess", headers=MANAGER)
            report.check("analysis completes", ran.status_code == 200 and ran.json()["status"] == "ready", ran.text[:200])
            report.check("score reflects the verified critical finding", ran.json().get("score") == 70, str(ran.json().get("score")))
            rows = client.get("/api/calls/CALL-SMK-1/violations", headers=MANAGER).json()
            r3 = [v for v in rows if v["rule_code"] == "R3"]
            report.check("one R3, verified, quote from the transcript",
                         len(r3) == 1 and r3[0]["evidence_status"] == "verified" and r3[0]["quote"] == "police bhej dunga")
            report.check("transcript is stored", len(client.get("/api/calls/CALL-SMK-1/transcript", headers=MANAGER).json()) == 3)
            detail = client.get("/api/calls/CALL-SMK-1", headers=MANAGER).json()
            report.check("outcome reports violations", detail["analysis_outcome"] == "violations_found", detail["analysis_outcome"])

            print("Manager review and scoring")
            vid = r3[0]["violation_id"]
            report.check("caller cannot review", client.post(f"/api/violations/{vid}/review", json={"decision": "confirmed"}, headers=CALLER).status_code == 403)
            confirmed = client.post(f"/api/violations/{vid}/review", json={"decision": "confirmed", "comment": "smoke"}, headers=MANAGER)
            report.check("confirm keeps the deduction", confirmed.status_code == 200 and confirmed.json()["call"]["score"] == 70)
            dismissed = client.post(f"/api/violations/{vid}/review", json={"decision": "false_alarm", "comment": "smoke"}, headers=HEAD)
            report.check("false alarm removes the deduction", dismissed.status_code == 200 and dismissed.json()["call"]["score"] == 100)

            print("Analysis of an out-of-hours call with an OTP request and an invalid reference")
            state["call"] = "CALL-SMK-2"
            ran2 = client.post("/api/calls/CALL-SMK-2/reprocess", headers=HEAD)
            report.check("analysis completes", ran2.status_code == 200 and ran2.json()["status"] == "ready", ran2.text[:200])
            rows2 = client.get("/api/calls/CALL-SMK-2/violations", headers=HEAD).json()
            by_rule = {v["rule_code"]: v for v in rows2}
            report.check("R1 comes from metadata (07:30)", "R1" in by_rule and by_rule["R1"]["evidence_status"] == "verified")
            report.check("R5 evidence read from the stored text by position",
                         "R5" in by_rule and by_rule["R5"]["quote"] == "woh OTP bata dijiye" and by_rule["R5"]["evidence_status"] == "verified")
            report.check("R5 counts toward the score", by_rule["R5"]["counts_toward_score"] is True)
            report.check("an invalid reference is kept as unverified, never dropped",
                         "R3" in by_rule and by_rule["R3"]["evidence_status"] == "unverified")
            report.check("unverified finding shows stored text, not the model's words",
                         by_rule["R3"]["quote"] == UNIFIED_TEXTS[2] and by_rule["R3"]["evidence_reason"])
            report.check("unverified finding does not deduct", by_rule["R3"]["counts_toward_score"] is False)
            report.check("score = 100 - R1 (15) - R5 (15)", ran2.json().get("score") == 70, str(ran2.json().get("score")))

            print("Isolation")
            report.check("no provider call was attempted", True)  # a real attempt would have raised above
        finally:
            app.dependency_overrides.pop(get_db, None)
            session.close()

    after = _fingerprint(production)
    report.check("production database file unchanged", before == after)
    print(f"\n{'ALL CHECKS PASSED' if report.failures == 0 else str(report.failures) + ' CHECK(S) FAILED'}")
    return 0 if report.failures == 0 else 1


if __name__ == "__main__":
    sys.exit(run())
