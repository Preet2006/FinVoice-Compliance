"""
I-05: reviewer work must never be lost to a reprocess.

* A reviewer write (speaker correction, manual finding, review decision, sign-off) is refused
  with 409 while the call is being processed.
* The replacement step looks for reviewer work again, under the write lock, in the transaction
  that deletes the old results, and aborts instead of erasing it.
* Two-thread tests run the real pipeline against a file database to exercise the ordering.
"""

import json
import threading
import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import AppUser, Call, Review, ReviewerAudit, TranscriptSegment, Violation
from app.services import pipeline, reviewer_service, scoring_service, transcription_service, unified_analysis_service
from tests.conftest import _seed_fixture, MGR1_HEADERS
from tests.integration.test_pipeline import (  # noqa: F401  (scratch_audio is a fixture)
    compliance_with_one_violation,
    labelled_segments,
    make_queued_call,
    mock_compliance,
    mock_speaker_labelling,
    mock_transcription,
    raw_segments,
    scratch_audio,
    write_fake_audio,
)

CALL = "CALL-RACE-1"


def analyse(db, scratch_audio, monkeypatch, call_id=CALL):
    make_queued_call(db, call_id=call_id)
    write_fake_audio(scratch_audio, "pipe.wav")
    mock_transcription(monkeypatch, raw_segments())
    mock_speaker_labelling(monkeypatch, labelled_segments())
    mock_compliance(monkeypatch, compliance_with_one_violation())
    result = pipeline.process_call(call_id, db)
    assert result.status == "ready"
    return result


def state(db, call_id=CALL):
    db.expire_all()
    return {
        "segments": [(s.id, s.text, s.speaker, s.speaker_override) for s in db.query(TranscriptSegment).filter_by(call_id=call_id).order_by(TranscriptSegment.seq)],
        "violations": [(v.id, v.source, v.rule_code, v.quote) for v in db.query(Violation).filter_by(call_id=call_id).order_by(Violation.id)],
        "reviews": [(r.violation_id, r.decision, r.comment) for r in db.query(Review).join(Violation, Review.violation_id == Violation.id).filter(Violation.call_id == call_id).order_by(Review.violation_id)],
        "audit": [(a.id, a.action, a.target_id, a.detail) for a in db.query(ReviewerAudit).filter_by(call_id=call_id).order_by(ReviewerAudit.id)],
        "score": db.get(Call, call_id).score,
    }


def reviewer_override(db, call_id=CALL, who="borrower"):
    """Reviewer work written straight to the database, as if committed by a request that slipped past."""
    seg = db.query(TranscriptSegment).filter_by(call_id=call_id).order_by(TranscriptSegment.seq).first()
    seg.speaker_override, seg.speaker_override_by = who, "u-mgr1"
    db.add(ReviewerAudit(call_id=call_id, action="speaker_correction", target_id=seg.id, actor_id="u-mgr1", detail=json.dumps({"new_speaker": who})))
    db.commit()
    return seg.id


def refuses_everything(client, db):
    seg = db.query(TranscriptSegment).filter_by(call_id=CALL, speaker="agent").order_by(TranscriptSegment.seq).first()
    vid = db.query(Violation).filter_by(call_id=CALL).first().id
    responses = {
        "speaker": client.post(f"/api/calls/{CALL}/segments/{seg.id}/speaker", headers=MGR1_HEADERS, json={"speaker": "borrower"}),
        "manual finding": client.post(f"/api/calls/{CALL}/violations", headers=MGR1_HEADERS, json={"rule_code": "R2", "segment_id": seg.id, "word_start": 0, "word_end": 0, "comment": "c"}),
        "review decision": client.post(f"/api/violations/{vid}/review", headers=MGR1_HEADERS, json={"decision": "false_alarm", "comment": "c"}),
        "sign-off": client.post(f"/api/calls/{CALL}/review/complete", headers=MGR1_HEADERS),
    }
    return responses


