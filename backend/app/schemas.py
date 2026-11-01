"""
Pydantic response schemas for Phase 3 (metadata + calls + role filtering).
Shapes follow MASTER_IMPLEMENTATION_PLAN §4.4.
"""

from datetime import datetime
from typing import Generic, List, Literal, Optional, TypeVar

from pydantic import BaseModel, Field, StrictInt

T = TypeVar("T")
SpeakerState = Literal["agent", "borrower", "third_party", "unknown", "mixed"]


class OfficeOut(BaseModel):
    id: str
    name: str

    class Config:
        from_attributes = True


class UserOut(BaseModel):
    id: str
    name: str
    role: str
    office_id: str

    class Config:
        from_attributes = True


class CallerRef(BaseModel):
    id: str
    name: str


class OfficeRef(BaseModel):
    id: str
    name: str


class CallListItem(BaseModel):
    call_id: str
    caller: CallerRef
    office: OfficeRef
    start_time: datetime
    duration_seconds: Optional[int]
    language: Optional[str]
    account_type: str
    score: Optional[int]
    status: str
    # What the analysis actually established (computed on read). A bare score
    # of 100 does not mean "clean": check analysis_outcome and the counts.
    analysis_outcome: str = "not_analyzed"
    counted_violations: int = 0
    findings_pending_review: int = 0
    findings_unverified: int = 0
    provisional_score: bool = False
    last_reprocess_failed: bool = False
    # not_applicable | pending_review | reviewed (explicit manager sign-off)
    review_status: str = "not_applicable"
    findings_total: int = 0
    # Highest severity among findings that deduct (the call's risk level); None when none deduct.
    top_severity: Optional[str] = None


class R1CheckOut(BaseModel):
    """What the call's start time says about R1, available even with no
    recording. Not a violation: it is never scored and cannot be reviewed."""

    status: str  # ok | violation | unresolved
    reason: str
    evaluated_start_time: Optional[str] = None
    # 'not_applicable' until the call is analysed; then 'present' / 'none'.
    stored_finding: str = "not_applicable"
    # False means a stored R1 row (or its absence) no longer matches the start time.
    stored_evidence_current: Optional[bool] = None
    needs_refresh: bool = False


class CallDetail(BaseModel):
    call_id: str
    caller: CallerRef
    office: OfficeRef
    start_time: datetime
    duration_seconds: Optional[int]
    language: Optional[str]
    account_type: str
    score: Optional[int]
    sentiment: Optional[str]
    agent_stayed_calm: Optional[bool]
    status: str
    has_audio: bool
    is_ground_truth: bool
    failure_reason: Optional[str]
    # What kind of failure failure_reason describes (rate_limit, auth,
    # not_configured, timeout, unavailable, rejected, ...), or None.
    failure_kind: Optional[str] = None
    r1_check: Optional[R1CheckOut] = None
    # What the analysis actually established (computed on read). A bare score
    # of 100 does not mean "clean": check analysis_outcome and the counts.
    analysis_outcome: str = "not_analyzed"
    counted_violations: int = 0
    findings_pending_review: int = 0
    findings_unverified: int = 0
    provisional_score: bool = False
    last_reprocess_failed: bool = False
    review_status: str = "not_applicable"
    findings_total: int = 0
    review_completed_by: Optional[str] = None
    review_completed_at: Optional[datetime] = None


class TranscriptSegmentOut(BaseModel):
    id: str
    seq: int
    speaker: SpeakerState
    start_time: float
    end_time: float
    text: str
    sentiment: Optional[str]
    # Uncalibrated model self-estimate (0.0-1.0), or None when the labelling
    # model gave no usable value. Not a probability of correctness.
    speaker_confidence: Optional[float] = None
    speaker_uncertain: bool = False
    # `speaker` above is the EFFECTIVE speaker (a reviewer's correction if one
    # exists). `ai_speaker` is always the model's own label.
    ai_speaker: Optional[SpeakerState] = None
    speaker_corrected: bool = False
    corrected_by: Optional[str] = None
    corrected_at: Optional[datetime] = None
    correction_comment: Optional[str] = None

    class Config:
        from_attributes = True


class ReviewOut(BaseModel):
    decision: str
    reviewer: Optional[str]
    comment: Optional[str]
    reviewed_at: Optional[datetime]


