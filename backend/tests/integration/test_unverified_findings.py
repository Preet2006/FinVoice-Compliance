"""
Batch 1: never lose a finding, never falsely report a clean call.

Pipeline persistence of evidence-invalid findings, the call-level outcome in
the API, and the explicit audio-attestation path for confirming them.
"""

from datetime import datetime

from app.models import Call, TranscriptSegment, Violation
from app.services import llm_service, pipeline, scoring_service
from tests.conftest import HEAD_HEADERS, MGR2_HEADERS, CALLER3_HEADERS
from tests.integration.test_pipeline import (  # noqa: F401  (scratch_audio is a fixture)
    _compliance,
    _r3,
    _threat_segments,
    make_queued_call,
    mock_compliance,
    mock_speaker_labelling,
    mock_transcription,
    raw_segments,
    scratch_audio,
    write_fake_audio,
)
from tests.integration.test_safe_reprocess import quota_failure

KIND = llm_service.KIND_INVALID_EVIDENCE


def rejection(rule="R3", seq=1, quote="police aur jail ka naam", reason="the quote does not appear in the cited segment",
              kind=KIND, explanation="threat", confidence=0.8):
    return {
        "entry": {"rule_code": rule, "segment_index": seq, "quote": quote, "explanation": explanation, "confidence": confidence},
        "reason": reason,
        "kind": kind,
    }


def run(db_session, scratch_audio, monkeypatch, call_id, violations=(), rejected=()):
    call = make_queued_call(db_session, call_id=call_id)
    write_fake_audio(scratch_audio, "pipe.wav")
    mock_transcription(monkeypatch, raw_segments())
    mock_speaker_labelling(monkeypatch, _threat_segments())
    result = _compliance(*violations)
    result.rejected_findings = list(rejected)
    mock_compliance(monkeypatch, result)
    return pipeline.process_call(call.id, db_session)


def rows(db_session, call_id):
    db_session.expire_all()
    return db_session.query(Violation).filter_by(call_id=call_id).order_by(Violation.id).all()


# --- persistence ------------------------------------------------------------------


def test_valid_segment_with_invalid_quote_is_kept_showing_the_stored_segment_text(db_session, scratch_audio, monkeypatch):
    result = run(db_session, scratch_audio, monkeypatch, "CALL-UNV-01", rejected=[rejection(seq=1)])

    [v] = rows(db_session, "CALL-UNV-01")
    assert v.evidence_status == "unverified" and v.rule_code == "R3" and v.severity == "critical"
    assert v.segment_id == "s-CALL-UNV-01-0001"
    assert v.quote == "aur aapko jail jaana padega"  # the STORED text, not the model's proposal
    assert float(v.start_time_seconds) == 2.0
    assert "does not appear" in v.evidence_reason and "police aur jail ka naam" in v.evidence_reason  # proposal kept separately
    assert "police aur jail ka naam" != v.quote
    assert v.review.decision == "pending" and v.needs_speaker_review is False
    assert result.score == 100  # unverified evidence never deducts


def test_finding_with_no_usable_segment_is_kept_without_inventing_evidence(db_session, scratch_audio, monkeypatch):
    run(db_session, scratch_audio, monkeypatch, "CALL-UNV-02",
        rejected=[rejection(seq=99, reason="the cited segment does not exist")])

    [v] = rows(db_session, "CALL-UNV-02")
    assert v.evidence_status == "unverified"
    assert v.segment_id is None and v.start_time_seconds is None  # attached to no segment
    assert v.quote == pipeline.NO_EVIDENCE_TEXT
    assert "does not exist" in v.evidence_reason and "segment 99" in v.evidence_reason and "police aur jail" in v.evidence_reason


