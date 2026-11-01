"""
ISOLATED EXPERIMENT ONLY. Not part of the application, not imported by it,
and does not write to any database. Reads the already-cached Sarvam batch
STT output (from evaluation/sarvam_benchmark.py's earlier run) and feeds it
through the REAL, UNMODIFIED Groq compliance step
(app.services.llm_service.analyse_compliance) to see whether Sarvam's
voice-based diarization, combined with our existing compliance LLM, fixes
the CALL-0002 speaker-misattribution problem without introducing new ones.

Makes NO new Sarvam API calls (reuses cached JSON). Makes new Groq
gpt-oss-120b calls only for: (1) one speaker-role classification per call,
(2) the compliance analysis itself (reusing the real production function
unchanged). Both are the same model already used by the real pipeline; no
new model is introduced.

Everything here is read from app.rules / app.services.llm_service
READ-ONLY — nothing in those files is modified by this script.
"""

import csv
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.rules import RULES, check_r1_violation, get_rule_by_code, get_severity_weight  # noqa: E402
from app.services import llm_service  # noqa: E402

SCRATCH_DIR = Path(
    r"C:\Users\preet\AppData\Local\Temp\claude\d--ShahFineLease-Assignment"
    r"\85df7430-0bed-4245-9435-7d62029b9656\scratchpad\sarvam_benchmark"
)
CACHED_SARVAM_DIR = SCRATCH_DIR / "output" / "downloaded"
EXPERIMENT_OUTPUT_DIR = SCRATCH_DIR / "hybrid_experiment"

# --- Disclosed, experimental-only thresholds (not from any spec) ---------
LOW_CONFIDENCE_THRESHOLD = 0.7  # LLM role-classification confidence below this -> "low confidence"
COLLAPSE_SHARE_THRESHOLD = 0.15  # either speaker's share of segments/duration below this -> "structurally collapsed"

COMPANY_NAME_PATTERN = re.compile(
    r"finl\w*|shah\s*famil", re.IGNORECASE
)

CALL_METADATA = {
    # call_id, start_time (from backend/data/metadata.csv), account_type -- read-only reference values
    "call_01": {"call_id": "CALL-0001", "start_time": "2026-10-01T10:30:00"},
    "call_02": {"call_id": "CALL-0002", "start_time": "2026-10-01T15:15:00"},
    "call_03": {"call_id": "CALL-0003", "start_time": "2026-10-02T11:00:00"},
    "call_04": {"call_id": "CALL-0004", "start_time": "2026-10-02T14:45:00"},
    "call_05": {"call_id": "CALL-0005", "start_time": "2026-10-03T09:00:00"},
}


@dataclass
class SpeakerStats:
    speaker_id: str
    segment_count: int
    total_duration: float
    full_text: str


@dataclass
class CallExperimentResult:
    filename: str
    call_id: str
    r1_violation: dict | None
    heuristic_agent_id: str | None  # None = inconclusive
    llm_agent_id: str | None
    llm_confidence: float | None
    llm_reasoning: str | None
    speaker_stats: dict  # speaker_id -> SpeakerStats
    structurally_collapsed: bool
    ambiguous: bool
    ambiguity_reasons: list = field(default_factory=list)
    hypotheses: list = field(default_factory=list)  # list of {agent_id, violations, score, sentiment}
    api_calls_made: int = 0
    errors: list = field(default_factory=list)


def load_cached_sarvam(filename: str) -> list[dict]:
    path = CACHED_SARVAM_DIR / f"{filename}.wav.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data["diarized_transcript"]["entries"]
    return sorted(entries, key=lambda e: e["start_time_seconds"])


def compute_speaker_stats(entries: list[dict]) -> dict[str, SpeakerStats]:
    stats: dict[str, SpeakerStats] = {}
    for e in entries:
        sid = e["speaker_id"]
        dur = e["end_time_seconds"] - e["start_time_seconds"]
        if sid not in stats:
            stats[sid] = SpeakerStats(speaker_id=sid, segment_count=0, total_duration=0.0, full_text="")
        stats[sid].segment_count += 1
        stats[sid].total_duration += dur
        stats[sid].full_text += " " + e["transcript"]
    return stats


def heuristic_agent_id(stats: dict[str, SpeakerStats]) -> str | None:
    """
    Content-grounded heuristic, NOT 'whoever speaks first': the speaker_id
    whose cumulative text mentions a company-name-shaped token is the
    agent, since only the agent is expected to self-identify the company
    (this is literally what R6 checks). If neither (or both, ambiguously)
    mention it, this heuristic abstains rather than guessing.
    """
    matches = [sid for sid, s in stats.items() if COMPANY_NAME_PATTERN.search(s.full_text)]
    if len(matches) == 1:
        return matches[0]
    return None  # zero or multiple matches: inconclusive by design


