from datetime import datetime

import pytest

from app.models import Call, TranscriptSegment, Violation
from app.services import llm_service, pipeline, transcription_service
from app.config import settings


def make_queued_call(db, call_id="CALL-PIPE-01", caller_id="u-caller1", office_id="o-01", filename="pipe.wav", hour=10):
    call = Call(
        id=call_id,
        caller_id=caller_id,
        office_id=office_id,
        account_type="personal_loan",
        start_time=datetime(2026, 10, 15, hour, 0, 0),
        duration_seconds=60,
        language="hi",
        has_audio=True,
        audio_path=f"data/audio/{filename}",
        status="queued",
        is_ground_truth=False,
    )
    db.add(call)
    db.commit()
    return call


@pytest.fixture()
def scratch_audio(tmp_path, monkeypatch):
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    monkeypatch.setattr(settings, "audio_dir", str(audio_dir))
    return audio_dir


def write_fake_audio(scratch_audio, filename="pipe.wav"):
    (scratch_audio / filename).write_bytes(b"RIFF....WAVEfmt ")


def mock_transcription(monkeypatch, segments):
    monkeypatch.setattr(
        transcription_service, "transcribe", lambda audio_bytes, filename, sleep_fn=None: segments
    )


# The analysis is ONE call that returns (labelled segments, compliance result). These two helpers keep the
# call sites readable: each sets one half, and together they fake unified_analysis_service.analyse_call.
_ANALYSIS = {"labelled": None, "compliance": None}


def _install_analysis(monkeypatch):
    from app.services import unified_analysis_service

    monkeypatch.setattr(unified_analysis_service, "analyse_call", lambda raw, call, **kw: (_ANALYSIS["labelled"], _ANALYSIS["compliance"]))


def mock_speaker_labelling(monkeypatch, labelled):
    _ANALYSIS["labelled"] = labelled
    _install_analysis(monkeypatch)


def mock_compliance(monkeypatch, result):
    _ANALYSIS["compliance"] = result
    _install_analysis(monkeypatch)


def mock_analysis_failure(monkeypatch, exc):
    """The analysis raises `exc` (a provider or validation error, or anything unexpected)."""
    from app.services import unified_analysis_service

    def boom(raw, call, **kw):
        raise exc

    monkeypatch.setattr(unified_analysis_service, "analyse_call", boom)


def raw_segments():
    return [
        transcription_service.RawSegment(seq=0, start=0.0, end=2.0, text="Namaste main Pooja bol rahi hoon"),
        transcription_service.RawSegment(seq=1, start=2.0, end=4.0, text="hum aapke ghar bhi aayenge"),
        transcription_service.RawSegment(seq=2, start=4.0, end=6.0, text="theek hai dhanyavaad"),
    ]


def labelled_segments():
    # confidence=0.95 on every segment: these tests are exercising the
    # happy path / R1 / failure-handling behavior, not the new confidence
    # gating (see test_speaker_review_gating.py for that), so they use a
    # confidently-labelled transcript on purpose.
    return [
        llm_service.LabelledSegment(seq=0, speaker="agent", start=0.0, end=2.0, text="Namaste main Pooja bol rahi hoon", confidence=0.95),
        llm_service.LabelledSegment(seq=1, speaker="agent", start=2.0, end=4.0, text="hum aapke ghar bhi aayenge", confidence=0.95),
        llm_service.LabelledSegment(seq=2, speaker="borrower", start=4.0, end=6.0, text="theek hai dhanyavaad", confidence=0.95),
    ]


def compliance_with_one_violation():
    return llm_service.ComplianceResult(
        violations=[
            llm_service.RawViolation(
                rule_code="R3",
                segment_seq=1,
                quote="hum aapke ghar bhi aayenge",
                explanation="Threatens an in-person visit.",
                confidence=0.9,
            )
        ],
        borrower_sentiment="negative",
        agent_stayed_calm=False,
    )


