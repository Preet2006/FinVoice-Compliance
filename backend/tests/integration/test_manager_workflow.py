"""
The manager's whole workflow, end to end through the HTTP API on scratch data:

  upload -> analysis -> call list -> open call (audio + full transcript) ->
  correct speakers / add missed findings / confirm / false alarm -> sign-off -> dashboard

Providers are stubbed (nothing here can reach Groq); the real upload, pipeline,
validation, scoring, review, audit, media and dashboard code runs.
"""

import pytest

from app.models import Call, ReviewerAudit
from app.services import llm_service, transcription_service, unified_analysis_service
from tests.conftest import CALLER1_HEADERS, CALLER3_HEADERS, HEAD_HEADERS, MGR1_HEADERS, MGR2_HEADERS
from tests.integration.test_uploads_api import VALID_HEADER, make_wav_bytes, scratch_audio_dir, upload  # noqa: F401

AGENT_OPENING = "Namaste main Pooja bol rahi hoon Shaha Finlease se loan payment ke baare mein"

# call -> (audio seconds, [(speaker the AI will label, text)], what the AI reports)
CALLS = {
    "CALL-E2E-1": (1, [("agent", AGENT_OPENING), ("borrower", "haan boliye"), ("borrower", "payment kal tak ho jayega")], None),
    "CALL-E2E-2": (2, [("agent", AGENT_OPENING), ("borrower", "aap bewakoof ho bilkul"), ("borrower", "theek hai")], None),
    "CALL-E2E-3": (3, [("agent", AGENT_OPENING), ("agent", "kal tak payment kar dijiye"), ("borrower", "theek hai")], ("R3", 1, "kal tak payment kar dijiye")),
}
FILES = {cid: f"e2e{n}.wav" for n, cid in enumerate(CALLS, start=1)}
SEGMENTS = {FILES[cid]: spec[1] for cid, spec in CALLS.items()}
AI_FINDING = {spec[1][0][1]: spec[2] for spec in CALLS.values()}  # keyed by first line - all the same, so key by call below


def install_providers(monkeypatch):
    by_text = {tuple(t for _, t in segs): (cid, segs, CALLS[cid][2]) for cid, (_, segs, _) in CALLS.items()}

    def transcribe(audio_bytes, filename, sleep_fn=None):
        return [transcription_service.RawSegment(seq=i, start=i * 4.0, end=i * 4.0 + 3.5, text=t) for i, (_, t) in enumerate(SEGMENTS[filename])]

    def label(raw, sleep_fn=None):
        _, segs, _ = by_text[tuple(s.text for s in raw)]
        return [llm_service.LabelledSegment(seq=s.seq, speaker=segs[i][0], start=s.start, end=s.end, text=s.text, confidence=0.95) for i, s in enumerate(raw)]

    def analyse(labelled, sleep_fn=None):
        _, _, finding = by_text[tuple(s.text for s in labelled)]
        violations = [llm_service.RawViolation(rule_code=finding[0], segment_seq=finding[1], quote=finding[2], explanation="AI finding", confidence=0.9)] if finding else []
        return llm_service.ComplianceResult(violations=violations, borrower_sentiment="neutral", agent_stayed_calm=True)

    monkeypatch.setattr(transcription_service, "transcribe", transcribe)
    def analyse_call(raw, call, sleep_fn=None, include_examples=True):
        labelled = label(raw)
        return labelled, analyse(labelled)

    monkeypatch.setattr(unified_analysis_service, "analyse_call", analyse_call)


@pytest.fixture()
def uploaded(client, scratch_audio_dir, monkeypatch):  # noqa: F811
    """Three recordings uploaded for caller1 (manager mgr1's team), different lengths so each audio is identifiable."""
    csv_text = VALID_HEADER + "".join(
        f"{cid},{FILES[cid]},u-caller1,o-01,personal_loan,2026-10-1{n}T10:00:00,hi\n" for n, cid in enumerate(CALLS, start=1)
    )
    files = [(FILES[cid], make_wav_bytes(duration_seconds=spec[0])) for cid, spec in CALLS.items()]
    r = upload(client, MGR1_HEADERS, csv_text, files)
    assert r.status_code == 202 and len(r.json()["accepted"]) == 3 and r.json()["rejected"] == []
    install_providers(monkeypatch)
    return {cid: make_wav_bytes(duration_seconds=spec[0]) for cid, spec in CALLS.items()}