def llm_classify_agent(entries: list[dict], sleep_fn=time.sleep) -> tuple[str | None, float | None, str | None]:
    """
    One new gpt-oss-120b call per call (reuses llm_service's own retry/
    JSON-mode machinery unchanged). Asks which raw speaker_id is the
    collection agent, given the real diarized transcript.
    """
    numbered = "\n".join(
        f"[speaker {e['speaker_id']}] ({e['start_time_seconds']:.2f}-{e['end_time_seconds']:.2f}s) {e['transcript']}"
        for e in entries
    )
    system_prompt = (
        "This is a real diarized transcript of a debt-collection phone call. "
        "Each line is tagged with a real speaker_id (0 or 1) from voice-based "
        "diarization -- the ids are already correct per-speaker, you only need "
        "to decide which speaker_id is the collection AGENT (introduces "
        "themselves, mentions a company, asks about a loan/payment) versus the "
        "BORROWER (the person being called about a debt). Respond with ONLY a "
        'JSON object: {"agent_speaker_id": "0"|"1", "confidence": <0-1>, '
        '"reasoning": "<one sentence>"}.'
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": numbered},
    ]
    client = llm_service._get_client()
    data = llm_service._chat_json(client, messages, sleep_fn=sleep_fn)
    agent_id = data.get("agent_speaker_id")
    confidence = data.get("confidence")
    reasoning = data.get("reasoning")
    if agent_id not in ("0", "1"):
        return None, None, f"LLM returned invalid agent_speaker_id: {agent_id!r}"
    if not isinstance(confidence, (int, float)):
        confidence = None
    return agent_id, confidence, reasoning


def build_labelled_segments(entries: list[dict], agent_id: str) -> list:
    out = []
    for seq, e in enumerate(entries):
        speaker = "agent" if e["speaker_id"] == agent_id else "borrower"
        out.append(
            llm_service.LabelledSegment(
                seq=seq,
                speaker=speaker,
                start=e["start_time_seconds"],
                end=e["end_time_seconds"],
                text=e["transcript"],
            )
        )
    return out


def run_compliance(labelled_segments: list, sleep_fn=time.sleep):
    return llm_service.analyse_compliance(labelled_segments, sleep_fn=sleep_fn)


def transcript_only_score(violations: list) -> int:
    """
    Sum of severity weights for the given violations only -- R1 is NOT
    included (it has no bearing on a transcript-only experiment) and no
    Review/false_alarm concept exists here. This is NOT the application's
    production Call.score; see the report's explicit disclaimer.
    """
    base = 100
    for v in violations:
        rule = get_rule_by_code(v.rule_code)
        base -= get_severity_weight(rule["default_severity"]) if rule else 0
    return max(0, min(100, base))


