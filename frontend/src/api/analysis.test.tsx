import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, waitFor, act, cleanup } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { DEFAULT_PACE_MS, useAnalysisQueue } from './analysis'
import { FakeBackend, makeCall } from '../test/fakeBackend'

const holder = vi.hoisted(() => ({ backend: null as unknown as FakeBackend }))

vi.mock('./client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./client')>()
  return {
    ...actual,
    apiFetch: (path: string, options?: { method?: string }) => holder.backend.handler(path, options),
  }
})

const HEADERS = { role: 'manager', userId: 'u-mgr1' }

function setup(ids: string[] = ['A', 'B', 'C']) {
  holder.backend = new FakeBackend()
  for (const id of ids) holder.backend.add(makeCall(id))
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const invalidate = vi.spyOn(queryClient, 'invalidateQueries')
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  const hook = renderHook(() => useAnalysisQueue(HEADERS, 0), { wrapper })
  return { hook, backend: holder.backend, invalidate }
}

const settled = (hook: ReturnType<typeof setup>['hook']) =>
  waitFor(() => expect(hook.result.current.isRunning).toBe(false))

afterEach(() => cleanup())
beforeEach(() => {
  vi.clearAllMocks()
})

describe('useAnalysisQueue — nothing runs by itself', () => {
  it('makes no request on mount, re-render or refresh of the hook', () => {
    const { hook, backend } = setup()
    hook.rerender()
    hook.rerender()
    expect(backend.handler).not.toHaveBeenCalled()
    expect(hook.result.current.isRunning).toBe(false)
    expect(hook.result.current.entries).toEqual({})
  })

  it('uses a pause between calls by default to respect provider rate limits', () => {
    expect(DEFAULT_PACE_MS).toBe(15000)
  })
})

describe('useAnalysisQueue — running calls', () => {
  it('runs the calls strictly one at a time, in order', async () => {
    const { hook, backend } = setup()
    await act(async () => {
      hook.result.current.start(['A', 'B', 'C'])
    })
    await settled(hook)

    expect(backend.posts).toEqual(['A', 'B', 'C'])
    expect(backend.maxInFlight).toBe(1)
    expect(Object.values(hook.result.current.entries).map((e) => e.state)).toEqual(['done', 'done', 'done'])
  })

  it('shows waiting then running then done, and isRunning while it works', async () => {
    const { hook, backend } = setup(['A', 'B'])
    let release!: () => void
    backend.gate = new Promise<void>((resolve) => (release = resolve))

    await act(async () => {
      hook.result.current.start(['A', 'B'])
    })
    await waitFor(() => expect(hook.result.current.entries.A?.state).toBe('running'))
    expect(hook.result.current.entries.B?.state).toBe('waiting')
    expect(hook.result.current.isRunning).toBe(true)

    await act(async () => release())
    await settled(hook)
    expect(hook.result.current.entries.A?.state).toBe('done')
    expect(hook.result.current.entries.B?.state).toBe('done')
  })

  it('refreshes the call queries after each call so statuses and results update', async () => {
    const { hook, invalidate } = setup(['A'])
    await act(async () => {
      hook.result.current.start(['A'])
    })
    await settled(hook)
    const keys = invalidate.mock.calls.map((c) => (c[0] as { queryKey: string[] }).queryKey[0])
    expect(keys).toEqual(expect.arrayContaining(['calls', 'call', 'call-transcript', 'call-violations']))
  })
})

describe('useAnalysisQueue — duplicate submissions', () => {
  it('ignores a call that is already queued or running', async () => {
    const { hook, backend } = setup(['A'])
    backend.gate = new Promise<void>(() => {}) // never finishes: A stays running
    let first = 0
    let second = 0
    await act(async () => {
      first = hook.result.current.start(['A'])
      second = hook.result.current.start(['A'])
    })
    expect([first, second]).toEqual([1, 0])
    await waitFor(() => expect(backend.posts).toEqual(['A']))
    expect(hook.result.current.start(['A'])).toBe(0)
    expect(backend.posts).toEqual(['A']) // still a single request
  })

  it('never re-runs a call that already completed in this session', async () => {
    const { hook, backend } = setup(['A'])
    await act(async () => {
      hook.result.current.start(['A'])
    })
    await settled(hook)
    let again = 99
    await act(async () => {
      again = hook.result.current.start(['A'])
      again += hook.result.current.retry('A')
    })
    expect(again).toBe(0)
    expect(backend.posts).toEqual(['A'])
  })

  it('treats "already being processed" (409) as busy, not as a failure', async () => {
    const { hook, backend } = setup(['A', 'B'])
    backend.plans.set('A', { kind: 'http', status: 409, code: 'ALREADY_PROCESSING', message: 'busy' })
    await act(async () => {
      hook.result.current.start(['A', 'B'])
    })
    await settled(hook)
    expect(hook.result.current.entries.A?.state).toBe('busy')
    expect(hook.result.current.entries.B?.state).toBe('done') // the queue carried on
  })
})

