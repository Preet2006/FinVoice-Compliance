"""
The shared plumbing under the analysis (app/services/llm_service.py): the structured-JSON chat call with its one
corrective retry, the speaker-confidence rules, and the rejected-finding record. The analysis itself (prompt,
speaker labelling, findings, R6, sentiment) is tested in test_unified_analysis.py and test_unified_outcomes.py.
"""

import json
from types import SimpleNamespace

import pytest

from app.services import llm_service as llm


def no_sleep(_seconds):
    pass


class FakeCompletions:
    def __init__(self, contents):
        self.contents = list(contents)
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        content = self.contents.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class FakeClient:
    def __init__(self, contents):
        self.completions = FakeCompletions(contents)
        self.chat = SimpleNamespace(completions=self.completions)


MESSAGES = [{"role": "user", "content": "x"}]


# --- the structured-JSON call ------------------------------------------------------------------------
def test_a_valid_json_answer_is_returned_after_one_request():
    client = FakeClient([json.dumps({"ok": True})])
    assert llm._chat_json(client, MESSAGES, sleep_fn=no_sleep) == {"ok": True}
    assert client.completions.calls == 1


def test_malformed_json_gets_one_corrective_retry_then_succeeds():
    client = FakeClient(["not json", json.dumps({"ok": True})])
    assert llm._chat_json(client, MESSAGES, sleep_fn=no_sleep) == {"ok": True}
    assert client.completions.calls == 2


def test_malformed_json_twice_is_a_validation_failure_not_a_guess():
    client = FakeClient(["not json", "still not json"])
    with pytest.raises(llm.LLMResponseValidationError):
        llm._chat_json(client, MESSAGES, sleep_fn=no_sleep)
    assert client.completions.calls == 2  # exactly one retry, never a loop


# --- speaker confidence --------------------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [(0.0, 0.0), (0.6, 0.6), (1.0, 1.0), (0, 0.0), (1, 1.0), (0.95, 0.95)])
def test_a_plain_in_range_number_is_a_confidence(raw, expected):
    assert llm._parse_speaker_confidence(raw) == expected


@pytest.mark.parametrize("bad", [None, "high", "0.9", True, False, 1.5, -0.1, float("nan"), float("inf"), [0.9], {"v": 0.9}])
def test_anything_else_is_unknown_never_high(bad):
    assert llm._parse_speaker_confidence(bad) is None
    assert llm.is_uncertain_confidence(llm._parse_speaker_confidence(bad)) is True


@pytest.mark.parametrize("value,uncertain", [(None, True), (0.0, True), (0.59, True), (0.6, False), (0.95, False), (1.0, False)])
def test_the_review_threshold_is_0_6_and_missing_is_always_uncertain(value, uncertain):
    assert llm.is_uncertain_confidence(value) is uncertain


# --- rejected findings --------------------------------------------------------------------------
def test_a_rejection_keeps_the_entry_the_reason_and_the_kind():
    entry = {"rule_code": "R3", "segment_index": 1}
    assert llm.rejection(entry, "why", llm.KIND_INVALID_EVIDENCE) == {"entry": entry, "reason": "why", "kind": "invalid_evidence"}
    assert {llm.KIND_INVALID_EVIDENCE, llm.KIND_POLICY, llm.KIND_DUPLICATE} == {"invalid_evidence", "policy", "duplicate"}


def test_the_single_analysis_is_the_only_analysis_entry_point():
    assert not hasattr(llm, "label_speakers") and not hasattr(llm, "analyse_compliance")