@pytest.fixture()
def analysed(client, uploaded):
    for cid in CALLS:
        r = client.post(f"/api/calls/{cid}/reprocess", headers=MGR1_HEADERS)
        assert r.status_code == 200 and r.json()["status"] == "ready", r.text
    return uploaded


def calls(client, headers=MGR1_HEADERS, **params):
    r = client.get("/api/calls", headers=headers, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def violations(client, cid, headers=MGR1_HEADERS):
    return client.get(f"/api/calls/{cid}/violations", headers=headers).json()


def detail(client, cid, headers=MGR1_HEADERS):
    return client.get(f"/api/calls/{cid}", headers=headers).json()


def transcript(client, cid, headers=MGR1_HEADERS):
    return client.get(f"/api/calls/{cid}/transcript", headers=headers).json()


# ------------------------------------------------------------------- upload -> analysis status
def test_uploaded_calls_start_queued_and_unreviewed(client, uploaded):
    items = {c["call_id"]: c for c in calls(client, analysis_status="queued")["items"]}
    assert set(items) == set(CALLS)
    assert all(c["status"] == "queued" and c["review_status"] == "not_applicable" and c["score"] is None for c in items.values())


def test_finishing_the_analysis_does_not_mark_a_call_reviewed(client, analysed):
    for c in calls(client, analysis_status="completed")["items"]:
        assert c["status"] == "ready" and c["review_status"] == "pending_review"
    assert calls(client, review_status="reviewed")["total"] == 0
    assert client.get("/api/dashboard", headers=MGR1_HEADERS).json()["reviewed_calls"] == 0


def test_analysis_status_filter_covers_every_state(client, analysed, db_session):
    db_session.get(Call, "CALL-E2E-1").status = "transcribing"
    db_session.get(Call, "CALL-E2E-2").status = "failed"
    db_session.commit()
    by = lambda s: {c["call_id"] for c in calls(client, analysis_status=s)["items"]}  # noqa: E731
    assert (by("processing"), by("failed"), by("completed"), by("queued")) == ({"CALL-E2E-1"}, {"CALL-E2E-2"}, {"CALL-E2E-3"}, set())
    db_session.get(Call, "CALL-E2E-3").status = "analyzing"  # both in-progress states count as "processing"
    db_session.commit()
    assert by("processing") == {"CALL-E2E-1", "CALL-E2E-3"} and by("completed") == set()


# ------------------------------------------------------------------- scenario 1: clean call
def test_scenario_1_clean_call_end_to_end(client, analysed):
    item = next(c for c in calls(client)["items"] if c["call_id"] == "CALL-E2E-1")
    assert (item["status"], item["score"], item["findings_total"], item["review_status"]) == ("ready", 100, 0, "pending_review")
    d = detail(client, "CALL-E2E-1")
    assert d["analysis_outcome"] == "clean" and d["duration_seconds"] == 1 and d["has_audio"] is True

    segs = transcript(client, "CALL-E2E-1")  # the COMPLETE transcript, not only findings
    assert [s["text"] for s in segs] == [t for _, t in CALLS["CALL-E2E-1"][1]]
    assert [s["seq"] for s in segs] == [0, 1, 2] and segs[0]["start_time"] == 0.0 and segs[1]["start_time"] == 4.0
    assert all(s["ai_speaker"] == s["speaker"] and s["speaker_corrected"] is False for s in segs)
    assert violations(client, "CALL-E2E-1") == []

    r = client.post("/api/calls/CALL-E2E-1/review/complete", headers=MGR1_HEADERS)  # a clean call needs no manual finding
    assert r.status_code == 200 and r.json()["review_status"] == "reviewed" and r.json()["reviewed_by"] == "Priya Sharma"
    assert detail(client, "CALL-E2E-1")["review_status"] == "reviewed" and detail(client, "CALL-E2E-1")["review_completed_by"] == "Priya Sharma"


# ------------------------------------------------------------------- scenario 2: AI missed a violation
def test_scenario_2_missed_violation_is_added_and_scored_and_audited(client, analysed):
    seg = transcript(client, "CALL-E2E-2")[1]
    assert seg["text"] == "aap bewakoof ho bilkul" and seg["speaker"] == "borrower"  # the AI mislabelled the agent's insult
    body = {"rule_code": "R2", "segment_id": seg["id"], "word_start": 1, "word_end": 1, "comment": "Agent insulted the borrower"}
    assert client.post("/api/calls/CALL-E2E-2/violations", headers=MGR1_HEADERS, json=body).json()["error"]["code"] == "SPEAKER_NOT_AGENT"

    fixed = client.post(f"/api/calls/CALL-E2E-2/segments/{seg['id']}/speaker", headers=MGR1_HEADERS, json={"speaker": "agent", "comment": "heard the agent"})
    assert fixed.status_code == 200
    added = client.post("/api/calls/CALL-E2E-2/violations", headers=MGR1_HEADERS, json=body)
    assert added.status_code == 201 and added.json()["violation"]["quote"] == "bewakoof" and added.json()["call"]["score"] == 70
    assert detail(client, "CALL-E2E-2")["score"] == 70
    [v] = violations(client, "CALL-E2E-2")
    assert (v["source"], v["score_deduction"], v["start_time_seconds"]) == ("manual", 30, 4.0)

    history = client.get("/api/calls/CALL-E2E-2/audit", headers=MGR1_HEADERS).json()
    assert [h["action"] for h in history] == ["speaker_correction", "manual_finding"]
    assert history[1]["detail"]["quote"] == "bewakoof" and history[0]["actor"] == "Priya Sharma"


# ------------------------------------------------------------------- scenario 3: false positive
def test_scenario_3_false_alarm_updates_the_score(client, analysed):
    [v] = violations(client, "CALL-E2E-3")
    assert (v["rule_code"], v["source"], v["score_deduction"], v["counts_toward_score"]) == ("R3", "ai", 30, True)
    assert detail(client, "CALL-E2E-3")["score"] == 70
    r = client.post(f"/api/violations/{v['violation_id']}/review", headers=MGR1_HEADERS, json={"decision": "false_alarm", "comment": "a polite reminder"})
    assert r.status_code == 200 and r.json()["call"]["score"] == 100
    [v] = violations(client, "CALL-E2E-3")
    assert (v["review"]["decision"], v["score_deduction"], v["counts_toward_score"]) == ("false_alarm", 0, False)  # kept, shown, not scored
    item = next(c for c in calls(client)["items"] if c["call_id"] == "CALL-E2E-3")
    assert item["findings_total"] == 0 and item["score"] == 100


# ------------------------------------------------------------------- scenario 4: speaker correction
def test_scenario_4_speaker_correction_keeps_the_ai_label_and_drives_scoring(client, analysed):
    [v] = violations(client, "CALL-E2E-3")
    seg = next(s for s in transcript(client, "CALL-E2E-3") if s["seq"] == 1)
    r = client.post(f"/api/calls/CALL-E2E-3/segments/{seg['id']}/speaker", headers=MGR1_HEADERS, json={"speaker": "borrower"})
    assert r.status_code == 200 and r.json()["call"]["score"] == 100  # the agent rule no longer applies to this line
    after = next(s for s in transcript(client, "CALL-E2E-3") if s["seq"] == 1)
    assert (after["speaker"], after["ai_speaker"], after["speaker_corrected"], after["corrected_by"]) == ("borrower", "agent", True, "Priya Sharma")
    [v2] = violations(client, "CALL-E2E-3")
    assert v2["needs_speaker_review"] is True and v2["review"]["decision"] == "pending"  # held for the reviewer, not deleted
    assert [h["detail"]["new_speaker"] for h in client.get("/api/calls/CALL-E2E-3/audit", headers=MGR1_HEADERS).json()] == ["borrower"]


# ------------------------------------------------------------------- scenario 5: audio <-> transcript timing
def test_scenario_5_findings_and_segments_carry_real_timestamps_and_the_audio_is_seekable(client, analysed):
    seg = next(s for s in transcript(client, "CALL-E2E-3") if s["seq"] == 1)
    [v] = violations(client, "CALL-E2E-3")
    assert v["start_time_seconds"] == seg["start_time"] == 4.0  # the finding points at its segment's real start
    audio = client.get("/api/media/CALL-E2E-3", headers=MGR1_HEADERS)
    assert audio.status_code == 200 and audio.content == analysed["CALL-E2E-3"] and audio.headers["accept-ranges"] == "bytes"
    part = client.get("/api/media/CALL-E2E-3", headers={**MGR1_HEADERS, "Range": "bytes=100-199"})
    assert part.status_code == 206 and part.content == analysed["CALL-E2E-3"][100:200]
    assert part.headers["content-range"] == f"bytes 100-199/{len(analysed['CALL-E2E-3'])}"


# ------------------------------------------------------------------- scenario 6: roles
def test_scenario_6_role_permissions_are_enforced_by_the_backend(client, analysed):
    seg = transcript(client, "CALL-E2E-2")[1]
    [v3] = violations(client, "CALL-E2E-3")
    body = {"rule_code": "R2", "segment_id": seg["id"], "word_start": 1, "word_end": 1, "comment": "c"}
    # the caller reads their own calls and nothing else
    assert {c["call_id"] for c in calls(client, CALLER1_HEADERS, analysis_status="completed")["items"]} == set(CALLS)
    assert client.get("/api/calls/CALL-E2E-1", headers=CALLER1_HEADERS).status_code == 200
    assert client.get("/api/media/CALL-E2E-1", headers=CALLER1_HEADERS).status_code == 200
    for method, url, json in [
        ("post", f"/api/calls/CALL-E2E-2/segments/{seg['id']}/speaker", {"speaker": "agent"}),
        ("post", "/api/calls/CALL-E2E-2/violations", body),
        ("post", f"/api/violations/{v3['violation_id']}/review", {"decision": "confirmed"}),
        ("post", "/api/calls/CALL-E2E-1/review/complete", None),
        ("post", "/api/calls/CALL-E2E-1/reprocess", None),
        ("get", "/api/calls/CALL-E2E-2/audit", None),
    ]:
        r = getattr(client, method)(url, headers=CALLER1_HEADERS, **({"json": json} if json is not None else {}))
        assert r.status_code == 403, (url, r.status_code)
    # another team's manager cannot even see them
    for url in ("/api/calls/CALL-E2E-1", "/api/calls/CALL-E2E-1/transcript", "/api/media/CALL-E2E-1"):
        assert client.get(url, headers=MGR2_HEADERS).status_code == 404
    assert client.post("/api/calls/CALL-E2E-1/review/complete", headers=MGR2_HEADERS).status_code == 404
    assert client.get("/api/calls", headers=CALLER3_HEADERS).json()["total"] == 1  # caller3 has only their own call
    # the head can do the manager's work
    assert client.post(f"/api/calls/CALL-E2E-2/segments/{seg['id']}/speaker", headers=HEAD_HEADERS, json={"speaker": "agent"}).status_code == 200
    assert client.post("/api/calls/CALL-E2E-2/violations", headers=HEAD_HEADERS, json=body).status_code == 201
    assert client.post("/api/calls/CALL-E2E-1/review/complete", headers=HEAD_HEADERS).status_code == 200


# ------------------------------------------------------------------- scenario 7: several calls
def test_scenario_7_each_call_has_its_own_audio_and_transcript_and_the_manager_can_page_through_them(client, analysed):
    for cid, spec in CALLS.items():
        assert client.get(f"/api/media/{cid}", headers=MGR1_HEADERS).content == analysed[cid]
        assert [s["text"] for s in transcript(client, cid)] == [t for _, t in spec[1]]
        assert detail(client, cid)["duration_seconds"] == spec[0]
    assert len({analysed[c] for c in CALLS}) == 3  # three genuinely different recordings
    seen, page = [], 1
    while True:
        r = calls(client, analysis_status="completed", page=page, page_size=2)
        seen += [c["call_id"] for c in r["items"]]
        if page * 2 >= r["total"]:
            break
        page += 1
    assert sorted(seen) == sorted(CALLS) and r["total"] == 3 and len(seen) == len(set(seen))


# ------------------------------------------------------------------- call list: filters, search, sort
def test_pending_review_filter_and_sorting(client, analysed):
    client.post("/api/calls/CALL-E2E-1/review/complete", headers=MGR1_HEADERS)
    pending = {c["call_id"] for c in calls(client, review_status="pending_review")["items"]}
    assert pending == {"CALL-E2E-2", "CALL-E2E-3"}  # the signed-off call leaves the pending list
    assert {c["call_id"] for c in calls(client, review_status="reviewed")["items"]} == {"CALL-E2E-1"}
    by_score = [c["call_id"] for c in calls(client, analysis_status="completed", sort="score_asc")["items"]]
    assert by_score[0] == "CALL-E2E-3"  # 70 before the two 100s
    assert [c["call_id"] for c in calls(client, analysis_status="completed", sort="risk")["items"]][0] == "CALL-E2E-3"
    dates = [c["start_time"] for c in calls(client, analysis_status="completed", sort="date_asc")["items"]]
    assert dates == sorted(dates)
    assert [c["start_time"] for c in calls(client, analysis_status="completed", sort="date_desc")["items"]] == sorted(dates, reverse=True)


def test_search_by_call_id_caller_or_transcript_and_date_filter(client, analysed):
    ids = lambda **p: {c["call_id"] for c in calls(client, analysis_status="completed", **p)["items"]}  # noqa: E731
    assert ids(q="E2E-2") == {"CALL-E2E-2"}  # call id
    assert ids(q="rahul kumar") == set(CALLS)  # caller name (case-insensitive)
    assert ids(q="bewakoof") == {"CALL-E2E-2"}  # transcript text
    assert ids(q="no-such-thing") == set()
    assert ids(date_from="2026-10-12T00:00:00", date_to="2026-10-12T23:59:59") == {"CALL-E2E-2"}


def test_invalid_filter_values_are_rejected(client, analysed):
    for params in ({"analysis_status": "bogus"}, {"review_status": "bogus"}, {"sort": "bogus"}):
        assert client.get("/api/calls", headers=MGR1_HEADERS, params=params).status_code == 422


# ------------------------------------------------------------------- sign-off rules
def test_review_cannot_be_completed_while_a_finding_is_undecided(client, analysed):
    r = client.post("/api/calls/CALL-E2E-3/review/complete", headers=MGR1_HEADERS)
    assert r.status_code == 409 and r.json()["error"]["code"] == "UNDECIDED_FINDINGS" and "1 finding" in r.json()["error"]["message"]
    [v] = violations(client, "CALL-E2E-3")
    client.post(f"/api/violations/{v['violation_id']}/review", headers=MGR1_HEADERS, json={"decision": "confirmed", "comment": "yes"})
    assert client.post("/api/calls/CALL-E2E-3/review/complete", headers=MGR1_HEADERS).status_code == 200
    assert detail(client, "CALL-E2E-3")["score"] == 70  # the confirmed deduction stands


def test_a_call_that_was_not_analysed_cannot_be_reviewed(client, uploaded):
    r = client.post("/api/calls/CALL-E2E-1/review/complete", headers=MGR1_HEADERS)
    assert r.status_code == 409 and r.json()["error"]["code"] == "NOT_ANALYSED"


def test_a_later_reviewer_change_reopens_the_review_and_it_is_audited(client, analysed):
    client.post("/api/calls/CALL-E2E-1/review/complete", headers=MGR1_HEADERS)
    seg = transcript(client, "CALL-E2E-1")[1]
    client.post(f"/api/calls/CALL-E2E-1/segments/{seg['id']}/speaker", headers=MGR1_HEADERS, json={"speaker": "agent"})
    assert detail(client, "CALL-E2E-1")["review_status"] == "pending_review"
    actions = [h["action"] for h in client.get("/api/calls/CALL-E2E-1/audit", headers=MGR1_HEADERS).json()]
    assert actions == ["review_completed", "review_reopened", "speaker_correction"]


def test_completing_twice_is_harmless(client, analysed):
    first = client.post("/api/calls/CALL-E2E-1/review/complete", headers=MGR1_HEADERS).json()
    assert client.post("/api/calls/CALL-E2E-1/review/complete", headers=MGR1_HEADERS).json()["reviewed_at"] == first["reviewed_at"]


# ------------------------------------------------------------------- reprocess safeguard
def test_reprocessing_is_blocked_with_a_clear_message_once_reviewer_work_exists(client, analysed, monkeypatch):
    client.post("/api/calls/CALL-E2E-1/review/complete", headers=MGR1_HEADERS)
    monkeypatch.setattr(transcription_service, "transcribe", lambda *a, **k: (_ for _ in ()).throw(AssertionError("provider reached")))
    r = client.post("/api/calls/CALL-E2E-1/reprocess", headers=MGR1_HEADERS)
    assert r.status_code == 409 and r.json()["error"]["code"] == "REVIEWER_WORK_EXISTS"
    assert "completed review sign-off" in r.json()["error"]["message"] and "blocked" in r.json()["error"]["message"]
    assert [s["text"] for s in transcript(client, "CALL-E2E-1")] == [t for _, t in CALLS["CALL-E2E-1"][1]]  # nothing erased
    assert detail(client, "CALL-E2E-1")["review_status"] == "reviewed"


# ------------------------------------------------------------------- dashboard agrees with the stored data
def test_dashboard_reflects_the_review_workflow(client, analysed):
    client.post("/api/calls/CALL-E2E-1/review/complete", headers=MGR1_HEADERS)
    [v] = violations(client, "CALL-E2E-3")
    client.post(f"/api/violations/{v['violation_id']}/review", headers=MGR1_HEADERS, json={"decision": "confirmed"})
    d = client.get("/api/dashboard", headers=MGR1_HEADERS).json()
    assert (d["total_calls"], d["calls_analyzed"], d["reviewed_calls"], d["calls_awaiting_review"]) == (5, 3, 1, 2)  # 2 more fixture calls have no audio
    assert (d["verified_violations"], d["counted_violations"]) == (1, 1) and d["review_outcomes"]["confirmed"] == 1
    assert d["average_score"] == round((100 + 100 + 70) / 3, 1) and d["accuracy"]["available"] is False
    assert db_count(client) == d["total_calls"]


def db_count(client):
    return calls(client, page_size=200)["total"]


def test_audit_trail_is_append_only_across_the_workflow(client, analysed, db_session):
    seg = transcript(client, "CALL-E2E-2")[1]
    client.post(f"/api/calls/CALL-E2E-2/segments/{seg['id']}/speaker", headers=MGR1_HEADERS, json={"speaker": "agent"})
    n1 = db_session.query(ReviewerAudit).count()
    client.post(f"/api/calls/CALL-E2E-2/segments/{seg['id']}/speaker", headers=MGR1_HEADERS, json={"speaker": "borrower"})
    assert db_session.query(ReviewerAudit).count() == n1 + 1  # entries are only ever added
