"""
Batch 2: safe reprocessing.

A failed reprocess must leave the transcript, violations, manager review
decisions and score exactly as they were, and new results replace the old ones
only after the whole analysis has succeeded.
"""

import threading

import pytest

from app.models import Call, Review, TranscriptSegment, Violation
from app.services import llm_service, pipeline, provider_errors, scoring_service, transcription_service
from tests.integration.test_pipeline import (  # noqa: F401  (scratch_audio is a fixture)
    compliance_with_one_violation,
    labelled_segments,
    make_queued_call,
    mock_analysis_failure,
    mock_compliance,
    mock_speaker_labelling,
    mock_transcription,
    raw_segments,
    scratch_audio,
    write_fake_audio,
)

PRESERVED_PREFIX = "Reprocessing failed; the previous results were kept."


def snapshot(db, call_id):
    db.expire_all()
    call = db.get(Call, call_id)
    segments = db.query(TranscriptSegment).filter_by(call_id=call_id).order_by(TranscriptSegment.seq).all()
    violations = db.query(Violation).filter_by(call_id=call_id).order_by(Violation.id).all()
    reviews = (
        db.query(Review)
        .join(Violation, Review.violation_id == Violation.id)
        .filter(Violation.call_id == call_id)
        .order_by(Review.violation_id)
        .all()
    )
    return {
        "score": call.score,
        "sentiment": call.sentiment,
        "calm": call.agent_stayed_calm,
        "segments": [(s.id, s.seq, s.speaker, s.text, float(s.start_time)) for s in segments],
        "violations": [(v.id, v.rule_code, v.quote, v.explanation, v.needs_speaker_review) for v in violations],
        "reviews": [(r.violation_id, r.decision, r.comment, r.reviewer_id) for r in reviews],
    }


def processed_and_reviewed_call(db_session, scratch_audio, monkeypatch, call_id, decided=False):
    """A call with a stored analysis. By default its review is still pending:
    a call carrying a manager's DECISION is not reprocessable any more (see
    test_reviewer_work_protection.py), so the failure-matrix tests below run on
    an undecided call. `decided=True` gives the manager's confirmed review."""
    make_queued_call(db_session, call_id=call_id)
    write_fake_audio(scratch_audio, "pipe.wav")
    mock_transcription(monkeypatch, raw_segments())
    mock_speaker_labelling(monkeypatch, labelled_segments())
    mock_compliance(monkeypatch, compliance_with_one_violation())
    pipeline.process_call(call_id, db_session)

    violation = db_session.query(Violation).filter_by(call_id=call_id).one()
    if decided:
        violation.review.decision = "confirmed"
        violation.review.comment = "verified against the audio"
        violation.review.reviewer_id = "u-mgr1"
        db_session.commit()
    before = snapshot(db_session, call_id)
    if decided:
        assert before["reviews"] == [(violation.id, "confirmed", "verified against the audio", "u-mgr1")]
    else:
        assert before["reviews"] == [(violation.id, "pending", None, None)]
    assert before["segments"] and before["score"] == 70
    return before


# --- failure injectors ---------------------------------------------------------


def transcription_failure(monkeypatch, scratch_audio):
    def boom(audio_bytes, filename, sleep_fn=None):
        raise transcription_service.TranscriptionFailedError("Transcription failed: provider down")

    monkeypatch.setattr(transcription_service, "transcribe", boom)


def quota_failure(monkeypatch, scratch_audio):
    mock_analysis_failure(monkeypatch, llm_service.LLMQuotaError("The analysis provider's rate or usage limit was reached."))


def invalid_key_failure(monkeypatch, scratch_audio):
    mock_analysis_failure(monkeypatch, llm_service.LLMProviderError("The analysis provider rejected the API key.", kind=provider_errors.AUTH))


def validation_failure(monkeypatch, scratch_audio):
    mock_analysis_failure(monkeypatch, llm_service.LLMResponseValidationError("not valid JSON after retry"))


def unexpected_analysis_error(monkeypatch, scratch_audio):
    mock_analysis_failure(monkeypatch, RuntimeError("internal detail org_SECRET123 at /srv/app/secret/path.py"))