# ------------------------------------------------------------------ 409 while processing
@pytest.mark.parametrize("status", ["transcribing", "analyzing"])
def test_every_reviewer_write_is_refused_while_the_call_is_being_processed(client, db_session, scratch_audio, monkeypatch, status):
    analyse(db_session, scratch_audio, monkeypatch)
    db_session.get(Call, CALL).status = status
    db_session.commit()
    before = state(db_session)

    for name, r in refuses_everything(client, db_session).items():
        assert r.status_code == 409, (name, r.status_code, r.text)
        assert r.json()["error"]["code"] == "CALL_BEING_PROCESSED", name
        assert "being re-analysed" in r.json()["error"]["message"] and "try again" in r.json()["error"]["message"]

    assert state(db_session) == before  # not one row written: no override, finding, decision, audit entry or sign-off
    assert db_session.get(Call, CALL).review_completed_at is None


def test_the_same_actions_work_once_processing_has_finished(client, db_session, scratch_audio, monkeypatch):
    analyse(db_session, scratch_audio, monkeypatch)
    seg = db_session.query(TranscriptSegment).filter_by(call_id=CALL, speaker="agent").first()
    assert client.post(f"/api/calls/{CALL}/segments/{seg.id}/speaker", headers=MGR1_HEADERS, json={"speaker": "borrower"}).status_code == 200
    vid = db_session.query(Violation).filter_by(call_id=CALL).first().id
    assert client.post(f"/api/violations/{vid}/review", headers=MGR1_HEADERS, json={"decision": "false_alarm", "comment": "c"}).status_code == 200
    assert client.post(f"/api/calls/{CALL}/review/complete", headers=MGR1_HEADERS).status_code == 200


def test_a_failed_call_that_keeps_its_earlier_results_can_still_be_reviewed(client, db_session, scratch_audio, monkeypatch):
    analyse(db_session, scratch_audio, monkeypatch)
    db_session.get(Call, CALL).status = "failed"  # e.g. a failed later attempt on a call that has results
    db_session.commit()
    seg = db_session.query(TranscriptSegment).filter_by(call_id=CALL, speaker="agent").first()
    assert client.post(f"/api/calls/{CALL}/segments/{seg.id}/speaker", headers=MGR1_HEADERS, json={"speaker": "borrower"}).status_code == 200


def test_the_service_level_guard_and_the_pipeline_agree_on_which_statuses_mean_processing():
    assert set(reviewer_service.PROCESSING_STATUSES) == set(pipeline.IN_PROGRESS_STATUSES)


# ------------------------------------------------------------------ replacement never erases reviewer work
def test_replacement_aborts_instead_of_erasing_reviewer_work(db_session, scratch_audio, monkeypatch):
    call = analyse(db_session, scratch_audio, monkeypatch)
    reviewer_override(db_session)
    before = state(db_session)
    with pytest.raises(pipeline.ReviewerWorkExistsError):
        pipeline._replace_results(db_session, call, CALL, None, labelled_segments(), compliance_with_one_violation())
    db_session.rollback()
    assert state(db_session) == before  # segments, overrides, findings, reviews and audit all untouched


def test_work_that_lands_during_the_analysis_is_kept_and_the_new_results_discarded(db_session, scratch_audio, monkeypatch):
    analyse(db_session, scratch_audio, monkeypatch)

    def slipping(audio_bytes, filename, sleep_fn=None):
        reviewer_override(db_session)  # committed while the provider call is in flight
        return raw_segments()

    monkeypatch.setattr(transcription_service, "transcribe", slipping)
    before_text = [s[1] for s in state(db_session)["segments"]]
    result = pipeline.process_call(CALL, db_session)

    after = state(db_session)
    assert result.status == "ready" and result.failure_reason.startswith("Reprocessing failed; the previous results were kept.")
    assert "Reviewer work was added while the analysis was running" in result.failure_reason
    assert after["segments"][0][3] == "borrower" and [s[1] for s in after["segments"]] == before_text  # override and old transcript both kept
    assert len(after["audit"]) == 1 and after["audit"][0][1] == "speaker_correction"
    assert CALL not in pipeline._ACTIVE_CALLS


def test_work_committed_just_before_the_status_change_is_caught_before_any_provider_call(db_session, scratch_audio, monkeypatch):
    analyse(db_session, scratch_audio, monkeypatch)
    real, seen = reviewer_service.reviewer_work, {"n": 0}

    def sneaky(db, call_id):
        seen["n"] += 1
        if seen["n"] == 2:  # the check made right after the call is claimed
            reviewer_override(db_session)
        return real(db, call_id)

    monkeypatch.setattr(reviewer_service, "reviewer_work", sneaky)
    monkeypatch.setattr(transcription_service, "transcribe", lambda *a, **k: (_ for _ in ()).throw(AssertionError("provider reached")))
    with pytest.raises(pipeline.ReviewerWorkExistsError):
        pipeline.process_call(CALL, db_session)
    assert db_session.get(Call, CALL).status == "ready"  # claimed, then released again
    assert state(db_session)["segments"][0][3] == "borrower"


