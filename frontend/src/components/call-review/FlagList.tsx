import { EmptyState } from '../ui/EmptyState'
import { OUTCOME_LABELS } from '../ui/OutcomeChip'
import { ReviewPanel } from './ReviewPanel'
import type { RoleHeaders } from '../../api/client'
import type { AnalysisOutcome, Violation } from '../../api/types'

interface FlagListProps {
  violations: Violation[]
  isLoading: boolean
  canReview: boolean
  headers: RoleHeaders | null
  onSeek: (seconds: number) => void
  // Called with the finding when the reviewer plays it, so the page can also highlight its transcript evidence.
  onFocus?: (violation: Violation) => void
  // When given, an empty list only says "No violations detected" for a call
  // whose analysis really was clean.
  analysisOutcome?: AnalysisOutcome
}

const SEVERITY_COLOR: Record<Violation['severity'], string> = {
  critical: 'var(--critical)',
  high: 'var(--high)',
  medium: 'var(--medium)',
  low: 'var(--low)',
}

// A violation held back from the score: speaker attribution is uncertain and
// no manager has decided yet. Mirrors scoring_service.compute_score exactly:
// once confirmed it is counted like any other violation; a false alarm is
// never counted.
function isAwaitingSpeakerReview(v: Violation): boolean {
  return v.needs_speaker_review && v.review.decision === 'pending'
}

// A finding whose evidence failed validation and that no manager has decided
// yet. It is never counted in the score until confirmed with an audio check.
function isUnresolvedUnverified(v: Violation): boolean {
  return v.evidence_status === 'unverified' && v.review.decision === 'pending'
}

function SpeakerReviewBadge({ violation }: { violation: Violation }) {
  if (!violation.needs_speaker_review) return null

  const awaiting = isAwaitingSpeakerReview(violation)
  return (
    <span
      data-testid="speaker-review-badge"
      data-awaiting={awaiting}
      style={{
        fontSize: 'var(--text-xs)',
        fontWeight: 700,
        padding: '2px 8px',
        borderRadius: 'var(--radius-input)',
        border: `1px solid ${awaiting ? 'var(--medium)' : 'var(--border)'}`,
        background: awaiting ? 'var(--warning-bg)' : 'var(--surface-2)',
        color: awaiting ? 'var(--medium)' : 'var(--text-secondary)',
      }}
    >
      {awaiting ? 'Needs speaker review' : 'Speaker attribution reviewed'}
    </span>
  )
}

function UnverifiedEvidenceBadge({ violation }: { violation: Violation }) {
  if (violation.evidence_status !== 'unverified') return null

  const unresolved = isUnresolvedUnverified(violation)
  return (
    <span
      data-testid="unverified-evidence-badge"
      data-unresolved={unresolved}
      style={{
        fontSize: 'var(--text-xs)',
        fontWeight: 700,
        padding: '2px 8px',
        borderRadius: 'var(--radius-input)',
        border: `1px solid ${unresolved ? 'var(--critical)' : 'var(--border)'}`,
        background: unresolved ? 'var(--error-bg)' : 'var(--surface-2)',
        color: unresolved ? 'var(--critical)' : 'var(--text-secondary)',
      }}
    >
      {unresolved ? 'Unverified evidence' : 'Unverified evidence — resolved'}
    </span>
  )
}

const NOTE_STYLE = {
  fontSize: 'var(--text-sm)',
  color: 'var(--text-secondary)',
  marginTop: '4px',
} as const

// Explains how a row relates to the score when it belongs to a multi-row
// incident. Reads the backend's flags only; it never recomputes scoring.
function IncidentNote({ violation, group }: { violation: Violation; group: Violation[] }) {
  if (!violation.incident_group_id || violation.review.decision === 'false_alarm') return null

  const size = group.length
  const groupCounted = group.some((g) => g.counts_toward_score)
  let text: string
  if (violation.counts_toward_score) {
    text = `Part of one incident (${size} flags) — counted once in the score.`
  } else if (groupCounted) {
    text = `Part of one incident (${size} flags) — not counted separately; another flag in this incident carries the deduction.`
  } else {
    text = `Part of one incident (${size} flags) — this incident is not currently counted in the score.`
  }

  return (
    <p data-testid="incident-group-note" style={NOTE_STYLE}>
      {text}
    </p>
  )
}

