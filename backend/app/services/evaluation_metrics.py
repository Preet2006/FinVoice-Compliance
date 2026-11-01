"""
Per-rule precision / recall / F1 of the AI findings against the approved SCRIPT-DERIVED evaluation labels
(data/ground_truth_evaluation_v1.json). Computed on read; only for calls of the evaluation batch.

What this is NOT: the labels say what each role-play script intends. Nobody has verified them against the audio, so
these are script-derived evaluation metrics, not audio-verified real-world accuracy.

Rules of the calculation (so the numbers cannot flatter the model):
  * Only ANALYSED calls (status 'ready') of the evaluation batch that the requester may see are scored.
  * Prediction = the AI produced at least one finding of the rule on the call. Held, unverified and later-dismissed
    findings still count as predictions: a manager's review changes the outcome, not what the AI said. Findings a
    reviewer added by hand (source 'manual') are not predictions.
  * R1 is decided by the backend from the call's start time, so its row measures the rule logic, not the model.
  * A rule with no positive labels (or no positive predictions) has no recall (or precision): None, never 0 or 1.
"""

import json
from collections import defaultdict
from pathlib import Path

from app.config import BACKEND_DIR
from app.models import Violation

BATCH_ID = "evaluation-v1"
GROUND_TRUTH_PATH = Path(BACKEND_DIR) / "data" / "ground_truth_evaluation_v1.json"
RULES = ("R1", "R2", "R3", "R4", "R5", "R6")
LABEL = "Script-derived evaluation metrics, not audio-verified real-world accuracy"


def _ratio(num, den):
    return round(num / den, 3) if den else None


def _load_labels():
    try:
        data = json.loads(GROUND_TRUTH_PATH.read_text(encoding="utf-8"))
        return {cid: c["violations"] for cid, c in data["calls"].items()}
    except (OSError, ValueError, KeyError):
        return None


def build(db, analysed_calls) -> dict:
    """`analysed_calls`: the already scoped, status-'ready' calls. Returns a dict for the dashboard."""
    unavailable = lambda reason: {"available": False, "label": LABEL, "reason": reason}  # noqa: E731
    evaluation_calls = [c for c in analysed_calls if c.batch_id == BATCH_ID]
    if not evaluation_calls:
        return unavailable("This database holds no analysed calls of the evaluation batch.")
    labels = _load_labels()
    if labels is None:
        return unavailable("The approved evaluation label file could not be read.")
    scored = [c for c in evaluation_calls if c.id in labels]
    if not scored:
        return unavailable("None of the analysed evaluation calls has an approved label.")

    predicted = defaultdict(set)
    for call_id, rule in (
        db.query(Violation.call_id, Violation.rule_code)
        .filter(Violation.call_id.in_([c.id for c in scored]), Violation.source != "manual")
        .all()
    ):
        predicted[call_id].add(rule)

    per_rule = []
    for rule in RULES:
        tp = fp = fn = tn = 0
        for c in scored:
            expected, got = bool(labels[c.id].get(rule)), rule in predicted[c.id]
            tp += expected and got
            fp += got and not expected
            fn += expected and not got
            tn += (not expected) and (not got)
        precision, recall = _ratio(tp, tp + fp), _ratio(tp, tp + fn)
        f1 = round(2 * precision * recall / (precision + recall), 3) if precision and recall else (0.0 if precision is not None and recall is not None else None)
        per_rule.append({"rule_code": rule, "tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": precision, "recall": recall, "f1": f1})
    return {
        "available": True,
        "label": LABEL,
        "reason": None,
        "calls_scored": len(scored),
        "per_rule": per_rule,
        "notes": [
            "Labels are derived from the role-play scripts and are not audio-verified.",
            "Scenario times are simulated role-play times, not recording times.",
            "Held, unverified and dismissed AI findings still count as predictions; manager-added findings do not.",
        ],
    }
