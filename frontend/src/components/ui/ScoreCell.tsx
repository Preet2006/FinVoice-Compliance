interface ScoreCellProps {
  score: number | null
  // True while unresolved findings remain: the number is right for what it
  // counts but is not final, and must not read as a verified result.
  provisional: boolean
}

export function ScoreCell({ score, provisional }: ScoreCellProps) {
  return (
    <span>
      <span className="font-mono">{score ?? '—'}</span>
      {provisional && score !== null && (
        <span
          data-testid="provisional-marker"
          title="Provisional: findings that are not yet counted are awaiting review."
          style={{ marginLeft: '6px', fontSize: 'var(--text-xs)', color: 'var(--medium)', fontWeight: 600 }}
        >
          provisional
        </span>
      )}
    </span>
  )
}

interface ProvisionalNoteProps {
  unverified: number
  pendingReview: number
}

function plural(n: number, singular: string, pluralForm: string): string {
  return `${n} ${n === 1 ? singular : pluralForm}`
}

// Says exactly why a score is provisional, so it is not mistaken for final.
export function ProvisionalNote({ unverified, pendingReview }: ProvisionalNoteProps) {
  if (unverified <= 0 && pendingReview <= 0) return null

  const parts: string[] = []
  if (unverified > 0) parts.push(`${plural(unverified, 'finding', 'findings')} with unverified evidence`)
  if (pendingReview > 0) parts.push(`${plural(pendingReview, 'finding', 'findings')} awaiting speaker review`)

  return (
    <p
      data-testid="provisional-note"
      style={{
        marginTop: '8px',
        padding: '8px 12px',
        background: 'var(--warning-bg)',
        borderRadius: 'var(--radius-input)',
        fontSize: 'var(--text-sm)',
      }}
    >
      This score is provisional: {parts.join(' and ')} {unverified + pendingReview === 1 ? 'is' : 'are'} not
      counted until a manager reviews {unverified + pendingReview === 1 ? 'it' : 'them'}.
    </p>
  )
}