class ViolationOut(BaseModel):
    violation_id: str
    rule_code: str
    rule_name: str
    rule_description: Optional[str] = None
    severity: str
    segment_id: Optional[str]
    start_time_seconds: Optional[float]
    quote: str
    explanation: Optional[str]
    confidence: Optional[float]
    # True when the cited segment's speaker attribution is uncertain; while
    # review.decision is 'pending' such a violation is excluded from the score.
    needs_speaker_review: bool = False
    # 'unverified' = the analysis proposed this finding but its evidence
    # failed validation. `quote` then shows stored transcript text (or a
    # placeholder when no segment is usable), never the model's proposal;
    # `evidence_reason` says why and holds the proposed quote.
    evidence_status: str = "verified"
    evidence_reason: Optional[str] = None
    # Whether this row carries a deduction in the call score. Computed by the
    # same function as the score (scoring_service.evaluate_violations); a row
    # can be False because it was dismissed, is awaiting speaker review, or
    # is a fragment of an incident whose deduction another row carries.
    counts_toward_score: bool = True
    # Shared by all rows of one incident (2+ rows); None when standalone.
    incident_group_id: Optional[str] = None
    # Other rules flagged on the same segment worth a manager's attention
    # (R3 <-> R4). Informational: each rule is still scored independently.
    overlapping_rule_codes: List[str] = Field(default_factory=list)
    # 'ai' = produced by the analysis; 'manual' = added by a reviewer.
    source: str = "ai"
    created_by: Optional[str] = None
    # Points this finding takes off the score (0 when it is not counted).
    score_deduction: int = 0
    review: ReviewOut


class Page(BaseModel, Generic[T]):
    items: List[T]
    total: int
    page: int
    page_size: int


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


class UploadAcceptedItem(BaseModel):
    call_id: str
    filename: str
    status: str


class UploadRejectedItem(BaseModel):
    identifier: str
    reason: str
    detail: Optional[str] = None


class UploadBatchResponse(BaseModel):
    batch_id: str
    accepted: List[UploadAcceptedItem]
    rejected: List[UploadRejectedItem]


class BatchStatusItem(BaseModel):
    call_id: str
    status: str
    failure_reason: Optional[str]
    failure_kind: Optional[str] = None


class BatchStatusResponse(BaseModel):
    batch_id: str
    items: List[BatchStatusItem]


class ReprocessResponse(BaseModel):
    call_id: str
    status: str
    score: Optional[int]
    failure_reason: Optional[str]
    failure_kind: Optional[str] = None


class ReviewRequest(BaseModel):
    decision: str  # 'confirmed' | 'false_alarm'
    comment: Optional[str] = None
    # Required (with a comment) to confirm a finding whose evidence is
    # unverified: the reviewer attests they checked the audio themselves.
    evidence_verified: bool = False


class SpeakerCorrectionRequest(BaseModel):
    # Kept as a string so reviewer_service can preserve the existing
    # INVALID_SPEAKER response for unsupported values.
    speaker: str
    comment: Optional[str] = None


class SpeakerCorrectionResponse(BaseModel):
    segment: TranscriptSegmentOut
    call: "ReviewCallRef"


class ManualFindingRequest(BaseModel):
    rule_code: str  # R2..R6 (R1 comes from call metadata)
    segment_id: str
    word_start: StrictInt  # whole numbers only; no coercion from strings or floats
    word_end: StrictInt
    comment: str
    # Optional echo of the selected words; must match the transcript.
    quote: Optional[str] = None


class ManualFindingResponse(BaseModel):
    violation: ViolationOut
    call: "ReviewCallRef"


class ReviewCompleteOut(BaseModel):
    call_id: str
    review_status: str
    reviewed_by: Optional[str]
    reviewed_at: Optional[datetime]
    score: Optional[int]


class AuditEntryOut(BaseModel):
    id: int
    action: str
    target_id: str
    actor: Optional[str]
    detail: dict
    created_at: datetime


class ReviewCallRef(BaseModel):
    call_id: str
    score: Optional[int]


class ReviewResponse(BaseModel):
    violation_id: str
    review: ReviewOut
    call: ReviewCallRef


SpeakerCorrectionResponse.model_rebuild()
ManualFindingResponse.model_rebuild()


class RuleCount(BaseModel):
    rule_code: str
    count: int


class DashboardRow(BaseModel):
    id: str
    name: str
    calls: int
    violations: int
    counted: int
    average_score: Optional[float]


class ReviewOutcomes(BaseModel):
    confirmed: int
    false_alarm: int
    pending: int


class AccuracyOut(BaseModel):
    available: bool
    reason: str


class RuleMetric(BaseModel):
    rule_code: str
    tp: int
    fp: int
    fn: int
    tn: int
    precision: Optional[float]
    recall: Optional[float]
    f1: Optional[float]


class EvaluationMetricsOut(BaseModel):
    available: bool
    label: str
    reason: Optional[str] = None
    calls_scored: int = 0
    per_rule: List[RuleMetric] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)


class DashboardOut(BaseModel):
    scope: str
    total_calls: int
    calls_analyzed: int
    calls_pending: int = 0
    calls_failed: int = 0
    calls_awaiting_review: int
    reviewed_calls: int
    verified_violations: int
    counted_violations: int
    unverified_findings: int
    average_score: Optional[float]
    breaches_by_rule: List[RuleCount]
    breaches_by_caller: List[DashboardRow]
    breaches_by_office: List[DashboardRow]
    top_risky_callers: List[DashboardRow]
    review_outcomes: ReviewOutcomes
    accuracy: AccuracyOut
    evaluation: Optional[EvaluationMetricsOut] = None
