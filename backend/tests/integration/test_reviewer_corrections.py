"""
Reviewer speaker corrections, manually added findings, the audit trail, and the
protection of that work from reprocessing.

Fixture call CALL-C3-01 (caller3 / manager mgr2): seg-1 agent "hum aapke ghar bhi
aayenge" with one pending AI finding v-1 (R3, critical). Score 100 - 30 = 70.
"""

import json

import pytest

from app.models import Call, Review, ReviewerAudit, TranscriptSegment, Violation
from app.services import pipeline, reviewer_service, scoring_service, transcription_service
from tests.conftest import CALLER3_HEADERS, HEAD_HEADERS, MGR1_HEADERS, MGR2_HEADERS

CALL = "CALL-C3-01"


@pytest.fixture()
def call(db_session):
    """The fixture call plus a borrower segment and two adjacent agent segments."""
    db_session.add_all([
        TranscriptSegment(id="seg-2", call_id=CALL, seq=2, speaker="borrower", start_time=5, end_time=7, text="nahi main nahi bataunga", speaker_confidence=0.95),
        TranscriptSegment(id="seg-3", call_id=CALL, seq=3, speaker="agent", start_time=20, end_time=23, text="aap bewakoof ho bilkul", speaker_confidence=0.95),
        TranscriptSegment(id="seg-4", call_id=CALL, seq=4, speaker="agent", start_time=23.5, end_time=26, text="aap nikamme bhi ho", speaker_confidence=0.95),
    ])
    c = db_session.get(Call, CALL)
    c.score = scoring_service.compute_score(CALL, db_session)  # the fixture row carries an arbitrary 40
    db_session.commit()
    return c


def speaker(client, seg, who, headers=MGR2_HEADERS, comment=None):
    return client.post(f"/api/calls/{CALL}/segments/{seg}/speaker", headers=headers, json={"speaker": who, "comment": comment})


def finding(client, seg="seg-3", rule="R2", start=1, end=1, headers=MGR2_HEADERS, comment="Insult aimed at the borrower.", **extra):
    return client.post(f"/api/calls/{CALL}/violations", headers=headers,
                       json={"rule_code": rule, "segment_id": seg, "word_start": start, "word_end": end, "comment": comment, **extra})


def score(client):
    return client.get(f"/api/calls/{CALL}", headers=MGR2_HEADERS).json()["score"]


# ------------------------------------------------------------------ speaker correction
def test_manager_corrects_a_speaker_and_the_ai_label_is_kept(client, db_session, call):
    r = speaker(client, "seg-2", "agent", comment="Heard the agent speaking here")
    assert r.status_code == 200
    seg = r.json()["segment"]
    assert (seg["speaker"], seg["ai_speaker"], seg["speaker_corrected"]) == ("agent", "borrower", True)
    assert seg["corrected_by"] == "Vikram Singh" and seg["correction_comment"] == "Heard the agent speaking here" and seg["corrected_at"]
    row = db_session.get(TranscriptSegment, "seg-2")
    assert row.speaker == "borrower" and row.speaker_override == "agent"  # the AI prediction is never overwritten


def test_head_can_correct_a_speaker(client, call):
    assert speaker(client, "seg-2", "agent", headers=HEAD_HEADERS).status_code == 200


def test_caller_cannot_correct_a_speaker_even_on_their_own_call(client, db_session, call):
    r = speaker(client, "seg-2", "agent", headers=CALLER3_HEADERS)
    assert r.status_code == 403 and r.json()["error"]["code"] == "REVIEW_FORBIDDEN"
    assert db_session.get(TranscriptSegment, "seg-2").speaker_override is None


def test_manager_outside_the_team_gets_404(client, call):
    assert speaker(client, "seg-2", "agent", headers=MGR1_HEADERS).status_code == 404