def missing_audio(monkeypatch, scratch_audio):
    (scratch_audio / "pipe.wav").unlink()


def failure_midway_through_saving(monkeypatch, scratch_audio):
    # The new analysis succeeds, then saving dies after the old rows were
    # already deleted and one new row written, all inside one transaction.
    two = llm_service.ComplianceResult(
        violations=[
            llm_service.RawViolation(rule_code="R3", segment_seq=1, quote="hum aapke ghar bhi aayenge", explanation="a", confidence=0.9),
            llm_service.RawViolation(rule_code="R2", segment_seq=0, quote="Namaste", explanation="b", confidence=0.9),
        ],
        borrower_sentiment="negative",
        agent_stayed_calm=False,
    )
    mock_compliance(monkeypatch, two)
    real, calls = pipeline._add_violation, {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("disk full")
        return real(*a, **k)

    monkeypatch.setattr(pipeline, "_add_violation", flaky)


def scoring_failure(monkeypatch, scratch_audio):
    def boom(call_id, db):
        raise RuntimeError("scoring blew up")

    monkeypatch.setattr(scoring_service, "compute_score", boom)


FAILURES = [
    transcription_failure,
    quota_failure,
    invalid_key_failure,
    validation_failure,
    unexpected_analysis_error,
    missing_audio,
    failure_midway_through_saving,
    scoring_failure,
]


# --- failure preserves data -----------------------------------------------------


@pytest.mark.parametrize("inject", FAILURES, ids=lambda f: f.__name__)
def test_failed_reprocess_preserves_transcript_violations_reviews_and_score(db_session, scratch_audio, monkeypatch, inject):
    before = processed_and_reviewed_call(db_session, scratch_audio, monkeypatch, "CALL-SAFE-01")

    inject(monkeypatch, scratch_audio)
    result = pipeline.process_call("CALL-SAFE-01", db_session)

    assert snapshot(db_session, "CALL-SAFE-01") == before  # nothing lost, nothing half-written
    assert result.status == "ready"  # the previous analysis is still the valid one
    assert result.failure_reason.startswith(PRESERVED_PREFIX)
    assert "org_SECRET123" not in result.failure_reason and "/srv/app" not in result.failure_reason
    assert "CALL-SAFE-01" not in pipeline._ACTIVE_CALLS  # lock released after a failure


def test_failed_reprocess_with_no_previous_results_is_simply_failed(db_session, scratch_audio, monkeypatch):
    make_queued_call(db_session, call_id="CALL-SAFE-02")
    write_fake_audio(scratch_audio, "pipe.wav")
    mock_transcription(monkeypatch, raw_segments())
    quota_failure(monkeypatch, scratch_audio)

    result = pipeline.process_call("CALL-SAFE-02", db_session)

    assert result.status == "failed"
    assert not result.failure_reason.startswith(PRESERVED_PREFIX)
    assert db_session.query(TranscriptSegment).filter_by(call_id="CALL-SAFE-02").count() == 0


def test_successful_reprocess_replaces_results_and_clears_the_failure_note(db_session, scratch_audio, monkeypatch):
    before = processed_and_reviewed_call(db_session, scratch_audio, monkeypatch, "CALL-SAFE-03")
    quota_failure(monkeypatch, scratch_audio)
    pipeline.process_call("CALL-SAFE-03", db_session)  # a failed attempt first
    assert db_session.get(Call, "CALL-SAFE-03").failure_reason.startswith(PRESERVED_PREFIX)

    mock_speaker_labelling(monkeypatch, labelled_segments())
    mock_compliance(
        monkeypatch,
        llm_service.ComplianceResult(violations=[], borrower_sentiment="neutral", agent_stayed_calm=True),
    )
    result = pipeline.process_call("CALL-SAFE-03", db_session)

    after = snapshot(db_session, "CALL-SAFE-03")
    assert result.status == "ready" and result.failure_reason is None
    assert after["violations"] == [] and after["score"] == 100  # genuinely replaced
    assert after["segments"] and before["violations"] != after["violations"]


def test_a_failed_reprocess_can_be_retried_successfully(db_session, scratch_audio, monkeypatch):
    before = processed_and_reviewed_call(db_session, scratch_audio, monkeypatch, "CALL-SAFE-04")
    transcription_failure(monkeypatch, scratch_audio)
    pipeline.process_call("CALL-SAFE-04", db_session)
    assert snapshot(db_session, "CALL-SAFE-04") == before

    mock_transcription(monkeypatch, raw_segments())
    result = pipeline.process_call("CALL-SAFE-04", db_session)
    assert result.status == "ready" and result.failure_reason is None


# --- concurrency and stuck-state recovery ----------------------------------------


def test_second_concurrent_run_is_rejected_while_the_first_is_active(db_session, scratch_audio, monkeypatch):
    call = make_queued_call(db_session, call_id="CALL-SAFE-05")
    write_fake_audio(scratch_audio, "pipe.wav")
    started, release = threading.Event(), threading.Event()

    def slow_transcribe(audio_bytes, filename, sleep_fn=None):
        started.set()
        release.wait(10)
        return raw_segments()

    monkeypatch.setattr(transcription_service, "transcribe", slow_transcribe)
    mock_speaker_labelling(monkeypatch, labelled_segments())
    mock_compliance(monkeypatch, compliance_with_one_violation())

    outcome = {}
    worker = threading.Thread(target=lambda: outcome.update(call=pipeline.process_call(call.id, db_session)))
    worker.start()
    try:
        assert started.wait(10)
        with pytest.raises(pipeline.AlreadyProcessingError):  # rejected before it touches the database
            pipeline.process_call(call.id, db_session)
    finally:
        release.set()
        worker.join(15)

    assert outcome["call"].status == "ready"
    assert db_session.query(Violation).filter_by(call_id=call.id).count() == 1  # processed once, not twice
    assert call.id not in pipeline._ACTIVE_CALLS


@pytest.mark.parametrize("stuck_status", ["transcribing", "analyzing"])
def test_call_abandoned_mid_run_is_recovered_not_blocked_forever(db_session, scratch_audio, monkeypatch, stuck_status):
    call = make_queued_call(db_session, call_id=f"CALL-STUCK-{stuck_status}")
    call.status = stuck_status  # a worker died after claiming it; nothing is running
    db_session.commit()
    write_fake_audio(scratch_audio, "pipe.wav")
    mock_transcription(monkeypatch, raw_segments())
    mock_speaker_labelling(monkeypatch, labelled_segments())
    mock_compliance(monkeypatch, compliance_with_one_violation())

    result = pipeline.process_call(call.id, db_session)

    assert result.status == "ready" and result.score == 70


def test_abandoned_call_with_earlier_results_keeps_them_if_recovery_fails(db_session, scratch_audio, monkeypatch):
    before = processed_and_reviewed_call(db_session, scratch_audio, monkeypatch, "CALL-SAFE-06")
    call = db_session.get(Call, "CALL-SAFE-06")
    call.status = "analyzing"  # a later run was abandoned mid-way
    db_session.commit()
    transcription_failure(monkeypatch, scratch_audio)

    result = pipeline.process_call("CALL-SAFE-06", db_session)

    assert snapshot(db_session, "CALL-SAFE-06") == before
    assert result.status == "ready"  # no longer stuck in 'analyzing'


def test_unexpected_exception_never_leaves_a_call_stuck_in_progress(db_session, scratch_audio, monkeypatch):
    make_queued_call(db_session, call_id="CALL-SAFE-07")
    write_fake_audio(scratch_audio, "pipe.wav")
    mock_transcription(monkeypatch, raw_segments())
    mock_speaker_labelling(monkeypatch, labelled_segments())
    unexpected_analysis_error(monkeypatch, scratch_audio)

    result = pipeline.process_call("CALL-SAFE-07", db_session)

    assert result.status == "failed" and result.status not in pipeline.IN_PROGRESS_STATUSES
    assert result.failure_reason == "Unexpected processing error; see the server log."
    assert "CALL-SAFE-07" not in pipeline._ACTIVE_CALLS