def test_full_pipeline_happy_path(db_session, scratch_audio, monkeypatch):
    call = make_queued_call(db_session)
    write_fake_audio(scratch_audio, "pipe.wav")
    mock_transcription(monkeypatch, raw_segments())
    mock_speaker_labelling(monkeypatch, labelled_segments())
    mock_compliance(monkeypatch, compliance_with_one_violation())

    result = pipeline.process_call(call.id, db_session)

    assert result.status == "ready"
    assert result.sentiment == "negative"
    assert result.agent_stayed_calm is False

    segments = db_session.query(TranscriptSegment).filter_by(call_id=call.id).order_by(TranscriptSegment.seq).all()
    assert len(segments) == 3
    assert segments[0].speaker == "agent"
    assert segments[2].speaker == "borrower"
    # Timestamps preserved unchanged from STT.
    assert float(segments[1].start_time) == 2.0

    violations = db_session.query(Violation).filter_by(call_id=call.id).all()
    assert len(violations) == 1
    assert violations[0].rule_code == "R3"
    assert violations[0].severity == "critical"  # from rules.py, not the LLM
    assert violations[0].review.decision == "pending"

    # Score: 100 - 30 (one critical, pending) = 70
    assert result.score == 70


def test_r1_violation_created_for_out_of_hours_call(db_session, scratch_audio, monkeypatch):
    call = make_queued_call(db_session, call_id="CALL-PIPE-R1", hour=21)  # 9pm, outside 8-19
    write_fake_audio(scratch_audio, "pipe.wav")
    mock_transcription(monkeypatch, raw_segments())
    mock_speaker_labelling(monkeypatch, labelled_segments())
    mock_compliance(
        monkeypatch,
        llm_service.ComplianceResult(violations=[], borrower_sentiment="neutral", agent_stayed_calm=True),
    )

    result = pipeline.process_call(call.id, db_session)

    violations = db_session.query(Violation).filter_by(call_id=call.id).all()
    assert len(violations) == 1
    assert violations[0].rule_code == "R1"
    assert violations[0].segment_id is None
    assert violations[0].severity == "high"
    assert result.score == 85  # 100 - 15 (high)


def test_transcription_failure_marks_call_failed(db_session, scratch_audio, monkeypatch):
    call = make_queued_call(db_session, call_id="CALL-PIPE-FAIL")
    write_fake_audio(scratch_audio, "pipe.wav")

    def boom(audio_bytes, filename, sleep_fn=None):
        raise transcription_service.TranscriptionFailedError("Transcription failed: whisper-large-v3-turbo (boom)")

    monkeypatch.setattr(transcription_service, "transcribe", boom)

    result = pipeline.process_call(call.id, db_session)
    assert result.status == "failed"
    assert "Transcription failed" in result.failure_reason


def test_llm_quota_failure_marks_call_failed(db_session, scratch_audio, monkeypatch):
    call = make_queued_call(db_session, call_id="CALL-PIPE-QUOTA")
    write_fake_audio(scratch_audio, "pipe.wav")
    mock_transcription(monkeypatch, raw_segments())

    mock_analysis_failure(monkeypatch, llm_service.LLMQuotaError("LLM quota exceeded or model unavailable: openai/gpt-oss-120b"))

    result = pipeline.process_call(call.id, db_session)
    assert result.status == "failed"
    assert "quota" in result.failure_reason.lower()


def test_missing_audio_file_marks_call_failed(db_session, scratch_audio, monkeypatch):
    call = make_queued_call(db_session, call_id="CALL-PIPE-NOFILE", filename="does_not_exist.wav")
    # Deliberately do not write the file.
    result = pipeline.process_call(call.id, db_session)
    assert result.status == "failed"
    assert "not found" in result.failure_reason.lower()


def test_metadata_only_call_rejected(db_session):
    call = Call(
        id="CALL-PIPE-META",
        caller_id="u-caller1",
        office_id="o-01",
        account_type="personal_loan",
        start_time=datetime(2026, 10, 15, 10, 0, 0),
        has_audio=False,
        status="metadata_only",
    )
    db_session.add(call)
    db_session.commit()

    with pytest.raises(pipeline.CallNotEligibleError):
        pipeline.process_call(call.id, db_session)


