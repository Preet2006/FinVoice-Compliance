import type { CallListItem } from '../../api/types'

const COLORS: Record<string, { bg: string; fg: string }> = {
  critical: { bg: 'var(--error-bg)', fg: 'var(--critical)' },
  high: { bg: 'var(--error-bg)', fg: 'var(--high)' },
  medium: { bg: 'var(--warning-bg)', fg: 'var(--medium)' },
  low: { bg: 'var(--surface-2)', fg: 'var(--low)' },
}

// The call's risk level: the highest severity among findings that deduct from its score.
export function RiskChip({ severity, analysed }: { severity: CallListItem['top_severity']; analysed: boolean }) {
  if (!analysed) return <span>—</span>
  if (!severity) {
    return (
      <span data-testid="risk-chip" data-risk="none" title="No finding deducts from this call's score." style={{ color: 'var(--text-secondary)' }}>
        None
      </span>
    )
  }
  const { bg, fg } = COLORS[severity]
  return (
    <span
      data-testid="risk-chip"
      data-risk={severity}
      title="Highest severity among findings that deduct from the score."
      style={{ display: 'inline-block', padding: '2px 10px', borderRadius: '999px', background: bg, color: fg, fontSize: 'var(--text-xs)', fontWeight: 700, textTransform: 'uppercase' }}
    >
      {severity}
    </span>
  )
}
