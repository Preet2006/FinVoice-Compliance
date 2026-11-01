// Mirrors backend/app/schemas.py exactly. Only fields the backend actually
// returns as of Phase 3 — nothing here should be invented ahead of the
// backend implementing it (e.g. no `top_severity` yet; that needs
// violation aggregation, which is Phase 7+).

export type Role = 'caller' | 'manager' | 'head'

export interface Office {
  id: string
  name: string
}

export interface User {
  id: string
  name: string
  role: Role
  office_id: string
}

export interface AuthUser extends User {}

export interface CallerRef {
  id: string
  name: string
}

export interface OfficeRef {
  id: string
  name: string
}

export type CallStatus =
  | 'metadata_only'
  | 'queued'
  | 'transcribing'
  | 'analyzing'
  | 'ready'
  | 'failed'

export interface CallListItem {
  call_id: string
  caller: CallerRef
  office: OfficeRef
  start_time: string
  duration_seconds: number | null
  language: string | null
  account_type: 'personal_loan' | 'credit_card'
  score: number | null
  status: CallStatus
  // What the analysis actually established. A bare score of 100 does not mean
  // "clean": read analysis_outcome and the counts.
  analysis_outcome: AnalysisOutcome
  counted_violations: number
  findings_pending_review: number
  findings_unverified: number
  provisional_score: boolean
  last_reprocess_failed: boolean
  // not_applicable until analysed; then pending_review until a manager or head
  // signs the review off. Finishing the AI analysis never makes a call 'reviewed'.
  review_status?: ReviewStatus
  // Findings not dismissed as false alarms.
  findings_total?: number
  // Highest severity among findings that deduct from the score (the call's risk level); null when none deduct.
  top_severity?: 'critical' | 'high' | 'medium' | 'low' | null
}

export type ReviewStatus = 'not_applicable' | 'pending_review' | 'reviewed'

export type AnalysisOutcome =
  | 'not_analyzed'
  | 'failed'
  | 'incomplete'
  | 'violations_found'
  | 'pending_review'
  | 'clean'

export interface CallDetail extends CallListItem {
  sentiment: 'positive' | 'neutral' | 'negative' | null
  agent_stayed_calm: boolean | null
  has_audio: boolean
  is_ground_truth: boolean
  failure_reason: string | null
  failure_kind: FailureKind | null
  review_completed_by?: string | null
  review_completed_at?: string | null
}

// What kind of failure a call's failure_reason describes. Derived by the
// backend from the already-redacted message.
export type FailureKind =
  | 'rate_limit'
  | 'auth'
  | 'not_configured'
  | 'timeout'
  | 'unavailable'
  | 'rejected'
  | 'empty_result'
  | 'audio_missing'
  | 'invalid_response'
  | 'internal'
  | 'other'

export interface ReprocessResponse {
  call_id: string
  status: CallStatus
  score: number | null
  failure_reason: string | null
  failure_kind: FailureKind | null
}

export interface TranscriptSegment {
  id: string
  seq: number
  speaker: 'agent' | 'borrower' | 'third_party' | 'unknown' | 'mixed'
  start_time: number
  end_time: number
  text: string
  sentiment: 'positive' | 'neutral' | 'negative' | null
  // Uncalibrated model self-estimate in [0, 1]; null when unavailable.
  speaker_confidence: number | null
  speaker_uncertain?: boolean
  // `speaker` above is the EFFECTIVE speaker (a reviewer's correction when
  // there is one); `ai_speaker` is always the model's own label.
  ai_speaker?: 'agent' | 'borrower' | 'third_party' | 'unknown' | 'mixed'
  speaker_corrected?: boolean
  corrected_by?: string | null
  corrected_at?: string | null
  correction_comment?: string | null
}

export interface Review {
  decision: 'pending' | 'confirmed' | 'false_alarm'
  reviewer: string | null
  comment: string | null
  reviewed_at: string | null
}

