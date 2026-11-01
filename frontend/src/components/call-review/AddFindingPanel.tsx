import { useState } from 'react'
import { useAddManualFinding } from '../../api/corrections'
import { ApiError, type RoleHeaders } from '../../api/client'
import type { TranscriptSegment } from '../../api/types'

interface AddFindingPanelProps {
  callId: string
  segments: TranscriptSegment[]
  headers: RoleHeaders
}

// R1 is evaluated from the call's start time, so it is not offered here.
const RULES = [
  { code: 'R2', label: 'R2 — Abusive or foul language' },
  { code: 'R3', label: 'R3 — Threats or intimidation' },
  { code: 'R4', label: 'R4 — Shaming or disclosure to third parties' },
  { code: 'R5', label: 'R5 — Asked for OTP, PIN, CVV or password' },
  { code: 'R6', label: 'R6 — Mandatory opening missing' },
]

// A word is a run of non-whitespace, the same definition the server uses to
// number words; the server re-reads the quote from the stored transcript.
export function wordSpans(text: string): { start: number; end: number }[] {
  return Array.from(text.matchAll(/\S+/g)).map((m) => ({ start: m.index ?? 0, end: (m.index ?? 0) + m[0].length }))
}

export function selectedText(text: string, from: number, to: number): string {
  const words = wordSpans(text)
  if (from < 0 || to >= words.length || from > to) return ''
  return text.slice(words[from].start, words[to].end)
}

export function AddFindingPanel({ callId, segments, headers }: AddFindingPanelProps) {
  const [open, setOpen] = useState(false)
  const [segmentId, setSegmentId] = useState('')
  const [rule, setRule] = useState('R2')
  const [range, setRange] = useState<{ from: number; to: number } | null>(null)
  const [anchor, setAnchor] = useState<number | null>(null)
  const [comment, setComment] = useState('')
  const [done, setDone] = useState<string | null>(null)
  const add = useAddManualFinding(callId, headers)

  const segment = segments.find((s) => s.id === segmentId)
  const words = segment ? wordSpans(segment.text) : []
  const notAgent = segment !== undefined && segment.speaker !== 'agent'
  const evidence = segment && range ? selectedText(segment.text, range.from, range.to) : ''
  const canSave = Boolean(segment) && !notAgent && range !== null && comment.trim().length > 0 && !add.isPending

  const reset = () => {
    setSegmentId('')
    setRange(null)
    setAnchor(null)
    setComment('')
    add.reset()
  }

  const pickWord = (i: number) => {
    if (anchor === null) {
      setAnchor(i)
      setRange({ from: i, to: i })
    } else {
      setRange({ from: Math.min(anchor, i), to: Math.max(anchor, i) })
      setAnchor(null)
    }
  }

  const save = () => {
    if (!segment || !range) return
    add.mutate(
      { rule_code: rule, segment_id: segment.id, word_start: range.from, word_end: range.to, comment: comment.trim() },
      {
        onSuccess: (res) => {
          setDone(`${res.violation.rule_code} added. Call score is now ${res.call.score ?? '—'}.`)
          setOpen(false)
          reset()
        },
      },
    )
  }

  if (!open) {
    return (
      <div style={{ marginTop: '12px' }}>
        <button type="button" onClick={() => { setDone(null); setOpen(true) }} style={secondaryButton}>
          + Add Manual Finding
        </button>
        {done && (
          <p role="status" data-testid="manual-finding-added" style={{ marginTop: '6px', fontSize: 'var(--text-sm)', color: 'var(--text-secondary)' }}>
            {done}
          </p>
        )}
      </div>
    )
  }

  return (
    <form
      aria-label="Add manual finding"
      onSubmit={(e) => { e.preventDefault(); if (canSave) save() }}
      style={{ marginTop: '12px', padding: '12px 16px', border: '1px solid var(--border)', borderRadius: 'var(--radius-card)', background: 'var(--surface-1)', display: 'flex', flexDirection: 'column', gap: '10px' }}
    >
      <p style={{ fontSize: 'var(--text-sm)', color: 'var(--text-secondary)' }}>
        Add a violation the analysis missed. The evidence is the transcript words you select; it is read from the stored transcript, not typed.
      </p>

      <label style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
        Rule
        <select value={rule} onChange={(e) => setRule(e.target.value)} style={field}>
          {RULES.map((r) => <option key={r.code} value={r.code}>{r.label}</option>)}
        </select>
      </label>

      <label style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
        Transcript segment
        <select value={segmentId} onChange={(e) => { setSegmentId(e.target.value); setRange(null); setAnchor(null) }} style={field}>
          <option value="">Select a segment…</option>
          {segments.map((s) => (
            <option key={s.id} value={s.id}>
              #{s.seq} · {s.speaker} · {s.text.slice(0, 60)}
            </option>
          ))}
        </select>
      </label>

      {notAgent && (
        <p role="alert" style={{ color: 'var(--critical)', fontSize: 'var(--text-sm)' }}>
          This segment is labelled {segment?.speaker}. These rules apply to the agent&rsquo;s words — correct the speaker in the transcript first.
        </p>
      )}

      {segment && !notAgent && (
        <div>
          <p style={{ fontSize: 'var(--text-sm)', marginBottom: '4px' }}>
            Click the first word, then the last word of the evidence{anchor !== null ? ' (now click the last word)' : ''}.
          </p>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
            {words.map((w, i) => {
              const selected = range !== null && i >= range.from && i <= range.to
              return (
                <button
                  key={i}
                  type="button"
                  data-word-index={i}
                  aria-pressed={selected}
                  onClick={() => pickWord(i)}
                  style={{ padding: '2px 6px', borderRadius: 'var(--radius-input)', border: '1px solid var(--border)', cursor: 'pointer', background: selected ? 'var(--brand)' : 'var(--surface-0)', color: selected ? 'white' : 'inherit' }}
                >
                  {segment.text.slice(w.start, w.end)}
                </button>
              )
            })}
          </div>
        </div>
      )}

      {evidence && (
        <p data-testid="selected-evidence" style={{ fontStyle: 'italic' }}>
          Evidence: &ldquo;{evidence}&rdquo;
        </p>
      )}

      <label style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
        Why is this a violation? (required)
        <textarea value={comment} maxLength={1000} rows={2} onChange={(e) => setComment(e.target.value)} style={field} />
      </label>

      {add.isError && (
        <p role="alert" style={{ color: 'var(--critical)', fontSize: 'var(--text-sm)' }}>
          {add.error instanceof ApiError ? add.error.message : 'Could not add the finding.'}
        </p>
      )}

      <div style={{ display: 'flex', gap: '8px' }}>
        <button type="submit" disabled={!canSave} style={{ ...secondaryButton, background: canSave ? 'var(--brand)' : 'var(--surface-2)', color: canSave ? 'white' : 'var(--text-secondary)' }}>
          {add.isPending ? 'Saving…' : 'Save finding'}
        </button>
        <button type="button" onClick={() => { setOpen(false); reset() }} style={secondaryButton}>Cancel</button>
      </div>
    </form>
  )
}

const field = { padding: '6px 8px', border: '1px solid var(--border)', borderRadius: 'var(--radius-input)', background: 'var(--surface-0)', font: 'inherit' } as const
const secondaryButton = { padding: '6px 14px', borderRadius: 'var(--radius-input)', border: '1px solid var(--border)', background: 'var(--surface-0)', cursor: 'pointer' } as const
