from tests.conftest import (
    HEAD_HEADERS,
    MGR1_HEADERS,
    MGR2_HEADERS,
    CALLER1_HEADERS,
    CALLER3_HEADERS,
)


def test_missing_role_headers_rejected(client):
    resp = client.get("/api/calls")
    assert resp.status_code == 401


def test_head_sees_all_calls(client):
    resp = client.get("/api/calls", headers=HEAD_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert {item["call_id"] for item in body["items"]} == {
        "CALL-C1-01",
        "CALL-C2-01",
        "CALL-C3-01",
    }


def test_manager_sees_only_team_calls(client):
    resp = client.get("/api/calls", headers=MGR1_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert {item["call_id"] for item in body["items"]} == {"CALL-C1-01", "CALL-C2-01"}


def test_other_manager_sees_disjoint_set(client):
    resp = client.get("/api/calls", headers=MGR2_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["call_id"] == "CALL-C3-01"


def test_caller_sees_only_own_call(client):
    resp = client.get("/api/calls", headers=CALLER1_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["call_id"] == "CALL-C1-01"


def test_caller_cannot_access_another_callers_call(client):
    resp = client.get("/api/calls/CALL-C2-01", headers=CALLER1_HEADERS)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "CALL_NOT_FOUND"


def test_caller_can_access_own_call_detail(client):
    resp = client.get("/api/calls/CALL-C1-01", headers=CALLER1_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["call_id"] == "CALL-C1-01"


def test_manager_cannot_access_other_teams_call(client):
    resp = client.get("/api/calls/CALL-C3-01", headers=MGR1_HEADERS)
    assert resp.status_code == 404


def test_manager_can_access_own_teams_call(client):
    resp = client.get("/api/calls/CALL-C2-01", headers=MGR1_HEADERS)
    assert resp.status_code == 200


def test_nonexistent_call_is_404_for_every_role(client):
    for headers in (HEAD_HEADERS, MGR1_HEADERS, CALLER1_HEADERS):
        resp = client.get("/api/calls/CALL-DOES-NOT-EXIST", headers=headers)
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "CALL_NOT_FOUND"


def test_transcript_visibility_inherits_from_call(client):
    # CALL-C3-01 belongs to caller3 / mgr2's team; caller1 may not see it.
    resp = client.get("/api/calls/CALL-C3-01/transcript", headers=CALLER1_HEADERS)
    assert resp.status_code == 404

    resp = client.get("/api/calls/CALL-C3-01/transcript", headers=CALLER3_HEADERS)
    assert resp.status_code == 200
    segments = resp.json()
    assert len(segments) == 1
    assert segments[0]["speaker"] == "agent"


def test_violations_visibility_and_shape(client):
    resp = client.get("/api/calls/CALL-C3-01/violations", headers=MGR2_HEADERS)
    assert resp.status_code == 200
    violations = resp.json()
    assert len(violations) == 1
    v = violations[0]
    assert v["rule_code"] == "R3"
    assert v["severity"] == "critical"
    assert v["review"]["decision"] == "pending"

    # Not visible to the other manager's identity.
    resp = client.get("/api/calls/CALL-C3-01/violations", headers=MGR1_HEADERS)
    assert resp.status_code == 404


def _add_segment_and_violation(db, *, suffix, speaker_confidence, needs_speaker_review, seq, speaker="agent", speaker_uncertain=False):
    from app.models import Review, TranscriptSegment, Violation

    seg_id = f"seg-{suffix}"
    db.add(
        TranscriptSegment(
            id=seg_id,
            call_id="CALL-C3-01",
            seq=seq,
            speaker=speaker,
            start_time=seq * 5,
            end_time=seq * 5 + 5,
            text=f"segment {suffix}",
            speaker_confidence=speaker_confidence,
            speaker_uncertain=speaker_uncertain,
        )
    )
    db.flush()
    vid = f"v-{suffix}"
    db.add(
        Violation(
            id=vid,
            call_id="CALL-C3-01",
            rule_code="R3",
            segment_id=seg_id,
            severity="critical",
            start_time_seconds=float(seq * 5),
            quote=f"segment {suffix}",
            explanation="x",
            confidence=0.9,
            needs_speaker_review=needs_speaker_review,
        )
    )
    db.add(Review(id=f"r-{suffix}", violation_id=vid, decision="pending"))
    db.commit()


def test_transcript_exposes_speaker_confidence_numeric_and_null(client, db_session):
    # A historical row without a confidence must remain null in the API.
    _add_segment_and_violation(
        db_session, suffix="missing", speaker_confidence=None, needs_speaker_review=True, seq=5
    )
    _add_segment_and_violation(
        db_session, suffix="hi", speaker_confidence=0.85, needs_speaker_review=False, seq=2
    )

    resp = client.get("/api/calls/CALL-C3-01/transcript", headers=MGR2_HEADERS)
    assert resp.status_code == 200
    by_id = {s["id"]: s for s in resp.json()}

    assert by_id["seg-missing"]["speaker_confidence"] is None
    assert by_id["seg-hi"]["speaker_confidence"] == 0.85
    # Existing fields are preserved.
    assert by_id["seg-1"]["speaker"] == "agent"
    assert by_id["seg-1"]["text"] == "hum aapke ghar bhi aayenge"


def test_transcript_exposes_new_speaker_states_and_uncertainty(client, db_session):
    _add_segment_and_violation(
        db_session, suffix="mixed", speaker_confidence=0.5, speaker="mixed",
        speaker_uncertain=True, needs_speaker_review=True, seq=3,
    )
    resp = client.get("/api/calls/CALL-C3-01/transcript", headers=MGR2_HEADERS)
    assert resp.status_code == 200
    segment = next(s for s in resp.json() if s["id"] == "seg-mixed")
    assert segment["speaker"] == "mixed"
    assert segment["ai_speaker"] == "mixed"
    assert segment["speaker_uncertain"] is True


def test_violations_expose_needs_speaker_review_true_and_false(client, db_session):
    _add_segment_and_violation(
        db_session, suffix="unsure", speaker_confidence=0.3, needs_speaker_review=True, seq=2
    )

    resp = client.get("/api/calls/CALL-C3-01/violations", headers=MGR2_HEADERS)
    assert resp.status_code == 200
    by_id = {v["violation_id"]: v for v in resp.json()}

    assert by_id["v-1"]["needs_speaker_review"] is False  # fixture default
    assert by_id["v-unsure"]["needs_speaker_review"] is True
    # Existing fields are preserved alongside the new one.
    assert by_id["v-unsure"]["review"]["decision"] == "pending"
    assert by_id["v-unsure"]["rule_code"] == "R3"


def test_new_fields_respect_role_visibility(client, db_session):
    _add_segment_and_violation(
        db_session, suffix="vis", speaker_confidence=0.4, needs_speaker_review=True, seq=2
    )
    # The other manager must still get 404, not the new fields.
    assert client.get("/api/calls/CALL-C3-01/transcript", headers=MGR1_HEADERS).status_code == 404
    assert client.get("/api/calls/CALL-C3-01/violations", headers=MGR1_HEADERS).status_code == 404


def test_review_flag_survives_manager_confirmation(client, db_session):
    # Confirming does not clear the flag (it records *why* it was held back);
    # it only changes the review decision, which makes the violation score.
    _add_segment_and_violation(
        db_session, suffix="conf", speaker_confidence=0.2, needs_speaker_review=True, seq=2
    )
    resp = client.post(
        "/api/violations/v-conf/review",
        json={"decision": "confirmed"},
        headers=MGR2_HEADERS,
    )
    assert resp.status_code == 200

    resp = client.get("/api/calls/CALL-C3-01/violations", headers=MGR2_HEADERS)
    by_id = {v["violation_id"]: v for v in resp.json()}
    assert by_id["v-conf"]["needs_speaker_review"] is True
    assert by_id["v-conf"]["review"]["decision"] == "confirmed"


def _add_fragment(db, suffix, seq, rule="R3", speaker="agent", needs_speaker_review=False):
    """A segment (contiguous, 5s each) plus one pending violation citing it."""
    from app.models import Review, TranscriptSegment, Violation

    seg_id = f"seg-{suffix}"
    db.add(
        TranscriptSegment(
            id=seg_id,
            call_id="CALL-C3-01",
            seq=seq,
            speaker=speaker,
            start_time=seq * 5,
            end_time=seq * 5 + 5,
            text=f"fragment {suffix}",
            speaker_confidence=0.95,
        )
    )
    db.flush()
    vid = f"v-{suffix}"
    db.add(
        Violation(
            id=vid,
            call_id="CALL-C3-01",
            rule_code=rule,
            segment_id=seg_id,
            severity="critical",
            start_time_seconds=float(seq * 5),
            quote=f"fragment {suffix}",
            explanation="x",
            confidence=0.9,
            needs_speaker_review=needs_speaker_review,
        )
    )
    db.add(Review(id=f"r-{suffix}", violation_id=vid, decision="pending"))
    db.commit()


def _violations_by_id(client, headers=MGR2_HEADERS):
    resp = client.get("/api/calls/CALL-C3-01/violations", headers=headers)
    assert resp.status_code == 200
    return {v["violation_id"]: v for v in resp.json()}


def test_fragments_of_one_incident_share_a_group_and_count_once(client, db_session):
    from app.services import scoring_service

    _add_fragment(db_session, "f1", 10)
    _add_fragment(db_session, "f2", 11)  # directly after f1, same speaker

    by_id = _violations_by_id(client)

    assert by_id["v-1"]["counts_toward_score"] is True
    assert by_id["v-1"]["incident_group_id"] is None
    assert by_id["v-f1"]["incident_group_id"] == by_id["v-f2"]["incident_group_id"]
    assert by_id["v-f1"]["incident_group_id"] is not None
    counted = {vid for vid, v in by_id.items() if v["counts_toward_score"]}
    assert counted == {"v-1", "v-f1"}  # f2 is a fragment of f1's incident

    # The API flags and the score come from the same logic.
    expected = max(0, 100 - 30 * len(counted))
    assert scoring_service.compute_score("CALL-C3-01", db_session) == expected == 40


def test_separate_incidents_each_count(client, db_session):
    _add_fragment(db_session, "a", 10)
    _add_fragment(db_session, "b", 12)  # seq 11 between them

    by_id = _violations_by_id(client)
    assert all(v["counts_toward_score"] for v in by_id.values())
    assert all(v["incident_group_id"] is None for v in by_id.values())


def test_r3_and_r4_on_the_same_segment_are_both_counted_and_flagged(client, db_session):
    from app.models import Review, Violation

    db_session.add(
        Violation(
            id="v-r4",
            call_id="CALL-C3-01",
            rule_code="R4",
            segment_id="seg-1",  # the fixture's R3 violation also cites seg-1
            severity="critical",
            start_time_seconds=1.0,
            quote="hum aapke ghar bhi aayenge",
            explanation="x",
            confidence=0.8,
        )
    )
    db_session.add(Review(id="r-r4", violation_id="v-r4", decision="pending"))
    db_session.commit()

    by_id = _violations_by_id(client)
    assert by_id["v-1"]["counts_toward_score"] and by_id["v-r4"]["counts_toward_score"]
    assert by_id["v-1"]["overlapping_rule_codes"] == ["R4"]
    assert by_id["v-r4"]["overlapping_rule_codes"] == ["R3"]


def test_fragment_held_for_speaker_review_is_reported_not_counted(client, db_session):
    _add_fragment(db_session, "u1", 10, needs_speaker_review=True)

    by_id = _violations_by_id(client)
    assert by_id["v-u1"]["needs_speaker_review"] is True
    assert by_id["v-u1"]["counts_toward_score"] is False


def test_review_endpoint_recomputes_using_incident_grouping(client, db_session):
    _add_fragment(db_session, "f1", 10)
    _add_fragment(db_session, "f2", 11)

    # Dismissing the row that carries the deduction moves it to the other
    # fragment: the incident is still real, so the score does not change.
    resp = client.post(
        "/api/violations/v-f1/review",
        json={"decision": "false_alarm", "comment": "not the agent"},
        headers=MGR2_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["call"]["score"] == 40
    by_id = _violations_by_id(client)
    assert by_id["v-f1"]["counts_toward_score"] is False
    assert by_id["v-f2"]["counts_toward_score"] is True
    assert by_id["v-f1"]["incident_group_id"] == by_id["v-f2"]["incident_group_id"]

    # Confirming does not change anything.
    resp = client.post(
        "/api/violations/v-f2/review", json={"decision": "confirmed"}, headers=MGR2_HEADERS
    )
    assert resp.json()["call"]["score"] == 40

    # Once every fragment is dismissed the incident stops counting.
    resp = client.post(
        "/api/violations/v-f2/review",
        json={"decision": "false_alarm", "comment": "dismissed"},
        headers=MGR2_HEADERS,
    )
    assert resp.json()["call"]["score"] == 70
    assert _violations_by_id(client)["v-f2"]["counts_toward_score"] is False


def test_pagination_envelope_shape(client):
    resp = client.get("/api/calls?page=1&page_size=2", headers=HEAD_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"items", "total", "page", "page_size"}
    assert body["total"] == 3
    assert len(body["items"]) == 2
    assert body["page"] == 1
    assert body["page_size"] == 2


def test_office_filter(client):
    resp = client.get("/api/calls?office_id=o-02", headers=HEAD_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["call_id"] == "CALL-C3-01"


def test_office_filter_combines_with_role_scope(client):
    # mgr1's team is entirely in office o-01; filtering by o-02 should
    # yield nothing, not bypass the role scope.
    resp = client.get("/api/calls?office_id=o-02", headers=MGR1_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


def test_unknown_user_id_rejected(client):
    resp = client.get("/api/calls", headers={"X-Role": "head", "X-User-Id": "u-ghost"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "UNKNOWN_USER"


def test_role_mismatch_rejected(client):
    # u-caller1 is actually a caller, not a manager.
    resp = client.get("/api/calls", headers={"X-Role": "manager", "X-User-Id": "u-caller1"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "ROLE_MISMATCH"


def test_meta_offices_and_users(client):
    resp = client.get("/api/meta/offices")
    assert resp.status_code == 401

    resp = client.get("/api/meta/users?role=caller")
    assert resp.status_code == 401