def test_actively_running_call_is_rejected_not_double_processed(db_session, scratch_audio):
    call = make_queued_call(db_session, call_id="CALL-PIPE-BUSY")
    pipeline._ACTIVE_CALLS.add(call.id)  # a worker in this process is running it
    try:
        with pytest.raises(pipeline.AlreadyProcessingError):
            pipeline.process_call(call.id, db_session)
    finally:
        pipeline._ACTIVE_CALLS.discard(call.id)
    db_session.refresh(call)
    assert call.status == "queued"  # the rejected attempt changed nothing


def test_reprocess_is_idempotent_no_duplicate_rows(db_session, scratch_audio, monkeypatch):
    call = make_queued_call(db_session, call_id="CALL-PIPE-RETRY")
    write_fake_audio(scratch_audio, "pipe.wav")
    mock_transcription(monkeypatch, raw_segments())
    mock_speaker_labelling(monkeypatch, labelled_segments())
    mock_compliance(monkeypatch, compliance_with_one_violation())

    pipeline.process_call(call.id, db_session)
    first_segment_count = db_session.query(TranscriptSegment).filter_by(call_id=call.id).count()
    first_violation_count = db_session.query(Violation).filter_by(call_id=call.id).count()

    # Re-run (e.g. via the reprocess endpoint) — must not duplicate rows.
    pipeline.process_call(call.id, db_session)
    second_segment_count = db_session.query(TranscriptSegment).filter_by(call_id=call.id).count()
    second_violation_count = db_session.query(Violation).filter_by(call_id=call.id).count()

    assert first_segment_count == second_segment_count == 3
    assert first_violation_count == second_violation_count == 1


def test_reprocess_recovers_a_failed_call(db_session, scratch_audio, monkeypatch):
    call = make_queued_call(db_session, call_id="CALL-PIPE-RECOVER")
    write_fake_audio(scratch_audio, "pipe.wav")

    def boom(audio_bytes, filename, sleep_fn=None):
        raise transcription_service.TranscriptionFailedError("boom")

    monkeypatch.setattr(transcription_service, "transcribe", boom)
    result = pipeline.process_call(call.id, db_session)
    assert result.status == "failed"

    # Now simulate the transient issue being gone and retry.
    mock_transcription(monkeypatch, raw_segments())
    mock_speaker_labelling(monkeypatch, labelled_segments())
    mock_compliance(
        monkeypatch,
        llm_service.ComplianceResult(violations=[], borrower_sentiment="neutral", agent_stayed_calm=True),
    )
    result2 = pipeline.process_call(call.id, db_session)
    assert result2.status == "ready"
    assert result2.failure_reason is None


# --- Duplicate findings -----------------------------------------------------

def _threat_segments():
    return [
        llm_service.LabelledSegment(seq=0, speaker="agent", start=0.0, end=2.0, text="agar paisa nahi aaya to police bhej dunga", confidence=0.95),
        llm_service.LabelledSegment(seq=1, speaker="agent", start=2.0, end=4.0, text="aur aapko jail jaana padega", confidence=0.95),
        llm_service.LabelledSegment(seq=2, speaker="borrower", start=4.0, end=6.0, text="theek hai", confidence=0.95),
    ]


def _r3(seq, quote):
    return llm_service.RawViolation(rule_code="R3", segment_seq=seq, quote=quote, explanation="threat", confidence=0.9)


def _compliance(*violations):
    return llm_service.ComplianceResult(violations=list(violations), borrower_sentiment="negative", agent_stayed_calm=False)


def _run_pipeline(db_session, scratch_audio, monkeypatch, call_id, compliance):
    call = make_queued_call(db_session, call_id=call_id)
    write_fake_audio(scratch_audio, "pipe.wav")
    mock_transcription(monkeypatch, raw_segments())
    mock_speaker_labelling(monkeypatch, _threat_segments())
    mock_compliance(monkeypatch, compliance)
    return pipeline.process_call(call.id, db_session)


def test_identical_finding_is_not_persisted_twice(db_session, scratch_audio, monkeypatch):
    q = "police bhej dunga"
    result = _run_pipeline(db_session, scratch_audio, monkeypatch, "CALL-PIPE-IDENT", _compliance(_r3(0, q), _r3(0, q.upper())))

    assert db_session.query(Violation).filter_by(call_id="CALL-PIPE-IDENT").count() == 1
    assert result.score == 70


