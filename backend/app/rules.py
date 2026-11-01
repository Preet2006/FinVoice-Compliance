"""
Compliance rules R1-R6 constants and R1 deterministic check.
Exact definitions from MASTER_IMPLEMENTATION_PLAN §7.1 and assignment §3.
"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Optional

# Rule definitions (will be inserted into database during seeding)
RULES = [
    {
        "code": "R1",
        "name": "Calls only between 8 AM and 7 PM",
        "description": "Call must start within permitted hours (08:00-19:00 IST). Metadata-based check.",
        "default_severity": "high",
    },
    {
        "code": "R2",
        "name": "No abusive or foul language",
        "description": "No slurs, swearing, or abusive language in any language. LLM-based detection.",
        "default_severity": "critical",
    },
    {
        "code": "R3",
        "name": "No threats or intimidation",
        "description": "No mentions of police, jail, arrest, legal action, or physical threats like 'we will come to your house'. LLM-based detection.",
        "default_severity": "critical",
    },
    {
        "code": "R4",
        "name": "No shaming or disclosure to third parties",
        "description": "Never discuss debt details with family members, employers, or anyone other than the borrower. LLM-based detection.",
        "default_severity": "critical",
    },
    {
        "code": "R5",
        "name": "Never ask for OTP, PIN, CVV or passwords",
        "description": "Agent must never request OTPs, PINs, CVVs, passwords, or other authentication credentials. LLM-based detection.",
        "default_severity": "high",
    },
    {
        "code": "R6",
        "name": "Mandatory opening (name, company, purpose)",
        "description": "Agent must introduce themselves with name, company (Shaha Finlease), and purpose of the call in the opening. LLM-based detection, scoped to first 1-2 agent turns.",
        "default_severity": "medium",
    },
]

# Severity weights for scoring (from MASTER_IMPLEMENTATION_PLAN §7.4)
SEVERITY_WEIGHTS = {
    "critical": 30,
    "high": 15,
    "medium": 7,
    "low": 3,
}

# R1 time boundaries.
#
# Interpretation of "calls only between 8 AM and 7 PM": a call is permitted when
# its start time is in [08:00:00, 19:00:00) local time. Exactly 08:00:00 is
# permitted; 19:00:00 and later is a violation, as is anything before 08:00:00.
# All times are Indian Standard Time. Timestamps without a UTC offset (the
# metadata CSV and the database store none) are taken to already be IST; a
# timestamp that carries an offset is converted to IST first.
R1_START_TIME = time(8, 0, 0)  # 08:00:00 (inclusive)
R1_END_TIME = time(19, 0, 0)  # 19:00:00 (exclusive)
IST = timezone(timedelta(hours=5, minutes=30), "IST")

R1_OK = "ok"
R1_VIOLATION = "violation"
R1_UNRESOLVED = "unresolved"


@dataclass
class R1Evaluation:
    """Outcome of the calling-hours check. `unresolved` means the start time
    is missing or not a usable date-time: neither a pass nor a violation can
    be claimed, so callers must not treat it as either."""

    status: str  # ok | violation | unresolved
    reason: str
    local_time: Optional[time] = None
    violation: Optional[dict] = None


def evaluate_r1(call_start_time) -> R1Evaluation:
    """Deterministic R1 check from the call's start time (metadata only; no
    recording, transcript or model is involved)."""
    if call_start_time is None:
        return R1Evaluation(R1_UNRESOLVED, "The call has no start time.")
    if not isinstance(call_start_time, datetime):
        return R1Evaluation(R1_UNRESOLVED, "The call start time is not a date-time value.")

    local = call_start_time.astimezone(IST) if call_start_time.tzinfo is not None else call_start_time
    call_time = local.time()

    if R1_START_TIME <= call_time < R1_END_TIME:
        return R1Evaluation(R1_OK, "Start time is within 08:00-19:00 IST.", call_time)

    return R1Evaluation(
        R1_VIOLATION,
        "Start time is outside 08:00-19:00 IST.",
        call_time,
        {
            "rule_code": "R1",
            "severity": "high",
            "start_time_seconds": 0.0,  # R1 has no transcript segment
            "quote": f"Call started at {local.strftime('%H:%M:%S')} IST (outside permitted hours 08:00-19:00)",
            "explanation": "Call initiated outside permitted calling hours (8 AM - 7 PM)",
            "confidence": 1.0,  # deterministic check
            "segment_id": None,  # R1 is metadata-based, no transcript segment
        },
    )


def check_r1_violation(call_start_time: datetime) -> Optional[dict]:
    """The R1 violation dict, or None when the call is within hours or its
    start time cannot be evaluated (use evaluate_r1 to tell those apart)."""
    return evaluate_r1(call_start_time).violation


def get_rule_by_code(code: str) -> Optional[dict]:
    """Get rule definition by code."""
    for rule in RULES:
        if rule["code"] == code:
            return rule
    return None


def get_severity_weight(severity: str) -> int:
    """Get scoring weight for a severity level."""
    return SEVERITY_WEIGHTS.get(severity, 0)
