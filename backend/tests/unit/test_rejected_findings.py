"""
Findings whose evidence fails validation are recorded (with a kind and a
reason), never silently dropped.
"""

from app.services import llm_service as llm
from tests.unit.test_unified_analysis import finding, response, validate


def kinds(result):
    return [r["kind"] for r in result.rejected_findings]


def test_unified_invalid_evidence_rejections_are_classified_as_such():
    data = response(
        ["agent"],
        violations=[finding("R3", 0, "burn your house"), finding("R5", 7, "otp"), {**finding("R2", 0, "x"), "quote": ""}],
    )
    _, result = validate(data, "please pay tomorrow")
    assert kinds(result) == [llm.KIND_INVALID_EVIDENCE] * 3


def test_unified_policy_and_duplicate_rejections_are_not_invalid_evidence():
    data = response(
        ["agent"],
        violations=[finding("R1", 0, "hi"), finding("R6", 0, "hi"), finding("R9", 0, "hi"),
                    finding("R3", 0, "police"), finding("R3", 0, "police")],
    )
    _, result = validate(data, "send police")
    assert sorted(kinds(result)) == sorted([llm.KIND_POLICY] * 3 + [llm.KIND_DUPLICATE])


def test_rule_reported_violated_with_no_finding_is_kept_as_a_finding_without_a_segment():
    data = response(["agent"], outcomes={"R5": {"violated": True, "uncertain": False, "note": "asked for an OTP"}})
    _, result = validate(data, "hello there")

    [kept] = [r for r in result.rejected_findings if r["kind"] == llm.KIND_INVALID_EVIDENCE]
    assert kept["entry"]["rule_code"] == "R5"
    assert kept["entry"]["segment_index"] is None and kept["entry"]["quote"] is None
    assert "no verifiable evidence" in kept["reason"] and kept["entry"]["explanation"] == "asked for an OTP"
    assert not any(v.rule_code == "R5" for v in result.violations)  # never counted as a finding


def test_no_second_record_when_the_asserted_rules_finding_was_already_rejected():
    data = response(["agent"], violations=[finding("R3", 0, "burn your house")],
                    outcomes={"R3": {"violated": True, "uncertain": False, "note": ""}})
    _, result = validate(data, "please pay tomorrow")
    assert kinds(result).count(llm.KIND_INVALID_EVIDENCE) == 1


def test_no_record_when_the_asserted_rule_has_an_accepted_finding():
    data = response(["agent"], violations=[finding("R3", 0, "police")],
                    outcomes={"R3": {"violated": True, "uncertain": False, "note": ""}})
    _, result = validate(data, "send police")
    assert result.rejected_findings == []


def test_a_rule_the_model_says_is_not_violated_creates_no_record():
    _, result = validate(response(["agent"]), "hello")
    assert result.rejected_findings == []


def test_the_model_cannot_assert_r1_or_r6_into_existence_through_rule_outcomes():
    data = response(["agent"], outcomes={"R6": {"violated": True, "uncertain": False, "note": ""}})
    _, result = validate(data, "hello")
    assert not [r for r in result.rejected_findings if r["kind"] == llm.KIND_INVALID_EVIDENCE]