def run_one_call(filename: str, sleep_fn=time.sleep) -> CallExperimentResult:
    meta = CALL_METADATA[filename]
    entries = load_cached_sarvam(filename)
    stats = compute_speaker_stats(entries)
    api_calls = 0
    errors = []

    # R1: deterministic, metadata-only, computed separately -- not part of
    # this transcript experiment's score.
    r1 = check_r1_violation(datetime.fromisoformat(meta["start_time"]))

    total_segments = sum(s.segment_count for s in stats.values())
    total_duration = sum(s.total_duration for s in stats.values())
    collapsed = False
    for s in stats.values():
        seg_share = s.segment_count / total_segments if total_segments else 0
        dur_share = s.total_duration / total_duration if total_duration else 0
        if seg_share < COLLAPSE_SHARE_THRESHOLD or dur_share < COLLAPSE_SHARE_THRESHOLD:
            collapsed = True

    h_agent = heuristic_agent_id(stats)

    try:
        llm_agent, llm_conf, llm_reason = llm_classify_agent(entries, sleep_fn=sleep_fn)
        api_calls += 1
    except (llm_service.LLMQuotaError, llm_service.LLMResponseValidationError) as exc:
        llm_agent, llm_conf, llm_reason = None, None, None
        errors.append(f"speaker classification failed: {exc}")

    ambiguity_reasons = []
    if h_agent is None:
        ambiguity_reasons.append("heuristic inconclusive (no/ambiguous company-name mention)")
    if llm_agent is None:
        ambiguity_reasons.append("LLM classification failed or invalid")
    elif llm_conf is not None and llm_conf < LOW_CONFIDENCE_THRESHOLD:
        ambiguity_reasons.append(f"LLM confidence {llm_conf:.2f} below threshold {LOW_CONFIDENCE_THRESHOLD}")
    if h_agent is not None and llm_agent is not None and h_agent != llm_agent:
        ambiguity_reasons.append(f"heuristic ({h_agent}) and LLM ({llm_agent}) disagree")
    if collapsed:
        ambiguity_reasons.append(
            f"diarization appears structurally collapsed (one speaker's share < {COLLAPSE_SHARE_THRESHOLD:.0%})"
        )

    ambiguous = len(ambiguity_reasons) > 0

    # Candidate agent-id hypotheses to actually run compliance against.
    if ambiguous:
        candidate_ids = ["0", "1"]
    else:
        candidate_ids = [llm_agent if llm_agent is not None else h_agent]

    hypotheses = []
    for agent_id in candidate_ids:
        labelled = build_labelled_segments(entries, agent_id)
        try:
            result = run_compliance(labelled, sleep_fn=sleep_fn)
            api_calls += 1
            score = transcript_only_score(result.violations)
            hypotheses.append(
                {
                    "agent_speaker_id": agent_id,
                    "hypothetical": ambiguous,
                    "violations": [
                        {
                            "rule_code": v.rule_code,
                            "quote": v.quote,
                            "explanation": v.explanation,
                            "confidence": v.confidence,
                        }
                        for v in result.violations
                    ],
                    "borrower_sentiment": result.borrower_sentiment,
                    "agent_stayed_calm": result.agent_stayed_calm,
                    "transcript_only_score": score,
                }
            )
        except (llm_service.LLMQuotaError, llm_service.LLMResponseValidationError) as exc:
            errors.append(f"compliance analysis failed for agent_id={agent_id}: {exc}")

    return CallExperimentResult(
        filename=filename,
        call_id=meta["call_id"],
        r1_violation=r1,
        heuristic_agent_id=h_agent,
        llm_agent_id=llm_agent,
        llm_confidence=llm_conf,
        llm_reasoning=llm_reason,
        speaker_stats={
            sid: {"segment_count": s.segment_count, "total_duration": round(s.total_duration, 2)}
            for sid, s in stats.items()
        },
        structurally_collapsed=collapsed,
        ambiguous=ambiguous,
        ambiguity_reasons=ambiguity_reasons,
        hypotheses=hypotheses,
        api_calls_made=api_calls,
        errors=errors,
    )


def main():
    EXPERIMENT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    filenames = ["call_01", "call_02", "call_03", "call_04", "call_05"]
    all_results = {}
    total_api_calls = 0

    for i, filename in enumerate(filenames):
        print(f"=== {filename} ({CALL_METADATA[filename]['call_id']}) [{i+1}/{len(filenames)}] ===")
        result = run_one_call(filename)
        total_api_calls += result.api_calls_made
        all_results[filename] = result
        print(
            f"  heuristic={result.heuristic_agent_id} llm={result.llm_agent_id} "
            f"conf={result.llm_confidence} collapsed={result.structurally_collapsed} "
            f"ambiguous={result.ambiguous} api_calls={result.api_calls_made}"
        )
        if result.errors:
            print(f"  ERRORS: {result.errors}")
        if i < len(filenames) - 1:
            time.sleep(8)

    print(f"\nTotal new Groq API calls made: {total_api_calls}")

    # Dump full results for inspection.
    dump = {
        fn: {
            "call_id": r.call_id,
            "r1_violation": r.r1_violation,
            "heuristic_agent_id": r.heuristic_agent_id,
            "llm_agent_id": r.llm_agent_id,
            "llm_confidence": r.llm_confidence,
            "llm_reasoning": r.llm_reasoning,
            "speaker_stats": r.speaker_stats,
            "structurally_collapsed": r.structurally_collapsed,
            "ambiguous": r.ambiguous,
            "ambiguity_reasons": r.ambiguity_reasons,
            "hypotheses": r.hypotheses,
            "api_calls_made": r.api_calls_made,
            "errors": r.errors,
        }
        for fn, r in all_results.items()
    }
    out_path = EXPERIMENT_OUTPUT_DIR / "hybrid_results.json"
    out_path.write_text(json.dumps(dump, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Full results saved to {out_path}")


if __name__ == "__main__":
    main()