def test_different_quotes_on_one_segment_are_both_kept_but_score_once(db_session, scratch_audio, monkeypatch):
    result = _run_pipeline(
        db_session, scratch_audio, monkeypatch, "CALL-PIPE-SAMESEG",
        _compliance(_r3(0, "police bhej dunga"), _r3(0, "agar paisa nahi aaya")),
    )

    assert db_session.query(Violation).filter_by(call_id="CALL-PIPE-SAMESEG").count() == 2  # evidence kept
    assert result.score == 70  # one incident


def test_continuous_threat_split_across_segments_scores_once(db_session, scratch_audio, monkeypatch):
    result = _run_pipeline(
        db_session, scratch_audio, monkeypatch, "CALL-PIPE-SPLIT",
        _compliance(_r3(0, "police bhej dunga"), _r3(1, "jail jaana padega")),
    )

    assert db_session.query(Violation).filter_by(call_id="CALL-PIPE-SPLIT").count() == 2
    assert result.score == 70


def test_reprocess_with_grouped_findings_is_still_idempotent(db_session, scratch_audio, monkeypatch):
    result = _run_pipeline(
        db_session, scratch_audio, monkeypatch, "CALL-PIPE-SPLIT2",
        _compliance(_r3(0, "police bhej dunga"), _r3(1, "jail jaana padega")),
    )
    again = pipeline.process_call("CALL-PIPE-SPLIT2", db_session)

    assert db_session.query(Violation).filter_by(call_id="CALL-PIPE-SPLIT2").count() == 2
    assert again.score == result.score == 70


# --- The analysis call itself (what the pipeline hands it, and what happens when it fails) ----------------

def _unified_setup(monkeypatch, labelled, compliance):
    from app.services import unified_analysis_service

    seen = {}

    def fake_analyse(raw, call, sleep_fn=None, include_examples=True):
        seen["call"] = call
        return labelled, compliance

    monkeypatch.setattr(unified_analysis_service, "analyse_call", fake_analyse)
    return seen


def _unified_run(db_session, scratch_audio, call_id, **call_kwargs):
    call = make_queued_call(db_session, call_id=call_id, **call_kwargs)
    write_fake_audio(scratch_audio, "pipe.wav")
    return pipeline.process_call(call.id, db_session)


def test_the_pipeline_makes_one_analysis_call_with_the_transcript_and_the_call(db_session, scratch_audio, monkeypatch):
    mock_transcription(monkeypatch, raw_segments())
    seen = _unified_setup(monkeypatch, _threat_segments(), _compliance(_r3(0, "police bhej dunga")))

    result = _unified_run(db_session, scratch_audio, "CALL-UNI-01")

    assert result.status == "ready" and result.score == 70
    assert seen["call"].id == "CALL-UNI-01"  # metadata is passed to the analysis
    assert db_session.query(TranscriptSegment).filter_by(call_id="CALL-UNI-01").count() == 3


def test_r1_comes_from_call_metadata_not_the_model(db_session, scratch_audio, monkeypatch):
    mock_transcription(monkeypatch, raw_segments())
    _unified_setup(monkeypatch, _threat_segments(), _compliance())

    result = _unified_run(db_session, scratch_audio, "CALL-UNI-R1", hour=7)  # 07:00, before 08:00

    rules = {v.rule_code: v for v in db_session.query(Violation).filter_by(call_id="CALL-UNI-R1")}
    assert set(rules) == {"R1"} and rules["R1"].severity == "high" and rules["R1"].segment_id is None
    assert result.score == 85


