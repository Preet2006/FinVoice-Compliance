import { describe, it, expect, vi, afterEach } from 'vitest'
import { renderHook, waitFor, act, cleanup } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { useAddManualFinding, useCorrectSpeaker } from './corrections'
import { ApiError, apiFetch } from './client'

vi.mock('./client', async (original) => ({ ...(await original<typeof import('./client')>()), apiFetch: vi.fn() }))
const api = vi.mocked(apiFetch)
const headers = { role: 'manager', userId: 'u-mgr1' }

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function setup() {
  const queryClient = new QueryClient()
  const invalidate = vi.spyOn(queryClient, 'invalidateQueries')
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  return { invalidate, wrapper }
}

describe('reviewer correction hooks', () => {
  it('a speaker correction refetches the transcript, findings, call (score) and history', async () => {
    api.mockResolvedValue({ segment: {}, call: { call_id: 'C1', score: 70 } })
    const { invalidate, wrapper } = setup()
    const { result } = renderHook(() => useCorrectSpeaker('C1', headers), { wrapper })
    await act(async () => { result.current.mutate({ segmentId: 's-1', speaker: 'agent', comment: '  ' }) })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(api).toHaveBeenCalledWith('/api/calls/C1/segments/s-1/speaker', { method: 'POST', body: { speaker: 'agent', comment: undefined }, headers })
    const keys = invalidate.mock.calls.map((c) => (c[0] as { queryKey: string[] }).queryKey[0])
    expect(keys).toEqual(expect.arrayContaining(['call-transcript', 'call-violations', 'call', 'call-audit', 'dashboard']))
  })

  it('a manual finding refetches the same data', async () => {
    api.mockResolvedValue({ violation: {}, call: { call_id: 'C1', score: 40 } })
    const { invalidate, wrapper } = setup()
    const { result } = renderHook(() => useAddManualFinding('C1', headers), { wrapper })
    await act(async () => { result.current.mutate({ rule_code: 'R2', segment_id: 's-1', word_start: 0, word_end: 1, comment: 'c' }) })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(invalidate).toHaveBeenCalled()
  })

  it('a rejected request becomes an error state and does not refetch anything', async () => {
    api.mockImplementation(async () => { throw new ApiError(422, 'INVALID_EVIDENCE', 'Word range is outside the segment.') })
    const { invalidate, wrapper } = setup()
    const { result } = renderHook(() => useAddManualFinding('C1', headers), { wrapper })
    await act(async () => { result.current.mutate({ rule_code: 'R2', segment_id: 's-1', word_start: 0, word_end: 99, comment: 'c' }) })
    await waitFor(() => expect(result.current.isError).toBe(true))
    expect((result.current.error as ApiError).code).toBe('INVALID_EVIDENCE')
    expect(invalidate).not.toHaveBeenCalled()
  })

  it('refuses to send anything without an identity', async () => {
    const { wrapper } = setup()
    const { result } = renderHook(() => useCorrectSpeaker('C1', null), { wrapper })
    await act(async () => { result.current.mutate({ segmentId: 's-1', speaker: 'agent' }) })
    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(api).not.toHaveBeenCalled()
  })
})