def test_asserted_rule_with_no_evidence_at_all_is_kept(db_session, scratch_audio, monkeypatch):
    placeholder = {"entry": {"rule_code": "R5", "segment_index": None, "quote": None, "explanation": "asked for an OTP"},
                   "reason": "the analysis reported this rule as violated but returned no verifiable evidence", "kind": KIND}
    run(db_session, scratch_audio, monkeypatch, "CALL-UNV-03", rejected=[placeholder])

    [v] = rows(db_session, "CALL-UNV-03")
    assert (v.rule_code, v.severity, v.evidence_status, v.segment_id) == ("R5", "high", "unverified", None)
    assert v.explanation == "asked for an OTP" and "no verifiable evidence" in v.evidence_reason


def test_policy_duplicate_and_unknown_rule_rejections_are_not_stored(db_session, scratch_audio, monkeypatch):
    run(db_session, scratch_audio, monkeypatch, "CALL-UNV-04",
        rejected=[rejection(kind=llm_service.KIND_POLICY), rejection(kind=llm_service.KIND_DUPLICATE), rejection(rule="R9")])
    assert rows(db_session, "CALL-UNV-04") == []


def test_identical_unverified_findings_are_stored_once(db_session, scratch_audio, monkeypatch):
    run(db_session, scratch_audio, monkeypatch, "CALL-UNV-05", rejected=[rejection(), rejection(quote="  POLICE aur jail ka NAAM ")])
    assert len(rows(db_session, "CALL-UNV-05")) == 1


def test_verified_and_unverified_findings_coexist_and_only_verified_deducts(db_session, scratch_audio, monkeypatch):
    result = run(db_session, scratch_audio, monkeypatch, "CALL-UNV-06", violations=[_r3(0, "police bhej dunga")], rejected=[rejection(seq=1)])

    by_status = {v.evidence_status: v for v in rows(db_session, "CALL-UNV-06")}
    assert set(by_status) == {"verified", "unverified"}
    assert len({v.id for v in rows(db_session, "CALL-UNV-06")}) == 2  # no id collision
    assert result.score == 70  # only the verified R3 deducts


def test_an_unverified_finding_is_stored_through_the_one_pipeline(db_session, scratch_audio, monkeypatch):
    run(db_session, scratch_audio, monkeypatch, "CALL-UNV-07", rejected=[rejection()])
    assert rows(db_session, "CALL-UNV-07")[0].evidence_status == "unverified"


def test_reprocessing_does_not_duplicate_unverified_findings(db_session, scratch_audio, monkeypatch):
    run(db_session, scratch_audio, monkeypatch, "CALL-UNV-08", rejected=[rejection()])
    pipeline.process_call("CALL-UNV-08", db_session)
    assert len(rows(db_session, "CALL-UNV-08")) == 1


def test_a_clean_reanalysis_replaces_an_earlier_unverified_finding(db_session, scratch_audio, monkeypatch):
    run(db_session, scratch_audio, monkeypatch, "CALL-UNV-09", rejected=[rejection()])
    mock_compliance(monkeypatch, _compliance())
    pipeline.process_call("CALL-UNV-09", db_session)
    assert rows(db_session, "CALL-UNV-09") == []


def test_failed_reprocess_keeps_the_unverified_finding_and_its_reason(db_session, scratch_audio, monkeypatch):
    run(db_session, scratch_audio, monkeypatch, "CALL-UNV-10", rejected=[rejection()])
    before = [(v.id, v.evidence_status, v.evidence_reason, v.quote) for v in rows(db_session, "CALL-UNV-10")]
    quota_failure(monkeypatch, scratch_audio)

    result = pipeline.process_call("CALL-UNV-10", db_session)

    assert [(v.id, v.evidence_status, v.evidence_reason, v.quote) for v in rows(db_session, "CALL-UNV-10")] == before
    assert result.status == "ready" and result.failure_reason.startswith("Reprocessing failed")


# --- API: outcome and evidence fields ----------------------------------------------------


