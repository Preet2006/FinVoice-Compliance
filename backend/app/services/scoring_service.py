"""
Deterministic scoring, per MASTER_IMPLEMENTATION_PLAN §7.4. The LLM never
sets severity or score — only `rules.py`'s SEVERITY_WEIGHTS does.

Formula (unchanged): 100 minus the severity weight of every *counted*
violation, clamped to [0, 100]. What this module decides is which stored
violation rows are counted. Evidence rows are never deleted or altered.

A violation row is eligible to count unless:
  - its review decision is 'false_alarm', or
  - it is flagged `needs_speaker_review` and still 'pending' (uncertain
    speaker attribution; a manager confirming it makes it eligible), or
  - its evidence is 'unverified' and it is still 'pending' (the proposed
    quote failed validation; it only counts once a manager has checked the
    audio and explicitly confirmed it - see the review endpoint).

Eligible rows are then collapsed into "incidents", so one utterance that
Whisper split across segments, or that the LLM reported twice, deducts once:

  1. Exact duplicates: same call, same rule, same transcript segment.
  2. Adjacent fragments: same call and rule, on consecutive segments (seq
     differs by exactly 1), same speaker, a gap of at most
     MAX_INCIDENT_GAP_SECONDS between one segment's end and the next one's
     start, and at most MAX_INCIDENT_SEGMENTS segments per incident (longer
     runs are split greedily left to right).

Grouping is computed from ALL rows of a call, independent of review state,
so an incident's identity does not change when a manager reviews a row; only
which row carries the deduction does. An incident deducts once if at least
one of its rows is eligible (the heaviest eligible row carries it).

Deliberately never merged: findings under different rule codes, R1, rows
without a transcript segment, unverified-evidence rows (they are never
folded into a verified incident; a confirmed one that merely repeats a
verified, counting finding of the same rule and segment is not counted
twice), and any row whose segment lacks a usable
sequence number, speaker or timestamps (those can still be merged as exact
duplicates, which needs only the segment id).

MAX_INCIDENT_GAP_SECONDS and MAX_INCIDENT_SEGMENTS are starting assumptions,
chosen to be conservative. They have NOT been validated against measured
Whisper inter-segment gaps (no timestamped transcripts were available).
"""

from dataclasses import dataclass
from typing import Iterable, Optional

from sqlalchemy.orm import Session, joinedload

from app.models import Violation
from app.rules import get_severity_weight

MAX_INCIDENT_GAP_SECONDS = 1.0
MAX_INCIDENT_SEGMENTS = 3

# Rule pairs whose findings on the same segment are surfaced to the manager
# as possibly describing one act. They are scored independently regardless:
# the finalized policy is that when both R3 and R4 genuinely apply, both count.
OVERLAP_REVIEW_PAIRS = (frozenset({"R3", "R4"}),)


@dataclass(frozen=True)
class ViolationScoreStatus:
    counts_toward_score: bool
    # Shared by every row of an incident with 2+ rows; None for a row that
    # is the only member of its incident.
    incident_group_id: Optional[str] = None
    # Other rules flagged on the same segment that the manager should check
    # (R3 <-> R4). Informational only: never changes what counts.
    overlapping_rule_codes: tuple = ()


@dataclass(frozen=True)
class _SegmentInfo:
    seq: int
    speaker: str
    start: float
    end: float


def _decision(v) -> str:
    return v.review.decision if v.review else "pending"


def _is_unverified(v) -> bool:
    return getattr(v, "evidence_status", "verified") == "unverified"


def _speaker_needs_review(v) -> bool:
    """Defensive score gate derived from the cited segment as well as the
    copied violation flag. This keeps unknown/mixed/third-party attribution
    conservative even for rows created outside the analysis pipeline."""
    if getattr(v, "needs_speaker_review", False):
        return True
    seg = getattr(v, "segment", None)
    if seg is None:
        return False
    speaker = getattr(seg, "effective_speaker", getattr(seg, "speaker", None))
    confidence = getattr(seg, "speaker_confidence", None)
    try:
        low_confidence = confidence is None or float(confidence) < 0.6
    except (TypeError, ValueError):
        low_confidence = True
    return (
        speaker in {"third_party", "unknown", "mixed"}
        or bool(getattr(seg, "speaker_uncertain", False))
        or low_confidence
    )


def _is_eligible(v) -> bool:
    decision = _decision(v)
    if decision == "false_alarm":
        return False
    if decision == "pending" and (_speaker_needs_review(v) or _is_unverified(v)):
        return False
    return True


def _segment_info(v) -> Optional[_SegmentInfo]:
    """The cited segment's seq/speaker/timestamps, or None if any of them is
    missing or malformed (in which case adjacent-fragment merging is unsafe)."""
    if v.segment_id is None:
        return None
    seg = v.segment
    if seg is None:
        return None
    seq, speaker = seg.seq, getattr(seg, "effective_speaker", seg.speaker)
    if isinstance(seq, bool) or not isinstance(seq, int):
        return None
    if not isinstance(speaker, str) or not speaker:
        return None
    if seg.start_time is None or seg.end_time is None:
        return None
    try:
        start, end = float(seg.start_time), float(seg.end_time)
    except (TypeError, ValueError):
        return None
    if end < start:
        return None
    return _SegmentInfo(seq=seq, speaker=speaker, start=start, end=end)