function RuleOverlapNote({ violation }: { violation: Violation }) {
  if (violation.overlapping_rule_codes.length === 0 || violation.review.decision === 'false_alarm') {
    return null
  }
  const others = violation.overlapping_rule_codes.join(', ')
  return (
    <p data-testid="rule-overlap-note" style={NOTE_STYLE}>
      The same segment is also flagged under {others}. The rules are scored independently — check
      whether both apply, and mark one a false alarm if it describes the same act.
    </p>
  )
}

// What the manager is shown as evidence. For unverified evidence this is the
// stored transcript text (never the model's unverified quote), labelled as
// such, with the validation reason alongside.
function Evidence({ violation }: { violation: Violation }) {
  const unverified = violation.evidence_status === 'unverified'
  return (
    <>
      {unverified && (
        <p data-testid="evidence-label" style={{ ...NOTE_STYLE, marginTop: '8px', fontWeight: 600 }}>
          {violation.segment_id ? 'Stored transcript text (not the analysis’s proposed quote):' : 'No transcript evidence:'}
        </p>
      )}
      <p style={{ marginTop: unverified ? '2px' : '8px', fontStyle: 'italic' }}>&ldquo;{violation.quote}&rdquo;</p>
      {unverified && violation.evidence_reason && (
        <p data-testid="evidence-reason" style={{ ...NOTE_STYLE, color: 'var(--critical)' }}>
          Why this is unverified: {violation.evidence_reason}
        </p>
      )}
    </>
  )
}