def add_unverified(db, vid="v-unv", rule="R3", segment="seg-1", reason="The quote does not appear in the cited segment."):
    from app.models import Review

    db.add(Violation(id=vid, call_id="CALL-C3-01", rule_code=rule, segment_id=segment, severity="critical",
                     start_time_seconds=1.0, quote="hum aapke ghar bhi aayenge", explanation="x", confidence=0.7,
                     evidence_status="unverified", evidence_reason=reason))
    db.add(Review(id=f"r-{vid}", violation_id=vid, decision="pending"))
    db.commit()


def detail(client, headers=HEAD_HEADERS, call_id="CALL-C3-01"):
    return client.get(f"/api/calls/{call_id}", headers=headers).json()


def test_violations_expose_evidence_status_and_reason(client, db_session):
    add_unverified(db_session)
    by_id = {v["violation_id"]: v for v in client.get("/api/calls/CALL-C3-01/violations", headers=MGR2_HEADERS).json()}
    assert by_id["v-1"]["evidence_status"] == "verified" and by_id["v-1"]["evidence_reason"] is None
    assert by_id["v-unv"]["evidence_status"] == "unverified"
    assert "does not appear" in by_id["v-unv"]["evidence_reason"]
    assert by_id["v-unv"]["counts_toward_score"] is False


def test_evidence_reason_is_redacted_on_output(client, db_session):
    add_unverified(db_session, reason="Failed near org_01abcdefg see https://console.example.com/x")
    reason = {v["violation_id"]: v for v in client.get("/api/calls/CALL-C3-01/violations", headers=MGR2_HEADERS).json()}["v-unv"]["evidence_reason"]
    assert "org_01abcdefg" not in reason and "console.example.com" not in reason


def test_unresolved_unverified_evidence_makes_the_call_incomplete_with_both_counts(client, db_session):
    add_unverified(db_session)
    body = detail(client)
    assert body["analysis_outcome"] == "incomplete"
    assert body["counted_violations"] == 1 and body["findings_unverified"] == 1  # v-1 still counts
    assert body["provisional_score"] is True


def test_list_endpoint_carries_the_same_outcome(client, db_session):
    add_unverified(db_session)
    items = {i["call_id"]: i for i in client.get("/api/calls", headers=HEAD_HEADERS).json()["items"]}
    assert items["CALL-C3-01"]["analysis_outcome"] == "incomplete"
    assert items["CALL-C3-01"]["findings_unverified"] == 1
    assert items["CALL-C1-01"]["analysis_outcome"] == "not_analyzed"  # metadata only: not 'clean'


def make_call(db, call_id, status="ready", with_segment=True, failure_reason=None):
    db.add(Call(id=call_id, caller_id="u-caller3", office_id="o-02", account_type="personal_loan",
                start_time=datetime(2026, 10, 1, 12, 0), has_audio=True, status=status,
                failure_reason=failure_reason, score=100 if status == "ready" else None))
    if with_segment:
        db.add(TranscriptSegment(id=f"seg-{call_id}", call_id=call_id, seq=0, speaker="agent",
                                 start_time=0, end_time=2, text="hello there"))
    db.commit()


def test_genuinely_clean_call(client, db_session):
    make_call(db_session, "CALL-CLEAN")
    body = detail(client, call_id="CALL-CLEAN")
    assert (body["analysis_outcome"], body["provisional_score"], body["last_reprocess_failed"]) == ("clean", False, False)


def test_failed_call_without_results_is_failed(client, db_session):
    make_call(db_session, "CALL-FAIL", status="failed", with_segment=False, failure_reason="The analysis provider timed out.")
    assert detail(client, call_id="CALL-FAIL")["analysis_outcome"] == "failed"


def test_call_marked_ready_without_a_transcript_is_incomplete_not_clean(client, db_session):
    make_call(db_session, "CALL-EMPTY", with_segment=False)
    assert detail(client, call_id="CALL-EMPTY")["analysis_outcome"] == "incomplete"