def _continues(prev: _SegmentInfo, nxt: _SegmentInfo) -> bool:
    if nxt.seq != prev.seq + 1:
        return False
    if nxt.speaker != prev.speaker:
        return False
    if nxt.start < prev.start:
        return False
    return round(nxt.start - prev.end, 2) <= MAX_INCIDENT_GAP_SECONDS


@dataclass
class _Node:
    """All rows of one rule that cite the same segment (exact duplicates)."""

    call_id: str
    rule_code: str
    segment_id: str
    info: Optional[_SegmentInfo]
    rows: list


def _build_incidents(violations: list) -> list:
    """Returns a list of incidents; each is (group_id_or_None, rows)."""
    incidents = []
    nodes: dict = {}

    for v in sorted(violations, key=lambda x: x.id):
        if v.segment_id is None or _is_unverified(v):
            incidents.append((None, [v]))
            continue
        key = (v.call_id, v.rule_code, v.segment_id)
        if key not in nodes:
            nodes[key] = _Node(v.call_id, v.rule_code, v.segment_id, _segment_info(v), [])
        nodes[key].rows.append(v)

    mergeable: dict = {}
    for node in nodes.values():
        if node.info is None:
            incidents.append(_finish([node]))
        else:
            mergeable.setdefault((node.call_id, node.rule_code), []).append(node)

    for group_nodes in mergeable.values():
        group_nodes.sort(key=lambda n: (n.info.seq, n.segment_id))
        chain = [group_nodes[0]]
        for node in group_nodes[1:]:
            if len(chain) < MAX_INCIDENT_SEGMENTS and _continues(chain[-1].info, node.info):
                chain.append(node)
            else:
                incidents.append(_finish(chain))
                chain = [node]
        incidents.append(_finish(chain))

    return incidents


def _finish(chain: list):
    rows = [row for node in chain for row in node.rows]
    group_id = f"inc-{chain[0].rule_code}-{chain[0].segment_id}" if len(rows) > 1 else None
    return (group_id, rows)


def _overlapping_rule_codes(v, by_segment: dict) -> tuple:
    if v.segment_id is None or _decision(v) == "false_alarm" or _is_unverified(v):
        return ()
    codes = set()
    for other in by_segment.get(v.segment_id, ()):
        if other.id == v.id or other.rule_code == v.rule_code:
            continue
        if _decision(other) == "false_alarm" or _is_unverified(other):
            continue
        if frozenset({v.rule_code, other.rule_code}) in OVERLAP_REVIEW_PAIRS:
            codes.add(other.rule_code)
    return tuple(sorted(codes))


def evaluate_violations(violations: Iterable) -> dict:
    """
    Maps violation id -> ViolationScoreStatus for one call's violations.
    This is the single place that decides what counts: compute_score sums
    the weights of the rows marked counts_toward_score, and the API reports
    the same flags, so the displayed score and the per-row status cannot
    disagree.
    """
    rows = list(violations)
    statuses: dict = {}

    by_segment: dict = {}
    for v in rows:
        if v.segment_id is not None:
            by_segment.setdefault(v.segment_id, []).append(v)

    for group_id, members in _build_incidents(rows):
        eligible = [m for m in members if _is_eligible(m)]
        carrier = None
        if eligible:
            carrier = min(
                eligible,
                key=lambda m: (-get_severity_weight(m.severity), _seq_of(m), m.id),
            )
        for m in members:
            statuses[m.id] = ViolationScoreStatus(
                counts_toward_score=m is carrier,
                incident_group_id=group_id,
                overlapping_rule_codes=_overlapping_rule_codes(m, by_segment),
            )

    # A confirmed unverified finding that repeats the evidence of a verified,
    # counting finding (same rule, same segment) is the same incident: it is
    # kept and shown, but must not deduct a second time.
    counted_evidence = {
        (m.rule_code, m.segment_id)
        for m in rows
        if m.segment_id is not None and not _is_unverified(m) and statuses[m.id].counts_toward_score
    }
    for m in rows:
        if _is_unverified(m) and statuses[m.id].counts_toward_score and (m.rule_code, m.segment_id) in counted_evidence:
            statuses[m.id] = ViolationScoreStatus(counts_toward_score=False)

    return statuses


def _seq_of(v) -> int:
    info = _segment_info(v)
    return info.seq if info is not None else 0


def compute_score(call_id: str, db: Session) -> int:
    violations = (
        db.query(Violation)
        .options(joinedload(Violation.segment), joinedload(Violation.review))
        .filter(Violation.call_id == call_id)
        .all()
    )
    statuses = evaluate_violations(violations)

    base_score = 100
    for v in violations:
        if statuses[v.id].counts_toward_score:
            base_score -= get_severity_weight(v.severity)

    return max(0, min(100, base_score))
