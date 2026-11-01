import { useState } from 'react'
import { useCallAudit } from '../../api/corrections'
import type { RoleHeaders } from '../../api/client'
import type { AuditEntry } from '../../api/types'

function describe(e: AuditEntry): string {
  const d = e.detail as Record<string, string | number | null>
  if (e.action === 'speaker_correction') {
    return `Segment #${d.segment_seq}: ${d.previous_effective} → ${d.new_speaker} (AI said ${d.ai_speaker})${d.comment ? ` — “${d.comment}”` : ''}`
  }
  return `Added ${d.rule_code} on segment #${d.segment_seq}: “${d.quote}” — “${d.comment}”`
}

// Reviewer history for one call: who changed what, and when. Read-only; the
// server never edits or removes these entries.
export function CorrectionHistory({ callId, headers }: { callId: string; headers: RoleHeaders }) {
  const [open, setOpen] = useState(false)
  const audit = useCallAudit(callId, headers, open)
  return (
    <details open={open} onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)} style={{ marginTop: '12px' }}>
      <summary style={{ cursor: 'pointer', fontWeight: 600 }}>Correction history</summary>
      {audit.isLoading && <p>Loading…</p>}
      {audit.isError && <p role="alert" style={{ color: 'var(--critical)' }}>Could not load the history.</p>}
      {audit.data && audit.data.length === 0 && (
        <p style={{ color: 'var(--text-secondary)' }}>No corrections or manual findings yet.</p>
      )}
      {audit.data && audit.data.length > 0 && (
        <ul style={{ marginTop: '6px', paddingLeft: '18px', fontSize: 'var(--text-sm)' }}>
          {audit.data.map((e) => (
            <li key={e.id}>
              {new Date(e.created_at).toLocaleString()} · {e.actor ?? 'unknown'} · {describe(e)}
            </li>
          ))}
        </ul>
      )}
    </details>
  )
}