describe('useAnalysisQueue — failures', () => {
  it.each(['rate_limit', 'auth', 'not_configured'] as const)(
    'stops after a %s failure and leaves the rest un-started (no wasted quota)',
    async (failure) => {
      const { hook, backend } = setup()
      backend.plans.set('A', { kind: 'fail', failure })
      await act(async () => {
        hook.result.current.start(['A', 'B', 'C'])
      })
      await settled(hook)

      expect(backend.posts).toEqual(['A']) // B and C were never sent
      const { A, B, C } = hook.result.current.entries
      expect(A).toMatchObject({ state: 'failed', kind: failure })
      expect(B?.state).toBe('skipped')
      expect(C?.state).toBe('skipped')
    },
  )

  it('lets a skipped call be started again later', async () => {
    const { hook, backend } = setup(['A', 'B'])
    backend.plans.set('A', { kind: 'fail', failure: 'rate_limit' })
    await act(async () => {
      hook.result.current.start(['A', 'B'])
    })
    await settled(hook)
    expect(hook.result.current.entries.B?.state).toBe('skipped')

    await act(async () => {
      hook.result.current.start(['B'])
    })
    await settled(hook)
    expect(hook.result.current.entries.B?.state).toBe('done')
  })

  it.each(['timeout', 'unavailable', 'invalid_response', 'internal', 'other'] as const)(
    'continues with the next call after a %s failure (it is specific to that call)',
    async (failure) => {
      const { hook, backend } = setup()
      backend.plans.set('A', { kind: 'fail', failure })
      await act(async () => {
        hook.result.current.start(['A', 'B', 'C'])
      })
      await settled(hook)
      expect(backend.posts).toEqual(['A', 'B', 'C'])
      expect(hook.result.current.entries.A).toMatchObject({ state: 'failed', kind: failure })
      expect(hook.result.current.entries.C?.state).toBe('done')
    },
  )

  it('reports the sanitized reason the backend gave', async () => {
    const { hook, backend } = setup(['A'])
    backend.plans.set('A', { kind: 'fail', failure: 'timeout', reason: 'The analysis provider timed out. Try again.' })
    await act(async () => {
      hook.result.current.start(['A'])
    })
    await settled(hook)
    expect(hook.result.current.entries.A).toMatchObject({ reason: 'The analysis provider timed out. Try again.' })
  })

  it('records that earlier results were kept when a re-run of an analysed call fails', async () => {
    const { hook, backend } = setup(['A'])
    backend.plans.set('A', { kind: 'fail', failure: 'rate_limit', keepResults: true })
    await act(async () => {
      hook.result.current.start(['A'])
    })
    await settled(hook)
    expect(hook.result.current.entries.A).toMatchObject({ state: 'failed', keptPreviousResults: true })
  })

  it('distinguishes an unreachable server from a provider failure', async () => {
    const { hook, backend } = setup(['A'])
    backend.plans.set('A', { kind: 'network' })
    await act(async () => {
      hook.result.current.start(['A'])
    })
    await settled(hook)
    expect(hook.result.current.entries.A).toMatchObject({ state: 'failed', kind: 'network' })
  })

  it('reports a permission refusal as forbidden', async () => {
    const { hook, backend } = setup(['A'])
    backend.plans.set('A', { kind: 'http', status: 403, code: 'REPROCESS_FORBIDDEN', message: 'Only a manager or head of collections may reprocess a call.' })
    await act(async () => {
      hook.result.current.start(['A'])
    })
    await settled(hook)
    expect(hook.result.current.entries.A).toMatchObject({ state: 'failed', kind: 'forbidden' })
  })

  it('allows a failed call to be retried, and a retry can succeed', async () => {
    const { hook, backend } = setup(['A'])
    backend.plans.set('A', { kind: 'fail', failure: 'timeout' })
    await act(async () => {
      hook.result.current.start(['A'])
    })
    await settled(hook)
    expect(hook.result.current.entries.A?.state).toBe('failed')

    backend.plans.set('A', { kind: 'ok' })
    await act(async () => {
      expect(hook.result.current.retry('A')).toBe(1)
    })
    await settled(hook)
    expect(hook.result.current.entries.A?.state).toBe('done')
    expect(backend.posts).toEqual(['A', 'A'])
  })
})