@pytest.mark.parametrize("seg,who,code,status", [
    ("seg-nope", "agent", "SEGMENT_NOT_FOUND", 404),
    ("seg-2", "not-a-speaker", "INVALID_SPEAKER", 422),
    ("seg-2", "", "INVALID_SPEAKER", 422),
])
def test_invalid_correction_requests_are_rejected_and_change_nothing(client, db_session, call, seg, who, code, status):
    r = speaker(client, seg, who)
    assert r.status_code == status and r.json()["error"]["code"] == code
    assert db_session.get(TranscriptSegment, "seg-2").speaker_override is None
    assert db_session.query(ReviewerAudit).count() == 0


def test_a_segment_of_another_call_is_rejected(client, db_session, call):
    r = client.post("/api/calls/CALL-C1-01/segments/seg-2/speaker", headers=MGR1_HEADERS, json={"speaker": "agent"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "SEGMENT_NOT_FOUND"


def test_full_correction_history_is_preserved_and_readable(client, db_session, call):
    speaker(client, "seg-2", "agent", comment="first")
    speaker(client, "seg-2", "borrower", comment="changed my mind")
    rows = db_session.query(ReviewerAudit).order_by(ReviewerAudit.id).all()
    assert [r.action for r in rows] == ["speaker_correction"] * 2
    first, second = (json.loads(r.detail) for r in rows)
    assert (first["ai_speaker"], first["previous_effective"], first["new_speaker"]) == ("borrower", "borrower", "agent")
    assert (second["previous_effective"], second["new_speaker"], second["comment"]) == ("agent", "borrower", "changed my mind")
    history = client.get(f"/api/calls/{CALL}/audit", headers=MGR2_HEADERS).json()
    assert [h["detail"]["new_speaker"] for h in history] == ["agent", "borrower"] and history[0]["actor"] == "Vikram Singh"
    assert client.get(f"/api/calls/{CALL}/audit", headers=CALLER3_HEADERS).status_code == 403


def test_transcript_shows_effective_speaker_beside_the_ai_label(client, call):
    speaker(client, "seg-2", "agent")
    seg = {s["id"]: s for s in client.get(f"/api/calls/{CALL}/transcript", headers=MGR2_HEADERS).json()}
    assert (seg["seg-2"]["speaker"], seg["seg-2"]["ai_speaker"], seg["seg-2"]["speaker_corrected"]) == ("agent", "borrower", True)
    assert (seg["seg-1"]["speaker"], seg["seg-1"]["ai_speaker"], seg["seg-1"]["speaker_corrected"]) == ("agent", "agent", False)


def test_moving_a_segment_away_from_the_agent_holds_its_undecided_finding(client, db_session, call):
    assert score(client) == 70
    r = speaker(client, "seg-1", "borrower")
    assert r.status_code == 200 and r.json()["call"]["score"] == 100  # R3 needs the agent: held, not deducted
    v = db_session.get(Violation, "v-1")
    assert v.needs_speaker_review is True and v.review.decision == "pending"  # kept, not deleted or decided


def test_a_decided_finding_is_not_changed_by_a_speaker_correction(client, db_session, call):
    client.post("/api/violations/v-1/review", headers=MGR2_HEADERS, json={"decision": "confirmed", "comment": "ok"})
    speaker(client, "seg-1", "borrower")
    v = db_session.get(Violation, "v-1")
    assert v.needs_speaker_review is False and v.review.decision == "confirmed"


# ------------------------------------------------------------------ manual findings
def test_manager_adds_a_missed_finding_grounded_in_the_stored_transcript(client, db_session, call):
    r = finding(client)
    assert r.status_code == 201
    v = r.json()["violation"]
    assert (v["rule_code"], v["severity"], v["source"], v["created_by"]) == ("R2", "critical", "manual", "Vikram Singh")
    assert v["quote"] == "bewakoof" and v["evidence_status"] == "verified" and v["counts_toward_score"] is True
    assert v["review"]["decision"] == "confirmed" and v["review"]["comment"] == "Insult aimed at the borrower."
    assert r.json()["call"]["score"] == 40 == score(client)  # 100 - R3 (30) - R2 (30)
    ids = {x["violation_id"]: x for x in client.get(f"/api/calls/{CALL}/violations", headers=MGR2_HEADERS).json()}
    assert ids["v-1"]["source"] == "ai" and ids[v["violation_id"]]["source"] == "manual"  # AI finding preserved and distinguishable


def test_the_quote_is_the_exact_stored_text_over_the_selected_words(client, db_session, call):
    v = finding(client, start=0, end=2).json()["violation"]
    assert v["quote"] == "aap bewakoof ho"
    assert db_session.get(Violation, v["violation_id"]).quote in db_session.get(TranscriptSegment, "seg-3").text


def test_head_can_add_a_finding(client, call):
    assert finding(client, headers=HEAD_HEADERS).status_code == 201


def test_caller_cannot_add_a_finding(client, db_session, call):
    r = finding(client, headers=CALLER3_HEADERS)
    assert r.status_code == 403 and db_session.query(Violation).filter_by(source="manual").count() == 0


def test_manager_outside_the_team_cannot_add_a_finding(client, call):
    assert finding(client, headers=MGR1_HEADERS).status_code == 404


@pytest.mark.parametrize("kwargs,code", [
    (dict(start=0, end=99), "INVALID_EVIDENCE"),
    (dict(start=3, end=1), "INVALID_EVIDENCE"),
    (dict(start=-1, end=1), "INVALID_EVIDENCE"),
    (dict(rule="R1"), "R1_NOT_MANUAL"),
    (dict(rule="R9"), "INVALID_RULE"),
    (dict(comment="   "), "COMMENT_REQUIRED"),
    (dict(seg="seg-2"), "SPEAKER_NOT_AGENT"),
    (dict(seg="seg-missing"), "SEGMENT_NOT_FOUND"),
    (dict(quote="completely different words"), "EVIDENCE_MISMATCH"),
])
def test_invalid_findings_are_rejected_and_nothing_is_stored(client, db_session, call, kwargs, code):
    r = finding(client, **kwargs)
    assert r.status_code in (404, 422) and r.json()["error"]["code"] == code
    assert db_session.query(Violation).filter_by(source="manual").count() == 0
    assert db_session.query(ReviewerAudit).count() == 0 and score(client) == 70


@pytest.mark.parametrize("start,end", [("1", 1), (1.5, 2), (True, 2), (None, 1)])
def test_word_positions_must_be_whole_numbers(client, call, start, end):
    r = client.post(f"/api/calls/{CALL}/violations", headers=MGR2_HEADERS,
                    json={"rule_code": "R2", "segment_id": "seg-3", "word_start": start, "word_end": end, "comment": "x"})
    assert r.status_code == 422


def test_a_segment_from_another_call_is_rejected(client, db_session, call):
    r = client.post("/api/calls/CALL-C1-01/violations", headers=MGR1_HEADERS,
                    json={"rule_code": "R2", "segment_id": "seg-3", "word_start": 0, "word_end": 0, "comment": "x"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "SEGMENT_NOT_FOUND"


def test_a_corrected_borrower_segment_can_then_carry_a_finding(client, call):
    assert finding(client, seg="seg-2", rule="R3", start=0, end=1).status_code == 422
    speaker(client, "seg-2", "agent")
    assert finding(client, seg="seg-2", rule="R3", start=0, end=1).status_code == 201


def test_the_same_rule_cannot_be_flagged_twice_on_one_segment(client, db_session, call):
    r = finding(client, seg="seg-1", rule="R3", start=0, end=1)  # v-1 already flags R3 here
    assert r.status_code == 409 and r.json()["error"]["code"] == "DUPLICATE_FINDING" and "v-1" in r.json()["error"]["message"]
    assert finding(client).status_code == 201
    assert finding(client, start=0, end=0).status_code == 409


def test_a_finding_dismissed_as_false_alarm_can_be_added_again(client, call):
    first = finding(client).json()["violation"]["violation_id"]
    client.post(f"/api/violations/{first}/review", headers=MGR2_HEADERS, json={"decision": "false_alarm", "comment": "wrong"})
    assert finding(client).status_code == 201


def test_adjacent_manual_findings_of_one_incident_deduct_once(client, call):
    a = finding(client, seg="seg-3").json()["violation"]["violation_id"]
    b = finding(client, seg="seg-4", start=1, end=1).json()["violation"]["violation_id"]
    listed = {v["violation_id"]: v for v in client.get(f"/api/calls/{CALL}/violations", headers=MGR2_HEADERS).json()}
    assert listed[a]["counts_toward_score"] is True and listed[b]["counts_toward_score"] is False  # same incident
    assert listed[a]["incident_group_id"] == listed[b]["incident_group_id"] is not None
    assert score(client) == 40  # R3 (30) + ONE R2 (30), not two


def test_scoring_uses_the_corrected_speaker_when_grouping_incidents(client, db_session, call):
    for vid, seg in (("v-g3", "seg-3"), ("v-g4", "seg-4")):  # two AI R2 findings on adjacent agent segments
        db_session.add(Violation(id=vid, call_id=CALL, rule_code="R2", segment_id=seg, severity="critical", start_time_seconds=20,
                                 quote="q", explanation="e", evidence_status="verified"))
        db_session.add(Review(id=f"r-{vid}", violation_id=vid, decision="confirmed", reviewer_id="u-mgr2"))
    db_session.commit()
    scoring_service.compute_score(CALL, db_session)
    assert client.post("/api/violations/v-g3/review", headers=MGR2_HEADERS, json={"decision": "confirmed"}).json()["call"]["score"] == 40  # one incident
    r = speaker(client, "seg-4", "borrower")  # the reviewer decides the second line was not the agent's
    assert r.json()["call"]["score"] == 10  # the speakers now differ: two incidents, each deducting
    listed = client.get(f"/api/calls/{CALL}/violations", headers=MGR2_HEADERS).json()
    assert [v["counts_toward_score"] for v in listed if v["rule_code"] == "R2"] == [True, True]


def test_a_segment_with_a_manual_finding_cannot_be_moved_away_from_the_agent(client, db_session, call):
    vid = finding(client).json()["violation"]["violation_id"]
    r = speaker(client, "seg-3", "borrower")
    assert r.status_code == 409 and r.json()["error"]["code"] == "SEGMENT_HAS_MANUAL_FINDING"
    assert db_session.get(TranscriptSegment, "seg-3").speaker_override is None and score(client) == 40
    client.post(f"/api/violations/{vid}/review", headers=MGR2_HEADERS, json={"decision": "false_alarm"})
    assert speaker(client, "seg-3", "borrower").status_code == 200  # fine once the finding is dismissed


def test_false_alarm_on_a_manual_finding_removes_its_deduction(client, call):
    vid = finding(client).json()["violation"]["violation_id"]
    assert score(client) == 40
    r = client.post(f"/api/violations/{vid}/review", headers=MGR2_HEADERS, json={"decision": "false_alarm", "comment": "mistake"})
    assert r.status_code == 200 and r.json()["call"]["score"] == 70
    assert score(client) == 70 and client.get(f"/api/calls/{CALL}/violations", headers=MGR2_HEADERS).json()  # still listed


def test_the_score_is_explained_by_the_findings(client, call):
    finding(client)
    body = client.get(f"/api/calls/{CALL}/violations", headers=MGR2_HEADERS).json()
    weights = {"critical": 30, "high": 15, "medium": 7, "low": 3}
    assert 100 - sum(weights[v["severity"]] for v in body if v["counts_toward_score"]) == score(client)


def test_a_manual_finding_is_audited_with_its_evidence(client, db_session, call):
    vid = finding(client, start=0, end=1).json()["violation"]["violation_id"]
    [row] = db_session.query(ReviewerAudit).all()
    detail = json.loads(row.detail)
    assert (row.action, row.target_id, row.actor_id) == ("manual_finding", vid, "u-mgr2")
    assert (detail["rule_code"], detail["word_start"], detail["word_end"], detail["quote"]) == ("R2", 0, 1, "aap bewakoof")


# ------------------------------------------------------------------ reprocess protection
def forbid_provider(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("the provider must not be reached when reprocessing is blocked")

    monkeypatch.setattr(transcription_service, "transcribe", boom)


def state(db):
    db.expire_all()
    return (
        [(s.id, s.speaker, s.speaker_override, s.text) for s in db.query(TranscriptSegment).order_by(TranscriptSegment.id)],
        [(v.id, v.source, v.quote, v.needs_speaker_review) for v in db.query(Violation).order_by(Violation.id)],
        [(r.violation_id, r.decision) for r in db.query(Review).order_by(Review.violation_id)],
        db.get(Call, CALL).score,
    )


def prepare_audio(db, scratch_audio):
    c = db.get(Call, CALL)
    c.has_audio, c.audio_path = True, "data/audio/pipe.wav"
    db.commit()
    (scratch_audio / "pipe.wav").write_bytes(b"RIFF....WAVEfmt ")


from tests.integration.test_pipeline import scratch_audio  # noqa: E402,F401  (fixture)


@pytest.mark.parametrize("work,expected", [
    ("speaker", "corrected speaker"),
    ("finding", "manually added"),
    ("decision", "review decision"),
])
def test_reprocessing_is_blocked_when_reviewer_work_exists(client, db_session, call, scratch_audio, monkeypatch, work, expected):
    if work == "speaker":
        speaker(client, "seg-2", "agent")
    elif work == "finding":
        finding(client)
    else:
        client.post("/api/violations/v-1/review", headers=MGR2_HEADERS, json={"decision": "confirmed", "comment": "ok"})
    prepare_audio(db_session, scratch_audio)
    before = state(db_session)
    forbid_provider(monkeypatch)

    r = client.post(f"/api/calls/{CALL}/reprocess", headers=MGR2_HEADERS)

    assert r.status_code == 409 and r.json()["error"]["code"] == "REVIEWER_WORK_EXISTS"
    assert expected in r.json()["error"]["message"] and "blocked" in r.json()["error"]["message"]
    assert state(db_session) == before  # nothing lost, nothing half-written
    assert db_session.get(Call, CALL).status == "ready" and CALL not in pipeline._ACTIVE_CALLS


def test_pipeline_refuses_before_any_provider_call(db_session, call, scratch_audio, monkeypatch):
    reviewer_service.correct_speaker(db_session, call, "seg-2", "agent", None, db_session.get(__import__("app.models", fromlist=["AppUser"]).AppUser, "u-mgr2"))
    prepare_audio(db_session, scratch_audio)
    forbid_provider(monkeypatch)
    with pytest.raises(pipeline.ReviewerWorkExistsError):
        pipeline.process_call(CALL, db_session)


def test_reviewer_work_report_names_what_would_be_lost(db_session, call):
    assert reviewer_service.reviewer_work(db_session, CALL) == []  # an undecided analysis may be reprocessed freely
    actor = db_session.get(__import__("app.models", fromlist=["AppUser"]).AppUser, "u-mgr2")
    reviewer_service.correct_speaker(db_session, call, "seg-2", "agent", None, actor)
    reviewer_service.create_manual_finding(db_session, call, actor, rule_code="R2", segment_id="seg-3", word_start=1, word_end=1, comment="c")
    assert reviewer_service.reviewer_work(db_session, CALL) == ["1 corrected speaker label(s)", "1 manually added finding(s)"]
