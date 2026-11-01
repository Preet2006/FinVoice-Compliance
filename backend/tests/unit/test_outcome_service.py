from types import SimpleNamespace

import pytest

from app.services import outcome_service as oc


def call(status="ready", has_audio=True, failure_reason=None):
    return SimpleNamespace(id="C", status=status, has_audio=has_audio, failure_reason=failure_reason)


_n = {"i": 0}


def finding(rule="R3", seq=1, decision="pending", held=False, unverified=False, severity="critical"):
    _n["i"] += 1
    seg = SimpleNamespace(seq=seq, speaker="agent", speaker_confidence=0.95,
                          start_time=seq * 10.0, end_time=seq * 10.0 + 2)
    return SimpleNamespace(
        id=f"v{_n['i']}", call_id="C", rule_code=rule, segment_id=f"s{seq}", segment=seg, severity=severity,
        needs_speaker_review=held, evidence_status="unverified" if unverified else "verified",
        review=SimpleNamespace(decision=decision),
    )


def outcome(c, violations=(), has_results=True):
    return oc.compute_outcome(c, violations, has_results)


# --- not analysed / failed ------------------------------------------------------


@pytest.mark.parametrize("status", ["metadata_only", "queued", "transcribing", "analyzing"])
def test_calls_that_have_not_been_analysed_are_never_clean(status):
    assert outcome(call(status=status), has_results=False).outcome == oc.NOT_ANALYZED


def test_call_without_audio_is_not_analysed():
    assert outcome(call(has_audio=False, status="ready")).outcome == oc.NOT_ANALYZED


def test_failed_with_no_results_is_failed_not_clean():
    result = outcome(call(status="failed", failure_reason="provider down"), has_results=False)
    assert result.outcome == oc.FAILED and result.last_reprocess_failed is False


def test_a_call_marked_ready_with_no_transcript_is_incomplete_not_clean():
    assert outcome(call(), has_results=False).outcome == oc.INCOMPLETE


# --- the five analysed outcomes ----------------------------------------------------


def test_genuinely_clean_call():
    result = outcome(call())
    assert result == oc.AnalysisOutcome(oc.CLEAN, review_status="pending_review")  # analysed, but not yet signed off
    assert result.provisional_score is False


def test_violations_found_when_a_finding_counts():
    result = outcome(call(), [finding()])
    assert result.outcome == oc.VIOLATIONS_FOUND and result.counted_violations == 1


def test_pending_review_when_only_held_findings_remain():
    result = outcome(call(), [finding(held=True)])
    assert result.outcome == oc.PENDING_REVIEW
    assert result.findings_pending_review == 1 and result.counted_violations == 0 and result.provisional_score


def test_incomplete_when_unverified_evidence_is_unresolved():
    result = outcome(call(), [finding(unverified=True)])
    assert result.outcome == oc.INCOMPLETE and result.findings_unverified == 1 and result.provisional_score
    assert result.counted_violations == 0  # an unverified finding never counts while pending


# --- precedence -----------------------------------------------------------------------


def test_incomplete_outranks_violations_found_and_counts_show_both():
    result = outcome(call(), [finding(seq=1), finding(seq=5, unverified=True)])
    assert result.outcome == oc.INCOMPLETE
    assert (result.counted_violations, result.findings_unverified) == (1, 1)


def test_violations_found_outranks_pending_review():
    result = outcome(call(), [finding(seq=1), finding(seq=5, held=True)])
    assert result.outcome == oc.VIOLATIONS_FOUND
    assert (result.counted_violations, result.findings_pending_review) == (1, 1) and result.provisional_score


def test_incomplete_outranks_pending_review():
    assert outcome(call(), [finding(held=True), finding(seq=5, unverified=True)]).outcome == oc.INCOMPLETE


# --- resolution by review ---------------------------------------------------------------


def test_dismissed_unverified_evidence_no_longer_makes_the_call_incomplete():
    result = outcome(call(), [finding(unverified=True, decision="false_alarm")])
    assert result.outcome == oc.CLEAN and result.findings_unverified == 0 and result.provisional_score is False


def test_confirmed_unverified_evidence_is_resolved_and_then_counts():
    result = outcome(call(), [finding(unverified=True, decision="confirmed")])
    assert result.outcome == oc.VIOLATIONS_FOUND and result.counted_violations == 1 and result.findings_unverified == 0


def test_all_findings_dismissed_is_clean():
    assert outcome(call(), [finding(decision="false_alarm"), finding(seq=5, held=True, decision="false_alarm")]).outcome == oc.CLEAN


def test_confirming_a_held_finding_moves_it_from_pending_to_counted():
    assert outcome(call(), [finding(held=True)]).outcome == oc.PENDING_REVIEW
    assert outcome(call(), [finding(held=True, decision="confirmed")]).outcome == oc.VIOLATIONS_FOUND


def test_an_unverified_and_held_finding_is_counted_once_not_twice():
    result = outcome(call(), [finding(held=True, unverified=True)])
    assert (result.findings_unverified, result.findings_pending_review) == (1, 0)


# --- failed reprocess keeps the previous outcome -------------------------------------------


NOTE = "Reprocessing failed; the previous results were kept. The analysis provider timed out."


def test_failed_reprocess_with_clean_earlier_results_stays_clean_but_is_flagged():
    result = outcome(call(failure_reason=NOTE))
    assert result.outcome == oc.CLEAN and result.last_reprocess_failed is True


def test_failed_reprocess_does_not_hide_earlier_violations():
    result = outcome(call(failure_reason=NOTE), [finding()])
    assert result.outcome == oc.VIOLATIONS_FOUND and result.last_reprocess_failed is True


def test_failed_status_that_still_has_results_uses_them_and_is_flagged():
    result = outcome(call(status="failed", failure_reason=NOTE), [finding(unverified=True)], has_results=True)
    assert result.outcome == oc.INCOMPLETE and result.last_reprocess_failed is True


def test_a_successful_run_reports_no_failure():
    assert outcome(call(failure_reason=None)).last_reprocess_failed is False


# --- scoring is unaffected by outcome ---------------------------------------------------------


def test_outcome_does_not_alter_what_counts_toward_the_score():
    from app.services import scoring_service

    rows = [finding(seq=1), finding(seq=5, unverified=True), finding(seq=9, held=True)]
    statuses = scoring_service.evaluate_violations(rows)
    assert [statuses[r.id].counts_toward_score for r in rows] == [True, False, False]
