import type { CallStatus } from './types'

// Statuses in which the backend is actively working on a call.
export const IN_PROGRESS_STATUSES: ReadonlySet<CallStatus> = new Set(['transcribing', 'analyzing'])

export function isInProgress(status: CallStatus | undefined): boolean {
  return status !== undefined && IN_PROGRESS_STATUSES.has(status)
}

// How often a view re-reads a call that is being processed.
export const STATUS_POLL_MS = 3000
