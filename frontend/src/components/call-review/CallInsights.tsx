import type { CallStatus } from '../../api/types'

type Sentiment = 'positive' | 'neutral' | 'negative'

interface CallInsightsProps {
  sentiment: Sentiment | null
  agentStayedCalm: boolean | null
  status: CallStatus
}

const SENTIMENT_LABEL: Record<Sentiment, string> = {
  positive: 'Positive',
  neutral: 'Neutral',
  negative: 'Negative',
}

const SENTIMENT_COLOR: Record<Sentiment, { bg: string; fg: string }> = {
  positive: { bg: '#E6F4EA', fg: 'var(--success)' },
  neutral: { bg: 'var(--surface-2)', fg: 'var(--text-secondary)' },
  negative: { bg: 'var(--warning-bg)', fg: 'var(--medium)' },
}

const PILL = {
  display: 'inline-block',
  padding: '2px 10px',
  borderRadius: '999px',
  fontSize: 'var(--text-xs)',
  fontWeight: 600,
} as const

// Borrower sentiment and whether the agent stayed calm, as produced by the
// analysis. Missing values are shown as such, never as a neutral default.
export function CallInsights({ sentiment, agentStayedCalm, status }: CallInsightsProps) {
  const analysed = status === 'ready'
  const missing = analysed ? 'Not available' : 'After analysis'

  return (
    <div
      data-testid="call-insights"
      style={{ display: 'flex', gap: '24px', flexWrap: 'wrap', marginTop: '8px', fontSize: 'var(--text-sm)' }}
    >
      <span>
        Borrower sentiment:{' '}
        {sentiment ? (
          <span
            data-testid="sentiment-value"
            style={{ ...PILL, background: SENTIMENT_COLOR[sentiment].bg, color: SENTIMENT_COLOR[sentiment].fg }}
          >
            {SENTIMENT_LABEL[sentiment]}
          </span>
        ) : (
          <span data-testid="sentiment-value" style={{ color: 'var(--text-secondary)' }}>
            {missing}
          </span>
        )}
      </span>
      <span>
        Agent stayed calm:{' '}
        {agentStayedCalm === null ? (
          <span data-testid="calm-value" style={{ color: 'var(--text-secondary)' }}>
            {missing}
          </span>
        ) : (
          <span
            data-testid="calm-value"
            style={{
              ...PILL,
              background: agentStayedCalm ? '#E6F4EA' : 'var(--error-bg)',
              color: agentStayedCalm ? 'var(--success)' : 'var(--critical)',
            }}
          >
            {agentStayedCalm ? 'Yes' : 'No'}
          </span>
        )}
      </span>
    </div>
  )
}
