"""
Evaluation results on the dashboard and in the call list: per-rule precision/recall/F1 against the approved
script-derived labels (clearly labelled as such), the pending/failed counts, the call's risk level and the rule text
on a finding. Every number is checked against a hand-worked expectation.

Fixture (conftest): CALL-C3-01 is the only analysed call; CALL-C1-01 and CALL-C2-01 have metadata only.
"""

import json

import pytest

from app.models import Call, Review, Violation
from app.services import evaluation_metrics
from tests.conftest import HEAD_HEADERS, MGR1_HEADERS, MGR2_HEADERS
from tests.integration.test_dashboard_api import add_finding, dash, make_ready


@pytest.fixture
def labels(tmp_path, monkeypatch):
    def write(per_call):
        path = tmp_path / "gt.json"
        path.write_text(json.dumps({"calls": {cid: {"violations": v} for cid, v in per_call.items()}}), encoding="utf-8")
        monkeypatch.setattr(evaluation_metrics, "GROUND_TRUTH_PATH", path)

    return write


def as_evaluation(db, *call_ids):
    for cid in call_ids:
        db.get(Call, cid).batch_id = evaluation_metrics.BATCH_ID
    db.commit()


def row(d, rule):
    return next(r for r in d["evaluation"]["per_rule"] if r["rule_code"] == rule)


def test_without_evaluation_calls_the_metrics_are_unavailable_with_a_reason(client):
    e = dash(client, HEAD_HEADERS)["evaluation"]
    assert e["available"] is False and e["per_rule"] == [] and "evaluation batch" in e["reason"]


def test_metrics_are_computed_against_the_labels_and_labelled_script_derived(client, db_session, labels):
    as_evaluation(db_session, "CALL-C3-01")
    labels({"CALL-C3-01": {"R1": False, "R2": False, "R3": True, "R4": True, "R5": False, "R6": False}})
    add_finding(db_session, "v-ok", "CALL-C3-01", "R3", None)  # expected and found -> TP
    add_finding(db_session, "v-fp", "CALL-C3-01", "R5", None)  # found, not expected -> FP
    d = dash(client, HEAD_HEADERS)
    e = d["evaluation"]
    assert e["available"] is True and e["calls_scored"] == 1
    assert e["label"] == "Script-derived evaluation metrics, not audio-verified real-world accuracy"
    assert (row(d, "R3")["tp"], row(d, "R3")["precision"], row(d, "R3")["recall"], row(d, "R3")["f1"]) == (1, 1.0, 1.0, 1.0)
    assert (row(d, "R4")["fn"], row(d, "R4")["recall"], row(d, "R4")["precision"]) == (1, 0.0, None)  # missed; nothing predicted
    assert (row(d, "R5")["fp"], row(d, "R5")["precision"], row(d, "R5")["recall"]) == (1, 0.0, None)  # no positive label
    assert (row(d, "R2")["tn"], row(d, "R2")["precision"], row(d, "R2")["f1"]) == (1, None, None)


def test_the_ai_prediction_does_not_change_when_a_reviewer_dismisses_the_finding(client, db_session, labels):
    as_evaluation(db_session, "CALL-C3-01")
    labels({"CALL-C3-01": {"R4": False}})
    add_finding(db_session, "v-x", "CALL-C3-01", "R4", None, decision="false_alarm")
    d = dash(client, HEAD_HEADERS)
    assert row(d, "R4")["fp"] == 1  # the AI said it; the review outcome is reported separately
    assert d["review_outcomes"]["false_alarm"] == 1 and d["verified_violations"] == 1  # only the fixture's R3 is a breach


def test_a_manager_added_finding_is_not_an_ai_prediction(client, db_session, labels):
    as_evaluation(db_session, "CALL-C3-01")
    labels({"CALL-C3-01": {"R5": True}})
    add_finding(db_session, "v-m", "CALL-C3-01", "R5", None)
    db_session.get(Violation, "v-m").source = "manual"
    db_session.commit()
    assert row(dash(client, HEAD_HEADERS), "R5")["fn"] == 1


def test_calls_that_are_not_analysed_or_not_in_the_batch_are_not_scored(client, db_session, labels):
    as_evaluation(db_session, "CALL-C1-01")  # metadata only: not analysed
    labels({"CALL-C1-01": {"R3": True}, "CALL-C3-01": {"R3": True}})  # CALL-C3-01 is analysed but not an evaluation call
    assert dash(client, HEAD_HEADERS)["evaluation"]["available"] is False


def test_an_unreadable_label_file_is_reported_not_guessed(client, db_session, monkeypatch, tmp_path):
    as_evaluation(db_session, "CALL-C3-01")
    monkeypatch.setattr(evaluation_metrics, "GROUND_TRUTH_PATH", tmp_path / "missing.json")
    e = dash(client, HEAD_HEADERS)["evaluation"]
    assert e["available"] is False and "could not be read" in e["reason"]


def test_metrics_respect_the_requesters_scope(client, db_session, labels):
    as_evaluation(db_session, "CALL-C3-01")
    labels({"CALL-C3-01": {"R3": True}})
    add_finding(db_session, "v-s", "CALL-C3-01", "R3", None)
    assert dash(client, MGR2_HEADERS)["evaluation"]["available"] is True  # their own team's call
    assert dash(client, MGR1_HEADERS)["evaluation"]["available"] is False  # another team's call is not theirs


def test_pending_and_failed_calls_are_counted_separately(client, db_session):
    db_session.get(Call, "CALL-C1-01").status = "queued"
    db_session.get(Call, "CALL-C1-01").has_audio = True
    db_session.get(Call, "CALL-C2-01").status = "failed"
    db_session.commit()
    d = dash(client, HEAD_HEADERS)
    assert (d["calls_pending"], d["calls_failed"], d["calls_analyzed"], d["total_calls"]) == (1, 1, 1, 3)


def test_the_call_list_shows_the_highest_deducting_severity(client, db_session):
    add_finding(db_session, "v-hi", "CALL-C3-01", "R3", None, severity="critical")
    add_finding(db_session, "v-lo", "CALL-C3-01", "R2", None, severity="medium")
    items = client.get("/api/calls", headers=HEAD_HEADERS).json()["items"]
    by_id = {i["call_id"]: i for i in items}
    assert by_id["CALL-C3-01"]["top_severity"] == "critical"
    assert by_id["CALL-C1-01"]["top_severity"] is None


def test_a_dismissed_finding_does_not_set_the_risk_level(client, db_session):
    db_session.get(Review, "r-1").decision = "false_alarm"  # the fixture's own critical R3 finding
    db_session.commit()
    items = {i["call_id"]: i for i in client.get("/api/calls", headers=HEAD_HEADERS).json()["items"]}
    assert items["CALL-C3-01"]["top_severity"] is None


def test_a_finding_carries_the_rule_description(client, db_session):
    add_finding(db_session, "v-r", "CALL-C3-01", "R3", None)
    rows = client.get("/api/calls/CALL-C3-01/violations", headers=HEAD_HEADERS).json()
    assert rows[0]["rule_name"] and rows[0]["rule_description"]
