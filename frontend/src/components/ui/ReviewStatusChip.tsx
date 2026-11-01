import type { ReviewStatus } from '../../api/types'

const CHIP: Record<ReviewStatus, { label: string; bg: string; fg: string }> = {
  not_applicable: { label: '—', bg: 'transparent', fg: 'var(--text-secondary)' },
  pending_review: { label: 'Pending review', bg: 'var(--warning-bg)', fg: 'var(--medium)' },
  reviewed: { label: 'Reviewed', bg: '#E6F4EA', fg: 'var(--success)' },
}

// Whether a manager has signed the call off. 'Reviewed' only ever means an
// explicit sign-off, never that the AI analysis finished.
export function ReviewStatusChip({ status }: { status: ReviewStatus | undefined }) {
  const { label, bg, fg } = CHIP[status ?? 'not_applicable']
  return (
    <span
      data-testid="review-status"
      data-review-status={status ?? 'not_applicable'}
      style={{ display: 'inline-block', padding: '2px 10px', borderRadius: '999px', fontSize: 'var(--text-xs)', fontWeight: 600, background: bg, color: fg }}
    >
      {label}
    </span>
  )
}