def test_unified_mode_holds_findings_the_analysis_is_unsure_about(db_session, scratch_audio, monkeypatch):
    mock_transcription(monkeypatch, raw_segments())
    unsure = llm_service.RawViolation(rule_code="R2", segment_seq=1, quote="jail jaana padega",
                                      explanation="possibly abusive", confidence=0.6, needs_review=True)
    _unified_setup(monkeypatch, _threat_segments(), _compliance(unsure))

    result = _unified_run(db_session, scratch_audio, "CALL-UNI-HOLD")

    v = db_session.query(Violation).filter_by(call_id="CALL-UNI-HOLD").one()
    assert v.needs_speaker_review is True and "FLAGGED FOR REVIEW" in v.explanation
    assert v.quote == "jail jaana padega"  # evidence kept
    assert result.score == 100  # pending + uncertain -> no automatic deduction


def test_unified_mode_scores_adjacent_fragments_once_and_distinct_rules_separately(db_session, scratch_audio, monkeypatch):
    mock_transcription(monkeypatch, raw_segments())
    r4 = llm_service.RawViolation(rule_code="R4", segment_seq=1, quote="jail jaana padega", explanation="x", confidence=0.8)
    _unified_setup(monkeypatch, _threat_segments(), _compliance(_r3(0, "police bhej dunga"), _r3(1, "jail jaana padega"), r4))

    result = _unified_run(db_session, scratch_audio, "CALL-UNI-GROUP")

    assert db_session.query(Violation).filter_by(call_id="CALL-UNI-GROUP").count() == 3
    assert result.score == 100 - 30 - 30  # R3 fragments once, R4 independently


def test_an_analysis_failure_marks_the_call_failed_with_no_fallback(db_session, scratch_audio, monkeypatch):
    from app.services import unified_analysis_service

    mock_transcription(monkeypatch, raw_segments())

    def boom(raw, call, sleep_fn=None, include_examples=True):
        raise llm_service.LLMResponseValidationError("Unified analysis response is missing the 'segments' list.")

    monkeypatch.setattr(unified_analysis_service, "analyse_call", boom)

    result = _unified_run(db_session, scratch_audio, "CALL-UNI-FAIL")
    assert result.status == "failed" and "segments" in result.failure_reason


def test_there_is_no_analysis_mode_to_switch(db_session, scratch_audio, monkeypatch):
    """One analysis path only: no setting selects another, and nothing else is callable from the pipeline."""
    mock_transcription(monkeypatch, raw_segments())
    mock_speaker_labelling(monkeypatch, labelled_segments())
    mock_compliance(monkeypatch, compliance_with_one_violation())

    assert not hasattr(settings, "analysis_mode")
    assert not hasattr(llm_service, "label_speakers") and not hasattr(llm_service, "analyse_compliance")
    assert _unified_run(db_session, scratch_audio, "CALL-UNI-ONLY").score == 70


# --- Phase 1: speaker-attribution uncertainty path -------------------------

def test_low_confidence_agent_violation_flagged_and_not_auto_deducted(db_session, scratch_audio, monkeypatch):
    call = make_queued_call(db_session, call_id="CALL-PIPE-LOWCONF")
    write_fake_audio(scratch_audio, "pipe.wav")
    mock_transcription(monkeypatch, raw_segments())
    # Segment 1 (the one the violation cites) is agent-labelled but with
    # low model confidence.
    low_conf_labelled = [
        llm_service.LabelledSegment(seq=0, speaker="agent", start=0.0, end=2.0, text="Namaste main Pooja bol rahi hoon", confidence=0.95),
        llm_service.LabelledSegment(seq=1, speaker="agent", start=2.0, end=4.0, text="hum aapke ghar bhi aayenge", confidence=0.3),
        llm_service.LabelledSegment(seq=2, speaker="borrower", start=4.0, end=6.0, text="theek hai dhanyavaad", confidence=0.95),
    ]
    mock_speaker_labelling(monkeypatch, low_conf_labelled)
    mock_compliance(monkeypatch, compliance_with_one_violation())

    result = pipeline.process_call(call.id, db_session)

    violation = db_session.query(Violation).filter_by(call_id=call.id).one()
    assert violation.needs_speaker_review is True
    assert "LOW-CONFIDENCE SPEAKER ATTRIBUTION" in violation.explanation
    assert violation.quote == "hum aapke ghar bhi aayenge"  # evidence retained, not discarded
    assert violation.review.decision == "pending"

    # Not auto-deducted while pending and uncertain: still 100, not 70.
    assert result.score == 100


