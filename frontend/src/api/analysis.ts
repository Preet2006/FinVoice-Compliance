import { useCallback, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { ApiError, apiFetch, type RoleHeaders } from './client'
import type { CallStatus, FailureKind, ReprocessResponse } from './types'

// Pause between calls. The free analysis tier allows roughly one call's worth
// of tokens per ~20 seconds, so calls are run one at a time with a gap.
export const DEFAULT_PACE_MS = 15000

// Failures that every remaining call would hit too: the queue stops and the
// rest are left un-started (not failed) so no quota is wasted.
const STOP_THE_QUEUE: ReadonlySet<string> = new Set(['rate_limit', 'auth', 'not_configured'])

export type QueueFailureKind = FailureKind | 'network' | 'forbidden' | 'not_found'

export type QueueEntry =
  | { state: 'waiting' }
  | { state: 'running' }
  // Another request (this user or someone else) is already processing it.
  | { state: 'busy' }
  | { state: 'done'; status: CallStatus }
  | { state: 'failed'; kind: QueueFailureKind; reason: string; keptPreviousResults: boolean }
  // Not started because an earlier call hit a limit that would stop it too.
  | { state: 'skipped'; reason: string }

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

function describeError(error: unknown): { kind: QueueFailureKind; reason: string } {
  if (error instanceof ApiError) {
    if (error.status === 403) return { kind: 'forbidden', reason: error.message }
    if (error.status === 404) return { kind: 'not_found', reason: error.message }
    return { kind: 'other', reason: error.message }
  }
  return { kind: 'network', reason: 'Could not reach the server.' }
}

/**
 * Starts analysis of calls through the existing reprocess endpoint, one call
 * at a time. Nothing runs unless `start`/`retry` is called from a user action:
 * there is no effect that starts processing on mount, refresh or navigation.
 *
 * Duplicate protection: an id that is already waiting, running or done in this
 * session is ignored, so a double click or a second "Start" cannot queue it
 * twice. The backend independently rejects a concurrent run (HTTP 409).
 */
export function useAnalysisQueue(headers: RoleHeaders | null, paceMs: number = DEFAULT_PACE_MS) {
  const queryClient = useQueryClient()
  const [entries, setEntries] = useState<Record<string, QueueEntry>>({})
  const [isRunning, setIsRunning] = useState(false)

  const headersRef = useRef(headers)
  headersRef.current = headers
  const claimed = useRef<Set<string>>(new Set()) // waiting or running right now
  const finished = useRef<Set<string>>(new Set()) // completed successfully this session
  const pending = useRef<string[]>([])
  const draining = useRef(false)

  const setEntry = useCallback((id: string, entry: QueueEntry) => {
    setEntries((prev) => ({ ...prev, [id]: entry }))
  }, [])

  const refresh = useCallback(() => {
    for (const key of ['calls', 'call', 'call-transcript', 'call-violations']) {
      queryClient.invalidateQueries({ queryKey: [key] })
    }
  }, [queryClient])

  const runOne = useCallback(
    async (id: string): Promise<QueueFailureKind | null> => {
      setEntry(id, { state: 'running' })
      refresh() // pick up 'transcribing' as soon as the backend flips it
      try {
        const result = await apiFetch<ReprocessResponse>(`/api/calls/${id}/reprocess`, {
          method: 'POST',
          headers: headersRef.current ?? undefined,
        })
        if (result.failure_reason) {
          const kind: QueueFailureKind = result.failure_kind ?? 'other'
          setEntry(id, {
            state: 'failed',
            kind,
            reason: result.failure_reason,
            keptPreviousResults: result.status === 'ready',
          })
          return kind
        }
        finished.current.add(id)
        setEntry(id, { state: 'done', status: result.status })
        return null
      } catch (error) {
        if (error instanceof ApiError && error.status === 409 && error.code !== 'REVIEWER_WORK_EXISTS') {
          setEntry(id, { state: 'busy' })
          return null
        }
        if (error instanceof ApiError && error.code === 'REVIEWER_WORK_EXISTS') {
          // Reprocessing would erase reviewer work, so the server refused: say why, keep the call as it is.
          setEntry(id, { state: 'failed', kind: 'other', reason: error.message, keptPreviousResults: true })
          return 'other'
        }
        const { kind, reason } = describeError(error)
        setEntry(id, { state: 'failed', kind, reason, keptPreviousResults: false })
        return kind
      } finally {
        claimed.current.delete(id)
        refresh()
      }
    },
    [refresh, setEntry],
  )

  const drain = useCallback(async () => {
    if (draining.current) return
    draining.current = true
    setIsRunning(true)
    try {
      let first = true
      while (pending.current.length > 0) {
        const id = pending.current.shift() as string
        if (!first && paceMs > 0) await sleep(paceMs)
        first = false
        const failure = await runOne(id)
        if (failure && STOP_THE_QUEUE.has(failure)) {
          const reason =
            failure === 'rate_limit'
              ? 'Not started: the provider limit was reached. Retry after it resets.'
              : 'Not started: the provider is not usable until its API key is fixed.'
          for (const skipped of pending.current.splice(0)) {
            claimed.current.delete(skipped)
            setEntry(skipped, { state: 'skipped', reason })
          }
        }
      }
    } finally {
      draining.current = false
      setIsRunning(false)
    }
  }, [paceMs, runOne, setEntry])

  const enqueue = useCallback(
    (ids: string[]): number => {
      const fresh = ids.filter((id) => !claimed.current.has(id) && !finished.current.has(id))
      for (const id of fresh) {
        claimed.current.add(id)
        pending.current.push(id)
        setEntry(id, { state: 'waiting' })
      }
      if (fresh.length > 0) void drain()
      return fresh.length
    },
    [drain, setEntry],
  )

  return {
    entries,
    isRunning,
    /** Queue calls for analysis. Returns how many were actually queued. */
    start: enqueue,
    /** Retry one failed call (same authorised endpoint; manager/head only on the server). */
    retry: useCallback((id: string) => enqueue([id]), [enqueue]),
  }
}
