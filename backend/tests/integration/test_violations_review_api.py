from tests.conftest import HEAD_HEADERS, MGR1_HEADERS, MGR2_HEADERS, CALLER1_HEADERS, CALLER3_HEADERS

# v-1 (rule R3, critical, pending) is the only violation on CALL-C3-01 in
# the fixture DB (see tests/conftest.py) — so its correctly *computed*
# score is 100 - 30 = 70, which is what the review endpoint recomputes to
# regardless of whatever value the fixture's Call row happens to carry.


def test_confirm_does_not_change_score(client):
    resp = client.post(
        "/api/violations/v-1/review",
        headers=MGR2_HEADERS,
        json={"decision": "confirmed", "comment": "Yes, this happened."},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["review"]["decision"] == "confirmed"
    assert body["review"]["reviewer"] == "Vikram Singh"
    assert body["call"]["score"] == 70  # unchanged — confirming never changes score


def test_false_alarm_removes_deduction_and_raises_score(client):
    resp = client.post(
        "/api/violations/v-1/review",
        headers=MGR2_HEADERS,
        json={"decision": "false_alarm", "comment": "Not actually a threat."},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["review"]["decision"] == "false_alarm"
    assert body["call"]["score"] == 100  # the only deduction is removed


def test_caller_forbidden_even_for_own_call(client):
    resp = client.post(
        "/api/violations/v-1/review",
        headers=CALLER3_HEADERS,  # caller3 owns CALL-C3-01 itself
        json={"decision": "confirmed"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "REVIEW_FORBIDDEN"


def test_manager_outside_team_gets_404_not_403(client):
    # mgr1's team doesn't include CALL-C3-01's caller.
    resp = client.post(
        "/api/violations/v-1/review",
        headers=MGR1_HEADERS,
        json={"decision": "confirmed"},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "VIOLATION_NOT_FOUND"


def test_head_can_review(client):
    resp = client.post(
        "/api/violations/v-1/review",
        headers=HEAD_HEADERS,
        json={"decision": "confirmed"},
    )
    assert resp.status_code == 200


def test_unknown_violation_404(client):
    resp = client.post(
        "/api/violations/v-does-not-exist/review",
        headers=HEAD_HEADERS,
        json={"decision": "confirmed"},
    )
    assert resp.status_code == 404


def test_invalid_decision_rejected(client):
    resp = client.post(
        "/api/violations/v-1/review",
        headers=MGR2_HEADERS,
        json={"decision": "maybe"},
    )
    assert resp.status_code == 422
