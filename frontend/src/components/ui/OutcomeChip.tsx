import type { AnalysisOutcome } from '../../api/types'

export const OUTCOME_LABELS: Record<AnalysisOutcome, string> = {
  not_analyzed: 'Not analysed',
  failed: 'Failed',
  incomplete: 'Incomplete',
  violations_found: 'Violations found',
  pending_review: 'Pending review',
  clean: 'Clean',
}

const OUTCOME_HINTS: Record<AnalysisOutcome, string> = {
  not_analyzed: 'Not analysed yet: queued, processing, or no recording.',
  failed: 'The analysis failed and there are no results.',
  incomplete: 'Some findings have evidence that could not be verified. The score is provisional.',
  violations_found: 'At least one confirmed finding counts toward the score.',
  pending_review: 'Findings are waiting for a manager to review them. The score is provisional.',
  clean: 'Analysed successfully with no violations and nothing awaiting review.',
}

const COLORS: Record<AnalysisOutcome, { bg: string; fg: string }> = {
  not_analyzed: { bg: 'var(--surface-2)', fg: 'var(--text-secondary)' },
  failed: { bg: 'var(--error-bg)', fg: 'var(--critical)' },
  incomplete: { bg: 'var(--error-bg)', fg: 'var(--critical)' },
  violations_found: { bg: 'var(--error-bg)', fg: 'var(--critical)' },
  pending_review: { bg: 'var(--warning-bg)', fg: 'var(--medium)' },
  clean: { bg: '#E6F4EA', fg: 'var(--success)' },
}

export function OutcomeChip({ outcome }: { outcome: AnalysisOutcome }) {
  const { bg, fg } = COLORS[outcome]
  return (
    <span
      data-testid="outcome-chip"
      data-outcome={outcome}
      title={OUTCOME_HINTS[outcome]}
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
      {OUTCOME_LABELS[outcome]}
    </span>
  )
}
