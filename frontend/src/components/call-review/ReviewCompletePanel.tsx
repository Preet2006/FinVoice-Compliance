import { useCompleteReview } from '../../api/corrections'
import { ApiError, type RoleHeaders } from '../../api/client'
import { ReviewStatusChip } from '../ui/ReviewStatusChip'
import type { CallDetail } from '../../api/types'

interface ReviewCompletePanelProps {
  call: CallDetail
  headers: RoleHeaders | null
  canReview: boolean
}

// The manager's explicit sign-off. A clean call can be completed as it is: no
// finding has to be added. The server refuses while a finding is still undecided.
export function ReviewCompletePanel({ call, headers, canReview }: ReviewCompletePanelProps) {
  const complete = useCompleteReview(call.call_id, headers)
  const status = call.review_status ?? 'not_applicable'
  if (call.status !== 'ready') return null

  return (
    <div data-testid="review-complete-panel" style={{ marginTop: '12px', display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
      <ReviewStatusChip status={status} />
      {status === 'reviewed' && (
        <span style={{ fontSize: 'var(--text-sm)', color: 'var(--text-secondary)' }}>
          Signed off by {call.review_completed_by ?? 'a reviewer'}
          {call.review_completed_at ? ` · ${new Date(call.review_completed_at).toLocaleString()}` : ''}
        </span>
      )}
      {status === 'pending_review' && canReview && (
        <button
          type="button"
          data-testid="complete-review"
          onClick={() => complete.mutate()}
          disabled={complete.isPending}
          style={{ padding: '6px 14px', borderRadius: 'var(--radius-input)', border: '1px solid var(--brand)', background: 'var(--brand)', color: '#fff', fontWeight: 600, cursor: 'pointer' }}
        >
          {complete.isPending ? 'Saving…' : 'Complete review'}
        </button>
      )}
      {status === 'pending_review' && !canReview && (
        <span style={{ fontSize: 'var(--text-sm)', color: 'var(--text-secondary)' }}>Waiting for a manager to review this call.</span>
      )}
      {complete.isError && (
        <span role="alert" style={{ fontSize: 'var(--text-sm)', color: 'var(--critical)' }}>
          {complete.error instanceof ApiError ? complete.error.message : 'Could not complete the review.'}
        </span>
      )}
      {canReview && (
        <span data-testid="reprocess-note" style={{ fontSize: 'var(--text-xs)', color: 'var(--text-secondary)', flexBasis: '100%' }}>
          Re-running the analysis is blocked once a call has reviewer work (a corrected speaker, a manual finding, a decision or a sign-off): it would erase that work.
        </span>
      )}
    </div>
  )
}