export interface Violation {
  violation_id: string
  rule_code: string
  rule_name: string
  rule_description?: string | null
  severity: 'critical' | 'high' | 'medium' | 'low'
  segment_id: string | null
  start_time_seconds: number | null
  quote: string
  explanation: string | null
  confidence: number | null
  needs_speaker_review: boolean
  // 'unverified' = the analysis proposed this finding but its evidence failed
  // validation. `quote` is then stored transcript text (or a placeholder),
  // never the model's proposal, which is held in evidence_reason.
  evidence_status: 'verified' | 'unverified'
  evidence_reason: string | null
  // Whether this row carries a deduction in the call score. Computed by the
  // backend with the same logic as the score itself — never recomputed here.
  counts_toward_score: boolean
  // Points this finding takes off the score (0 when it is not counted).
  score_deduction?: number
  // Shared by all rows of one incident (2+ rows); null when standalone.
  incident_group_id: string | null
  // Other rules flagged on the same segment worth a manager's attention.
  overlapping_rule_codes: string[]
  // 'manual' = added by a reviewer (created_by); 'ai' = produced by the analysis.
  source?: 'ai' | 'manual'
  created_by?: string | null
  review: Review
}

export interface Page<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

export interface ApiErrorBody {
  error: {
    code: string
    message: string
  }
}

export interface UploadAcceptedItem {
  call_id: string
  filename: string
  status: string
}

export interface UploadRejectedItem {
  identifier: string
  reason: string
  detail: string | null
}

export interface UploadBatchResponse {
  batch_id: string
  accepted: UploadAcceptedItem[]
  rejected: UploadRejectedItem[]
}

export interface ReviewRequest {
  decision: 'confirmed' | 'false_alarm'
  comment?: string
  // Required to confirm a finding whose evidence is unverified: the reviewer
  // attests they listened to the audio themselves.
  evidence_verified?: boolean
}

export interface ReviewResponse {
  violation_id: string
  review: Review
  call: { call_id: string; score: number | null }
}

export interface CallListFilters {
  office_id?: string
  caller_id?: string
  severity?: 'critical' | 'high' | 'medium' | 'low'
  date_from?: string
  date_to?: string
  q?: string
  analysis_status?: 'queued' | 'processing' | 'completed' | 'failed'
  review_status?: 'pending_review' | 'reviewed'
  sort?: 'date_desc' | 'date_asc' | 'score_asc' | 'score_desc' | 'risk'
  page?: number
  page_size?: number
}

export interface SpeakerCorrectionResponse {
  segment: TranscriptSegment
  call: { call_id: string; score: number | null }
}

export interface ManualFindingRequest {
  rule_code: string
  segment_id: string
  word_start: number
  word_end: number
  comment: string
}

export interface ManualFindingResponse {
  violation: Violation
  call: { call_id: string; score: number | null }
}

export interface AuditEntry {
  id: number
  action: 'speaker_correction' | 'manual_finding'
  target_id: string
  actor: string | null
  detail: Record<string, unknown>
  created_at: string
}

export interface DashboardRow {
  id: string
  name: string
  calls: number
  violations: number
  counted: number
  average_score: number | null
}

export interface RuleMetric {
  rule_code: string
  tp: number
  fp: number
  fn: number
  tn: number
  precision: number | null
  recall: number | null
  f1: number | null
}

export interface EvaluationMetrics {
  available: boolean
  label: string
  reason: string | null
  calls_scored: number
  per_rule: RuleMetric[]
  notes: string[]
}

export interface Dashboard {
  scope: Role
  total_calls: number
  calls_analyzed: number
  calls_pending?: number
  calls_failed?: number
  calls_awaiting_review: number
  reviewed_calls?: number
  verified_violations: number
  counted_violations: number
  unverified_findings: number
  average_score: number | null
  breaches_by_rule: { rule_code: string; count: number }[]
  breaches_by_caller: DashboardRow[]
  breaches_by_office: DashboardRow[]
  top_risky_callers: DashboardRow[]
  review_outcomes: { confirmed: number; false_alarm: number; pending: number }
  accuracy: { available: boolean; reason: string }
  evaluation?: EvaluationMetrics | null
}

export interface ReviewCompleteResponse {
  call_id: string
  review_status: ReviewStatus
  reviewed_by: string | null
  reviewed_at: string | null
  score: number | null
}