def test_an_abandoned_run_on_a_call_with_reviewer_work_is_unstuck_so_the_reviewer_is_not_locked_out(client, db_session, scratch_audio, monkeypatch):
    analyse(db_session, scratch_audio, monkeypatch)
    reviewer_override(db_session)
    db_session.get(Call, CALL).status = "analyzing"  # a worker died mid-run; nothing is running now
    db_session.commit()
    with pytest.raises(pipeline.ReviewerWorkExistsError):
        pipeline.process_call(CALL, db_session)
    assert db_session.get(Call, CALL).status == "ready"
    seg = db_session.query(TranscriptSegment).filter_by(call_id=CALL, speaker="agent").first()
    assert client.post(f"/api/calls/{CALL}/segments/{seg.id}/speaker", headers=MGR1_HEADERS, json={"speaker": "agent"}).status_code == 200


def test_existing_audit_and_review_history_survive_an_aborted_replacement(client, db_session, scratch_audio, monkeypatch):
    analyse(db_session, scratch_audio, monkeypatch)
    seg = db_session.query(TranscriptSegment).filter_by(call_id=CALL, speaker="agent").first()
    vid = db_session.query(Violation).filter_by(call_id=CALL).first().id
    client.post(f"/api/calls/{CALL}/segments/{seg.id}/speaker", headers=MGR1_HEADERS, json={"speaker": "borrower", "comment": "heard it"})
    client.post(f"/api/violations/{vid}/review", headers=MGR1_HEADERS, json={"decision": "confirmed", "comment": "yes"})
    before = state(db_session)
    assert len(before["audit"]) == 1 and before["reviews"][0][1] == "confirmed"
    with pytest.raises(pipeline.ReviewerWorkExistsError):
        pipeline.process_call(CALL, db_session)  # blocked at the door
    assert state(db_session) == before


# ------------------------------------------------------------------ real concurrency (two threads, file database)
@pytest.fixture()
def file_db(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'race.db').as_posix()}", connect_args={"check_same_thread": False, "timeout": 20})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    s = Session()
    _seed_fixture(s)
    s.close()
    yield Session
    engine.dispose()


def reprocess(Session):
    db = Session()
    try:
        return pipeline.process_call(CALL, db).status
    finally:
        db.close()


def run_in_thread(fn):
    box = {}

    def target():
        try:
            box["result"] = fn()
        except BaseException as exc:  # noqa: BLE001 - reported to the test
            box["error"] = exc

    t = threading.Thread(target=target)
    t.start()
    return t, box


def test_a_reviewer_is_refused_while_a_real_run_is_in_flight_then_succeeds(file_db, scratch_audio, monkeypatch):
    Session = file_db
    main = Session()
    analyse(main, scratch_audio, monkeypatch)
    started, release = threading.Event(), threading.Event()

    def blocking(audio_bytes, filename, sleep_fn=None):
        started.set()
        assert release.wait(20)
        return raw_segments()

    monkeypatch.setattr(transcription_service, "transcribe", blocking)
    worker, box = run_in_thread(lambda: reprocess(Session))
    try:
        assert started.wait(20)
        reviewer = Session()
        actor, call = reviewer.get(AppUser, "u-mgr1"), reviewer.get(Call, CALL)
        seg_id = reviewer.query(TranscriptSegment).filter_by(call_id=CALL).first().id
        with pytest.raises(reviewer_service.ReviewerActionError) as refused:
            reviewer_service.correct_speaker(reviewer, call, seg_id, "borrower", None, actor)
        assert refused.value.status == 409 and refused.value.code == "CALL_BEING_PROCESSED"
        reviewer.rollback()
    finally:
        release.set()
        worker.join(30)
    assert "error" not in box and box["result"] == "ready"

    reviewer = Session()  # afterwards the very same action works
    reviewer_service.correct_speaker(reviewer, reviewer.get(Call, CALL), seg_id, "borrower", None, reviewer.get(AppUser, "u-mgr1"))
    fresh = Session()
    assert fresh.get(TranscriptSegment, seg_id).speaker_override == "borrower"
    assert fresh.query(ReviewerAudit).filter_by(call_id=CALL).count() == 1


