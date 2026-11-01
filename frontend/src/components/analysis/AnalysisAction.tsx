import { useAnalysisQueue } from '../../api/analysis'
import type { RoleHeaders } from '../../api/client'
import type { CallDetail } from '../../api/types'

interface AnalysisActionProps {
  call: CallDetail
  headers: RoleHeaders | null
  canRun: boolean
  paceMs?: number
}

// The start / retry control on a single call's review page. It is offered only
// for a call that has a recording and either has never been analysed (queued)
// or whose analysis failed with nothing to show. A call that already has
// results is never offered here: re-running it would replace those results
// and the manager decisions made on them.
export function AnalysisAction({ call, headers, canRun, paceMs = 0 }: AnalysisActionProps) {
  const queue = useAnalysisQueue(headers, paceMs)

  const eligible = call.has_audio && (call.status === 'queued' || call.status === 'failed')
  if (!eligible) return null

  if (!canRun) {
    return (
      <p data-testid="analysis-forbidden-note" style={{ marginTop: '8px', fontSize: 'var(--text-sm)', color: 'var(--text-secondary)' }}>
        Only a manager or head of collections can {call.status === 'failed' ? 'retry' : 'start'} analysis.
      </p>
    )
  }

  const entry = queue.entries[call.call_id]
  const busy = queue.isRunning || entry?.state === 'running' || entry?.state === 'waiting'
  const label = call.status === 'failed' ? 'Retry analysis' : 'Start analysis'

  return (
    <div style={{ marginTop: '8px', display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
      <button
        type="button"
        data-testid="analysis-action"
        onClick={() => queue.start([call.call_id])}
        disabled={busy}
        style={{
          padding: '8px 18px',
          borderRadius: 'var(--radius-input)',
          border: 'none',
          background: busy ? 'var(--surface-2)' : 'var(--brand)',
          color: busy ? 'var(--text-secondary)' : '#fff',
          fontWeight: 600,
          cursor: busy ? 'not-allowed' : 'pointer',
        }}
      >
        {busy ? 'Analysing…' : label}
      </button>
      {entry?.state === 'busy' && (
        <span data-testid="analysis-busy" style={{ fontSize: 'var(--text-sm)' }}>
          This call is already being processed. The page updates when it finishes.
        </span>
      )}
      {entry?.state === 'failed' && entry.kind === 'forbidden' && (
        <span role="alert" style={{ fontSize: 'var(--text-sm)', color: 'var(--critical)' }}>
          {entry.reason}
        </span>
      )}
    </div>
  )
}