def test_missing_confidence_is_also_treated_as_uncertain(db_session, scratch_audio, monkeypatch):
    call = make_queued_call(db_session, call_id="CALL-PIPE-NOCONF")
    write_fake_audio(scratch_audio, "pipe.wav")
    mock_transcription(monkeypatch, raw_segments())
    no_conf_labelled = [
        llm_service.LabelledSegment(seq=0, speaker="agent", start=0.0, end=2.0, text="Namaste main Pooja bol rahi hoon"),
        llm_service.LabelledSegment(seq=1, speaker="agent", start=2.0, end=4.0, text="hum aapke ghar bhi aayenge"),  # confidence defaults to None
        llm_service.LabelledSegment(seq=2, speaker="borrower", start=4.0, end=6.0, text="theek hai dhanyavaad"),
    ]
    mock_speaker_labelling(monkeypatch, no_conf_labelled)
    mock_compliance(monkeypatch, compliance_with_one_violation())

    result = pipeline.process_call(call.id, db_session)

    violation = db_session.query(Violation).filter_by(call_id=call.id).one()
    assert violation.needs_speaker_review is True  # missing confidence is never "high"
    assert result.score == 100


def test_non_agent_cited_violation_also_sets_needs_speaker_review(db_session, scratch_audio, monkeypatch):
    call = make_queued_call(db_session, call_id="CALL-PIPE-NONAGENT")
    write_fake_audio(scratch_audio, "pipe.wav")
    mock_transcription(monkeypatch, raw_segments())
    # Segment 1 is confidently labelled, but as 'borrower' — the rule
    # requires agent misconduct.
    borrower_cited = [
        llm_service.LabelledSegment(seq=0, speaker="agent", start=0.0, end=2.0, text="Namaste main Pooja bol rahi hoon", confidence=0.95),
        llm_service.LabelledSegment(seq=1, speaker="borrower", start=2.0, end=4.0, text="hum aapke ghar bhi aayenge", confidence=0.95),
        llm_service.LabelledSegment(seq=2, speaker="borrower", start=4.0, end=6.0, text="theek hai dhanyavaad", confidence=0.95),
    ]
    mock_speaker_labelling(monkeypatch, borrower_cited)
    mock_compliance(monkeypatch, compliance_with_one_violation())

    result = pipeline.process_call(call.id, db_session)

    violation = db_session.query(Violation).filter_by(call_id=call.id).one()
    assert violation.needs_speaker_review is True
    assert "SPEAKER ATTRIBUTION FLAG" in violation.explanation
    assert result.score == 100  # not auto-deducted while pending and uncertain


def test_manager_confirming_an_uncertain_violation_deducts_normally(db_session, scratch_audio, monkeypatch):
    call = make_queued_call(db_session, call_id="CALL-PIPE-CONFIRMUNCERTAIN")
    write_fake_audio(scratch_audio, "pipe.wav")
    mock_transcription(monkeypatch, raw_segments())
    low_conf_labelled = [
        llm_service.LabelledSegment(seq=0, speaker="agent", start=0.0, end=2.0, text="Namaste main Pooja bol rahi hoon", confidence=0.95),
        llm_service.LabelledSegment(seq=1, speaker="agent", start=2.0, end=4.0, text="hum aapke ghar bhi aayenge", confidence=0.1),
        llm_service.LabelledSegment(seq=2, speaker="borrower", start=4.0, end=6.0, text="theek hai dhanyavaad", confidence=0.95),
    ]
    mock_speaker_labelling(monkeypatch, low_conf_labelled)
    mock_compliance(monkeypatch, compliance_with_one_violation())
    pipeline.process_call(call.id, db_session)

    violation = db_session.query(Violation).filter_by(call_id=call.id).one()
    assert violation.needs_speaker_review is True

    # A manager checks the audio and confirms it really was the agent.
    violation.review.decision = "confirmed"
    db_session.commit()
    from app.services import scoring_service
    new_score = scoring_service.compute_score(call.id, db_session)
    assert new_score == 70  # 100 - 30 (critical), deducted like any confirmed violation
