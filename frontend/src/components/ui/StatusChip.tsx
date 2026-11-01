import type { CallStatus } from '../../api/types'

const LABELS: Record<CallStatus, string> = {
  metadata_only: 'No audio',
  queued: 'Queued',
  transcribing: 'Transcribing',
  analyzing: 'Analyzing',
  ready: 'Completed',
  failed: 'Failed',
}

const COLORS: Record<CallStatus, { bg: string; fg: string }> = {
  metadata_only: { bg: 'var(--surface-2)', fg: 'var(--text-secondary)' },
  queued: { bg: 'var(--warning-bg)', fg: 'var(--medium)' },
  transcribing: { bg: 'var(--warning-bg)', fg: 'var(--medium)' },
  analyzing: { bg: 'var(--warning-bg)', fg: 'var(--medium)' },
  ready: { bg: '#E6F4EA', fg: 'var(--success)' },
  failed: { bg: 'var(--error-bg)', fg: 'var(--critical)' },
}

export function StatusChip({ status }: { status: CallStatus }) {
  const { bg, fg } = COLORS[status]
  return (
    <span
      style={{
        display: 'inline-block',
        padding: '2px 10px',
        borderRadius: '999px',
        fontSize: 'var(--text-xs)',
        fontWeight: 600,
        textTransform: 'uppercase',
        letterSpacing: '0.02em',
        background: bg,
        color: fg,
      }}
    >
      {LABELS[status]}
    </span>
  )
}