export function FlagList({ violations, isLoading, canReview, headers, onSeek, onFocus, analysisOutcome }: FlagListProps) {
  if (isLoading) {
    return <p style={{ color: 'var(--text-secondary)' }}>Loading flags…</p>
  }

  if (violations.length === 0) {
    if (analysisOutcome && analysisOutcome !== 'clean') {
      return (
        <EmptyState
          title="No findings recorded"
          description={`This is not a verified clean result — analysis outcome: ${OUTCOME_LABELS[analysisOutcome]}.`}
        />
      )
    }
    return <EmptyState title="No violations detected" />
  }

  const awaitingCount = violations.filter(isAwaitingSpeakerReview).length
  const unverifiedCount = violations.filter(isUnresolvedUnverified).length

  const groups = new Map<string, Violation[]>()
  for (const v of violations) {
    if (v.incident_group_id) {
      groups.set(v.incident_group_id, [...(groups.get(v.incident_group_id) ?? []), v])
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
      {unverifiedCount > 0 && (
        <p
          data-testid="unverified-evidence-banner"
          style={{
            padding: '10px 12px',
            background: 'var(--error-bg)',
            borderRadius: 'var(--radius-input)',
            fontSize: 'var(--text-sm)',
          }}
        >
          {unverifiedCount} {unverifiedCount === 1 ? 'finding has' : 'findings have'} evidence that
          could not be verified against the transcript and {unverifiedCount === 1 ? 'is' : 'are'} not
          counted in the call score. Listen to the audio, then confirm it (with an explicit
          attestation) or mark it a false alarm.
        </p>
      )}
      {awaitingCount > 0 && (
        <p
          role="note"
          style={{
            padding: '10px 12px',
            background: 'var(--warning-bg)',
            borderRadius: 'var(--radius-input)',
            fontSize: 'var(--text-sm)',
          }}
        >
          {awaitingCount} {awaitingCount === 1 ? 'flag is' : 'flags are'} awaiting speaker review
          and {awaitingCount === 1 ? 'is' : 'are'} not counted in the call score until reviewed.
          Check the audio to confirm the agent said it: confirming counts the flag toward the
          score; marking it a false alarm removes it.
        </p>
      )}
      {groups.size > 0 && (
        <p
          data-testid="incident-group-summary"
          style={{ fontSize: 'var(--text-sm)', color: 'var(--text-secondary)' }}
        >
          Some flags below are fragments of one incident (for example one sentence split across
          transcript segments). Each incident is counted once in the score, so not every flag
          below deducts separately.
        </p>
      )}
      {violations.map((v) => (
        <div
          key={v.violation_id}
          data-awaiting-speaker-review={isAwaitingSpeakerReview(v)}
          data-unresolved-unverified={isUnresolvedUnverified(v)}
          style={{
            border: '1px solid var(--border)',
            borderLeft: isUnresolvedUnverified(v)
              ? '4px solid var(--critical)'
              : isAwaitingSpeakerReview(v)
                ? '4px solid var(--medium)'
                : '1px solid var(--border)',
            borderRadius: 'var(--radius-card)',
            padding: '12px 16px',
            background: 'var(--surface-1)',
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }}>
              <span
                style={{
                  fontSize: 'var(--text-xs)',
                  fontWeight: 700,
                  textTransform: 'uppercase',
                  color: SEVERITY_COLOR[v.severity],
                }}
              >
                {v.severity}
              </span>
              <span style={{ fontWeight: 600 }} data-testid="rule-title">
                {v.rule_code} — {v.rule_name}
              </span>
              {v.rule_description && (
                <span data-testid="rule-description" style={{ fontSize: 'var(--text-xs)', color: 'var(--text-secondary)' }}>
                  {v.rule_description}
                </span>
              )}
              <UnverifiedEvidenceBadge violation={v} />
              <SpeakerReviewBadge violation={v} />
              {v.source === 'manual' && (
                <span
                  data-testid="manual-badge"
                  style={{
                    fontSize: 'var(--text-xs)',
                    fontWeight: 700,
                    padding: '2px 8px',
                    borderRadius: 'var(--radius-input)',
                    border: '1px solid var(--brand)',
                    color: 'var(--brand)',
                  }}
                >
                  Added manually{v.created_by ? ` by ${v.created_by}` : ''}
                </span>
              )}
            </div>
            {v.start_time_seconds !== null && (
              <button
                onClick={() => {
                  onSeek(v.start_time_seconds ?? 0)
                  onFocus?.(v)
                }}
                style={{
                  padding: '4px 10px',
                  borderRadius: 'var(--radius-input)',
                  border: '1px solid var(--border)',
                  background: 'var(--surface-0)',
                  cursor: 'pointer',
                  fontSize: 'var(--text-sm)',
                }}
              >
                ▶ Play
              </button>
            )}
          </div>

          <Evidence violation={v} />
          <p data-testid="score-impact" style={NOTE_STYLE}>
            {v.review.decision === 'false_alarm'
              ? 'Dismissed — no deduction.'
              : v.counts_toward_score
                ? `Deducts ${v.score_deduction ?? 0} points from the score.`
                : 'Not deducted from the score right now (awaiting review, unverified, or part of an incident counted once).'}
          </p>
          {v.explanation && (
            <p style={{ color: 'var(--text-secondary)', fontSize: 'var(--text-sm)', marginTop: '4px' }}>
              {v.explanation}
            </p>
          )}

          <IncidentNote violation={v} group={groups.get(v.incident_group_id ?? '') ?? []} />
          <RuleOverlapNote violation={v} />

          {canReview && headers && <ReviewPanel violation={v} headers={headers} />}
          {!canReview && v.review.decision !== 'pending' && (
            <p style={{ fontSize: 'var(--text-sm)', color: 'var(--text-secondary)', marginTop: '8px' }}>
              {v.review.decision === 'confirmed' ? 'Confirmed' : 'Marked false alarm'}
              {v.review.reviewer ? ` by ${v.review.reviewer}` : ''}
            </p>
          )}
        </div>
      ))}
    </div>
  )
}