def test_reviewer_work_committed_while_a_run_waits_for_the_write_lock_is_not_lost(file_db, scratch_audio, monkeypatch):
    Session = file_db
    main = Session()
    analyse(main, scratch_audio, monkeypatch)
    monkeypatch.setattr(transcription_service, "transcribe", lambda *a, **k: (_ for _ in ()).throw(AssertionError("provider reached")))

    reviewer = Session()
    call = reviewer.get(Call, CALL)
    reviewer_service.guard_not_processing(reviewer, call)  # the reviewer now holds the write lock...
    seg = reviewer.query(TranscriptSegment).filter_by(call_id=CALL).first()
    seg.speaker_override, seg.speaker_override_by = "borrower", "u-mgr1"
    reviewer.flush()  # ...with a correction written but not yet committed

    worker, box = run_in_thread(lambda: reprocess(Session))
    time.sleep(1.0)
    assert worker.is_alive()  # the reprocess is waiting behind the reviewer, not racing past
    reviewer.commit()
    worker.join(30)

    assert isinstance(box.get("error"), pipeline.ReviewerWorkExistsError)  # it saw the committed work and stepped aside
    fresh = Session()
    assert fresh.get(TranscriptSegment, seg.id).speaker_override == "borrower"
    assert fresh.get(Call, CALL).status == "ready" and CALL not in pipeline._ACTIVE_CALLS
    assert [s.id for s in fresh.query(TranscriptSegment).filter_by(call_id=CALL)] == [s.id for s in main.query(TranscriptSegment).filter_by(call_id=CALL)]


def test_the_guard_itself_takes_the_write_lock_so_a_run_cannot_claim_the_call_in_between(file_db, scratch_audio, monkeypatch):
    Session = file_db
    main = Session()
    analyse(main, scratch_audio, monkeypatch)
    monkeypatch.setattr(transcription_service, "transcribe", lambda *a, **k: (_ for _ in ()).throw(AssertionError("provider reached")))

    reviewer = Session()
    reviewer_service.guard_not_processing(reviewer, reviewer.get(Call, CALL))  # passed the check; nothing else written yet
    worker, box = run_in_thread(lambda: reprocess(Session))
    time.sleep(1.0)
    assert worker.is_alive()  # the guard's own lock is what holds the run back
    reviewer.rollback()
    worker.join(30)
    assert not worker.is_alive()


def test_replacement_waits_for_a_writer_that_bypassed_the_guard_and_then_sees_its_work(file_db, scratch_audio, monkeypatch):
    """Defence in depth: even a write that did not go through the guard (so it started during the analysis)
    cannot be deleted unseen, because the replacement takes the lock before it looks."""
    Session = file_db
    main = Session()
    analyse(main, scratch_audio, monkeypatch)
    writer = Session()

    def analyse_after_the_status_write(raw, call, **kw):
        # runs after the run's own "analyzing" status write, so the run is NOT already queued behind the writer
        writer.query(Call).filter(Call.id == CALL).update({Call.status: Call.status}, synchronize_session=False)  # takes the lock
        seg = writer.query(TranscriptSegment).filter_by(call_id=CALL).first()
        seg.speaker_override, seg.speaker_override_by = "borrower", "u-mgr1"
        writer.flush()  # written, not yet committed
        threading.Timer(1.0, writer.commit).start()  # commits while the replacement is waiting
        return labelled_segments(), compliance_with_one_violation()

    monkeypatch.setattr(unified_analysis_service, "analyse_call", analyse_after_the_status_write)
    result = reprocess_result(Session)
    time.sleep(1.5)
    fresh = Session()
    assert result == ("ready", True)  # the run stepped aside and reported why
    assert fresh.query(TranscriptSegment).filter_by(call_id=CALL, speaker_override="borrower").count() == 1  # the override survived
    assert "Reviewer work was added" in fresh.get(Call, CALL).failure_reason


def reprocess_result(Session):
    db = Session()
    try:
        call = pipeline.process_call(CALL, db)
        return call.status, bool(call.failure_reason and call.failure_reason.startswith("Reprocessing failed"))
    finally:
        db.close()
