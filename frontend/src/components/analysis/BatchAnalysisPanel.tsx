import { Link } from 'react-router-dom'
import { useAnalysisQueue, type QueueEntry } from '../../api/analysis'
import { isInProgress } from '../../api/analysisStatus'
import { useCall } from '../../api/calls'
import type { RoleHeaders } from '../../api/client'
import type { UploadAcceptedItem } from '../../api/types'
import { OutcomeChip } from '../ui/OutcomeChip'
import { ScoreCell } from '../ui/ScoreCell'
import { StatusChip } from '../ui/StatusChip'
import { FailureNotice } from './FailureNotice'

interface BatchAnalysisPanelProps {
  accepted: UploadAcceptedItem[]
  headers: RoleHeaders | null
  // Only a manager or head may start or retry analysis (enforced by the backend too).
  canRun: boolean
  paceMs?: number
}

const CELL = { padding: '10px 14px', borderBottom: '1px solid var(--border)', verticalAlign: 'top' } as const

const BUTTON = {
  padding: '6px 14px',
  borderRadius: 'var(--radius-input)',
  border: '1px solid var(--border)',
  background: 'var(--surface-0)',
  fontWeight: 600,
} as const

function BatchRow({
  item,
  headers,
  entry,
  canRun,
  onRetry,
}: {
  item: UploadAcceptedItem
  headers: RoleHeaders | null
  entry: QueueEntry | undefined
  canRun: boolean
  onRetry: (id: string) => void
}) {
  // forcePoll while this call is running, so 'transcribing' / 'analyzing' show up live.
  const { data: call } = useCall(item.call_id, headers, { forcePoll: entry?.state === 'running' })

  const finished = call?.status === 'ready' || call?.status === 'failed'
  const failed = entry?.state === 'failed' || call?.status === 'failed'
  const retryable = canRun && failed && entry?.state !== 'waiting' && entry?.state !== 'running'

  return (
    <tr data-testid={`batch-row-${item.call_id}`}>
      <td style={CELL} className="font-mono">
        {item.call_id} ({item.filename})
      </td>
      <td style={CELL}>
        {entry?.state === 'waiting' ? (
          <span data-testid="row-state">Waiting in queue</span>
        ) : entry?.state === 'skipped' ? (
          <span data-testid="row-state">Not started</span>
        ) : entry?.state === 'busy' ? (
          <span data-testid="row-state">Already being processed</span>
        ) : entry?.state === 'running' && !isInProgress(call?.status) ? (
          // Started, but the first status poll has not seen the backend's
          // 'transcribing' yet: say so instead of showing a stale 'Queued'.
          <span data-testid="row-state">Starting…</span>
        ) : (
          <StatusChip status={call?.status ?? 'queued'} />
        )}
      </td>
      <td style={CELL}>
        {finished && call ? (
          <>
            <OutcomeChip outcome={call.analysis_outcome} /> <ScoreCell score={call.score} provisional={call.provisional_score} />
          </>
        ) : (
          '—'
        )}
      </td>
      <td style={CELL}>
        {entry?.state === 'failed' ? (
          <FailureNotice kind={entry.kind} reason={entry.reason} keptPreviousResults={entry.keptPreviousResults} />
        ) : entry?.state === 'skipped' ? (
          <span style={{ fontSize: 'var(--text-sm)', color: 'var(--text-secondary)' }}>{entry.reason}</span>
        ) : call?.status === 'failed' ? (
          <FailureNotice kind={call.failure_kind} reason={call.failure_reason} />
        ) : null}
        <div style={{ display: 'flex', gap: '8px', marginTop: failed || entry?.state === 'skipped' ? '6px' : 0 }}>
          {call?.status === 'ready' && (
            <Link to={`/calls/${item.call_id}`} style={{ color: 'var(--brand)', fontWeight: 600 }}>
              Open review
            </Link>
          )}
          {retryable && (
            <button type="button" onClick={() => onRetry(item.call_id)} style={{ ...BUTTON, cursor: 'pointer' }}>
              Retry analysis
            </button>
          )}
        </div>
      </td>
    </tr>
  )
}

export function BatchAnalysisPanel({ accepted, headers, canRun, paceMs }: BatchAnalysisPanelProps) {
  const queue = useAnalysisQueue(headers, paceMs)

  // Only calls this session has not started are startable. Calls that already
  // ran (or are queued to run) are never re-queued by the start button.
  const startable = accepted.filter((a) => queue.entries[a.call_id] === undefined)
  const states = Object.values(queue.entries)
  const count = (s: QueueEntry['state']) => states.filter((e) => e.state === s).length
  const started = states.length > 0

  return (
    <div data-testid="batch-analysis-panel" style={{ marginTop: '16px' }}>
      {!canRun ? (
        <p role="note" data-testid="analysis-forbidden-note" style={{ fontSize: 'var(--text-sm)', color: 'var(--text-secondary)' }}>
          These calls are queued. Only a manager or head of collections can start analysis.
        </p>
      ) : (
        <div style={{ display: 'flex', alignItems: 'center', gap: '16px', flexWrap: 'wrap' }}>
          <button
            type="button"
            data-testid="start-analysis"
            onClick={() => queue.start(startable.map((a) => a.call_id))}
            disabled={queue.isRunning || startable.length === 0}
            style={{
              ...BUTTON,
              padding: '10px 20px',
              border: 'none',
              background: !queue.isRunning && startable.length > 0 ? 'var(--brand)' : 'var(--surface-2)',
              color: !queue.isRunning && startable.length > 0 ? '#fff' : 'var(--text-secondary)',
              cursor: !queue.isRunning && startable.length > 0 ? 'pointer' : 'not-allowed',
            }}
          >
            {queue.isRunning
              ? 'Analysis running…'
              : started && startable.length === 0
                ? 'Analysis started'
                : `Start analysis (${startable.length} ${startable.length === 1 ? 'call' : 'calls'})`}
          </button>
          <span style={{ fontSize: 'var(--text-sm)', color: 'var(--text-secondary)' }}>
            Calls run one at a time to respect provider limits, about a minute each. You can open a
            finished call while the others continue.
          </span>
        </div>
      )}

      {started && (
        <p data-testid="analysis-summary" style={{ marginTop: '12px', fontSize: 'var(--text-sm)' }}>
          {count('done')} completed · {count('failed')} failed · {count('running') + count('waiting')} in progress or
          waiting · {count('skipped')} not started{count('busy') > 0 ? ` · ${count('busy')} already being processed` : ''}
        </p>
      )}

      <div
        style={{
          marginTop: '12px',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius-card)',
          overflow: 'hidden',
          background: 'var(--surface-1)',
        }}
      >
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              {['Call', 'Status', 'Outcome / score', 'Details'].map((h) => (
                <th
                  key={h}
                  style={{
                    textAlign: 'left',
                    padding: '10px 14px',
                    fontSize: 'var(--text-xs)',
                    color: 'var(--text-secondary)',
                    borderBottom: '1px solid var(--border)',
                  }}
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {accepted.map((a) => (
              <BatchRow
                key={a.call_id}
                item={a}
                headers={headers}
                entry={queue.entries[a.call_id]}
                canRun={canRun}
                onRetry={queue.retry}
              />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
