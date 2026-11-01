import { useState } from 'react'
import { useSubmitReview } from '../../api/violations'
import { ApiError, type RoleHeaders } from '../../api/client'
import type { Violation } from '../../api/types'

interface ReviewPanelProps {
  violation: Violation
  headers: RoleHeaders
}

function formatReviewedAt(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

// Rendered only when role !== 'caller' — the parent decides whether to
// mount this at all, so a caller identity never has this component (and
// its Confirm/False-alarm controls) in the DOM, not just visually hidden.
export function ReviewPanel({ violation, headers }: ReviewPanelProps) {
  const [comment, setComment] = useState('')
  const [audioChecked, setAudioChecked] = useState(false)
  const submitReview = useSubmitReview(headers)

  const { decision, reviewer, reviewed_at } = violation.review
  // Evidence that failed validation is not made valid by a click: confirming
  // it needs an explicit attestation plus a comment on what was heard.
  const unverified = violation.evidence_status === 'unverified'

  if (decision !== 'pending') {
    return (
      <p style={{ fontSize: 'var(--text-sm)', color: 'var(--text-secondary)', marginTop: '8px' }}>
        {decision === 'confirmed' ? 'Confirmed' : 'Marked false alarm'}
        {reviewer ? ` by ${reviewer}` : ''}
        {reviewed_at ? ` at ${formatReviewedAt(reviewed_at)}` : ''}
        {violation.review.comment ? ` — "${violation.review.comment}"` : ''}
        {decision === 'confirmed' && unverified ? ' (evidence verified by the reviewer against the audio)' : ''}
      </p>
    )
  }

  const confirmBlocked = unverified && (!audioChecked || comment.trim().length === 0)
  const confirmDisabled = confirmBlocked || submitReview.isPending
  const falseAlarmDisabled = comment.trim().length === 0 || submitReview.isPending

  return (
    <div style={{ marginTop: '8px' }}>
      <textarea
        value={comment}
        onChange={(e) => setComment(e.target.value)}
        placeholder={
          unverified
            ? 'Comment — describe what you heard (required to confirm or dismiss)'
            : 'Comment (required for False alarm, optional for Confirm)'
        }
        rows={2}
        style={{
          width: '100%',
          padding: '6px 8px',
          borderRadius: 'var(--radius-input)',
          border: '1px solid var(--border)',
          fontSize: 'var(--text-sm)',
          resize: 'vertical',
        }}
      />
      {unverified && (
        <label
          style={{ display: 'flex', gap: '6px', alignItems: 'center', marginTop: '6px', fontSize: 'var(--text-sm)' }}
        >
          <input
            type="checkbox"
            checked={audioChecked}
            onChange={(e) => setAudioChecked(e.target.checked)}
          />
          I listened to the audio and verified this finding myself
        </label>
      )}
      <div style={{ display: 'flex', gap: '8px', marginTop: '6px' }}>
        <button
          disabled={confirmDisabled}
          onClick={() =>
            submitReview.mutate({
              violationId: violation.violation_id,
              body: {
                decision: 'confirmed',
                comment: comment.trim() || undefined,
                ...(unverified ? { evidence_verified: true } : {}),
              },
            })
          }
          title={
            confirmBlocked && !submitReview.isPending
              ? 'Tick the audio check and describe what you heard to confirm unverified evidence'
              : undefined
          }
          style={{
            padding: '6px 14px',
            borderRadius: 'var(--radius-input)',
            border: '1px solid var(--success)',
            background: 'var(--surface-1)',
            color: 'var(--success)',
            cursor: confirmDisabled ? 'not-allowed' : 'pointer',
            opacity: confirmBlocked ? 0.5 : 1,
          }}
        >
          Confirm
        </button>
        <button
          disabled={falseAlarmDisabled}
          onClick={() =>
            submitReview.mutate({
              violationId: violation.violation_id,
              body: { decision: 'false_alarm', comment: comment.trim() },
            })
          }
          title={falseAlarmDisabled && !submitReview.isPending ? 'A comment is required for False alarm' : undefined}
          style={{
            padding: '6px 14px',
            borderRadius: 'var(--radius-input)',
            border: '1px solid var(--critical)',
            background: 'var(--surface-1)',
            color: 'var(--critical)',
            cursor: falseAlarmDisabled ? 'not-allowed' : 'pointer',
            opacity: falseAlarmDisabled ? 0.5 : 1,
          }}
        >
          False alarm
        </button>
      </div>
      {submitReview.isError && (
        <p style={{ color: 'var(--critical)', fontSize: 'var(--text-sm)', marginTop: '4px' }}>
          {submitReview.error instanceof ApiError
            ? submitReview.error.message
            : 'Failed to submit review.'}
        </p>
      )}
    </div>
  )
}