def test_failed_reprocess_keeps_the_previous_outcome_and_exposes_the_safe_note(client, db_session):
    note = "Reprocessing failed; the previous results were kept. The analysis provider timed out."
    make_call(db_session, "CALL-PREV", failure_reason=note)
    body = detail(client, call_id="CALL-PREV")
    assert body["analysis_outcome"] == "clean" and body["last_reprocess_failed"] is True
    assert body["failure_reason"] == note and body["score"] == 100  # previous score untouched


def test_still_processing_call_is_not_clean(client, db_session):
    make_call(db_session, "CALL-BUSY", status="analyzing", with_segment=False)
    assert detail(client, call_id="CALL-BUSY")["analysis_outcome"] == "not_analyzed"


# --- review: confirming unverified evidence needs an explicit audio attestation ------------------


def review(client, vid, **body):
    return client.post(f"/api/violations/{vid}/review", json=body, headers=MGR2_HEADERS)


def current_decision(db_session, vid):
    db_session.expire_all()
    return db_session.get(Violation, vid).review.decision


def test_plain_confirm_of_unverified_evidence_is_refused(client, db_session):
    add_unverified(db_session)
    resp = review(client, "v-unv", decision="confirmed")
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "EVIDENCE_VERIFICATION_REQUIRED"
    assert current_decision(db_session, "v-unv") == "pending"


def test_attestation_without_a_comment_is_refused(client, db_session):
    add_unverified(db_session)
    assert review(client, "v-unv", decision="confirmed", evidence_verified=True).status_code == 422
    assert review(client, "v-unv", decision="confirmed", evidence_verified=True, comment="   ").status_code == 422
    assert current_decision(db_session, "v-unv") == "pending"


def test_comment_without_attestation_is_refused(client, db_session):
    add_unverified(db_session)
    assert review(client, "v-unv", decision="confirmed", comment="looks right").status_code == 422


def test_confirmed_unverified_repeating_the_same_evidence_is_not_double_counted(client, db_session):
    add_unverified(db_session)  # same rule (R3) and segment as the verified v-1
    resp = review(client, "v-unv", decision="confirmed", evidence_verified=True, comment="Heard it on the recording.")
    assert resp.status_code == 200 and resp.json()["call"]["score"] == 70  # one incident, deducted once


def test_confirm_with_attestation_and_comment_counts_but_keeps_the_audit_trail(client, db_session):
    add_unverified(db_session, rule="R2")  # a distinct finding: counts once confirmed
    assert scoring_service.compute_score("CALL-C3-01", db_session) == 70  # only v-1 counts while pending

    resp = review(client, "v-unv", decision="confirmed", evidence_verified=True, comment="Heard the threat at 0:41 on the recording.")

    assert resp.status_code == 200 and resp.json()["call"]["score"] == 40  # now counts: 100 - 30 - 30
    got = {v["violation_id"]: v for v in client.get("/api/calls/CALL-C3-01/violations", headers=MGR2_HEADERS).json()}["v-unv"]
    assert got["evidence_status"] == "unverified" and got["evidence_reason"]  # history is not rewritten
    assert got["review"]["comment"].startswith("Heard the threat") and got["review"]["reviewer"]
    assert detail(client)["analysis_outcome"] == "violations_found"  # no longer incomplete


def test_dismissing_unverified_evidence_needs_no_attestation_and_resolves_the_call(client, db_session):
    add_unverified(db_session)
    db_session.query(Violation).filter_by(id="v-1").update({"evidence_status": "verified"})
    resp = review(client, "v-unv", decision="false_alarm", comment="Not what was said.")
    assert resp.status_code == 200 and resp.json()["call"]["score"] == 70
    assert detail(client)["findings_unverified"] == 0


def test_confirming_a_verified_finding_is_unchanged(client, db_session):
    assert review(client, "v-1", decision="confirmed").status_code == 200  # no attestation needed


def test_callers_still_cannot_review_even_unverified_findings(client, db_session):
    add_unverified(db_session)
    resp = client.post("/api/violations/v-unv/review", json={"decision": "false_alarm", "comment": "x"}, headers=CALLER3_HEADERS)
    assert resp.status_code == 403
