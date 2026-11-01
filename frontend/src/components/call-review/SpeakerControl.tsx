import { useState } from 'react'
import { useCorrectSpeaker } from '../../api/corrections'
import { ApiError, type RoleHeaders } from '../../api/client'
import type { TranscriptSegment } from '../../api/types'

interface SpeakerControlProps {
  callId: string
  segment: TranscriptSegment
  headers: RoleHeaders
}

// Agent / Borrower correction for one segment. Rendered only for a manager or
// head (the parent decides); the AI's own label is never overwritten, it stays
// visible beside the correction.
export function SpeakerControl({ callId, segment, headers }: SpeakerControlProps) {
  const [target, setTarget] = useState<TranscriptSegment['speaker'] | null>(null)
  const [comment, setComment] = useState('')
  const correct = useCorrectSpeaker(callId, headers)

  const other = segment.speaker === 'agent' ? 'borrower' : 'agent'
  const states: TranscriptSegment['speaker'][] = ['agent', 'borrower', 'third_party', 'unknown', 'mixed']

  const save = () => {
    if (!target) return
    correct.mutate(
      { segmentId: segment.id, speaker: target, comment },
      {
        onSuccess: () => {
          setTarget(null)
          setComment('')
        },
      },
    )
  }

  return (
    <div style={{ marginLeft: '60px', marginBottom: '6px', fontSize: 'var(--text-sm)' }}>
      {target === null ? (
        <button
          type="button"
          data-testid={`correct-speaker-${segment.id}`}
          onClick={() => setTarget(other)}
          style={linkButton}
        >
          Mark as {other}
        </button>
      ) : (
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }}>
          <span>
            Change to <strong>{target}</strong>?
          </span>
          <select aria-label="New speaker" value={target} onChange={(e) => setTarget(e.target.value as TranscriptSegment['speaker'])}>
            {states.map((state) => <option key={state} value={state}>{state}</option>)}
          </select>
          <input
            aria-label="Reason (optional)"
            placeholder="Reason (optional)"
            value={comment}
            maxLength={1000}
            onChange={(e) => setComment(e.target.value)}
            style={{ padding: '4px 8px', border: '1px solid var(--border)', borderRadius: 'var(--radius-input)', minWidth: '200px' }}
          />
          <button type="button" onClick={save} disabled={correct.isPending} style={primaryButton}>
            {correct.isPending ? 'Saving…' : 'Save'}
          </button>
          <button type="button" onClick={() => setTarget(null)} disabled={correct.isPending} style={linkButton}>
            Cancel
          </button>
        </div>
      )}
      {correct.isError && (
        <p role="alert" style={{ color: 'var(--critical)', marginTop: '4px' }}>
          {correct.error instanceof ApiError ? correct.error.message : 'Could not save the correction.'}
        </p>
      )}
    </div>
  )
}

const linkButton = {
  background: 'none',
  border: 'none',
  color: 'var(--brand)',
  cursor: 'pointer',
  padding: 0,
  textDecoration: 'underline',
  fontSize: 'inherit',
} as const

const primaryButton = {
  padding: '4px 12px',
  borderRadius: 'var(--radius-input)',
  border: '1px solid var(--brand)',
  background: 'var(--brand)',
  color: 'white',
  cursor: 'pointer',
} as const
