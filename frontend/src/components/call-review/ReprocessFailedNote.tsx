import { FAILURE_DESCRIPTIONS } from '../analysis/FailureNotice'
import type { FailureKind } from '../../api/types'

interface ReprocessFailedNoteProps {
  failed: boolean
  // What kind of failure it was, when the backend reports one.
  kind?: FailureKind | null
  // The safe, already-redacted message from the API.
  reason: string | null
}

// Shown when the latest reprocessing attempt failed but the earlier valid
// analysis was kept: the results below are from the previous successful run.
export function ReprocessFailedNote({ failed, kind, reason }: ReprocessFailedNoteProps) {
  if (!failed) return null

  return (
    <div
      role="status"
      data-testid="reprocess-failed-note"
      style={{
        marginTop: '8px',
        padding: '8px 12px',
        background: 'var(--error-bg)',
        borderRadius: 'var(--radius-input)',
        fontSize: 'var(--text-sm)',
      }}
    >
      <strong>The latest reprocessing attempt failed.</strong> The analysis shown below is from the
      previous successful run and has not been changed.
      {kind && (
        <div data-testid="reprocess-failed-kind" style={{ marginTop: '4px' }}>
          {FAILURE_DESCRIPTIONS[kind].title}. {FAILURE_DESCRIPTIONS[kind].advice}
        </div>
      )}
      {reason && (
        <div data-testid="reprocess-failed-reason" style={{ marginTop: '4px', color: 'var(--text-secondary)' }}>
          {reason}
        </div>
      )}
    </div>
  )
}
