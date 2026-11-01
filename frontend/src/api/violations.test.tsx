import { describe, it, expect, vi, afterEach } from 'vitest'
import { renderHook, waitFor, act, cleanup } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { useSubmitReview } from './violations'

vi.mock('./client', () => ({
  apiFetch: vi.fn().mockResolvedValue({
    violation_id: 'v-1',
    review: { decision: 'false_alarm', reviewer: 'Priya', comment: 'x', reviewed_at: null },
    call: { call_id: 'CALL-1', score: 70 },
  }),
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('useSubmitReview', () => {
  it('refetches the violations (group status) and the call (score) after a review', async () => {
    const queryClient = new QueryClient()
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries')
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )

    const { result } = renderHook(() => useSubmitReview({ role: 'manager', userId: 'u-mgr1' }), { wrapper })
    await act(async () => {
      result.current.mutate({ violationId: 'v-1', body: { decision: 'false_alarm', comment: 'x' } })
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['call-violations'] })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['call'] })
  })
})
