import { describe, it, expect, vi, afterEach } from 'vitest'
import { renderHook, waitFor, act, cleanup } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { useAnalysisQueue } from './analysis'
import { ApiError, apiFetch } from './client'

vi.mock('./client', async (original) => ({ ...(await original<typeof import('./client')>()), apiFetch: vi.fn() }))
const api = vi.mocked(apiFetch)
afterEach(() => {
  cleanup()
  api.mockReset()
})

const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>

describe('a refused reprocess', () => {
  it('explains that reviewer work blocked it instead of calling the call "busy"', async () => {
    const message = 'This call has reviewer work that reprocessing would erase (1 corrected speaker label(s)). Reprocessing is blocked to protect that work.'
    api.mockImplementation(async () => {
      throw new ApiError(409, 'REVIEWER_WORK_EXISTS', message)
    })
    const { result } = renderHook(() => useAnalysisQueue({ role: 'manager', userId: 'u-1' }, 0), { wrapper })
    await act(async () => {
      result.current.start(['CALL-1'])
    })
    await waitFor(() => expect(result.current.entries['CALL-1']?.state).toBe('failed'))
    expect(result.current.entries['CALL-1']).toMatchObject({ state: 'failed', reason: message, keptPreviousResults: true })
  })

  it('still treats an ordinary 409 as "already being processed"', async () => {
    api.mockImplementation(async () => {
      throw new ApiError(409, 'ALREADY_PROCESSING', 'Call CALL-2 is already being processed.')
    })
    const { result } = renderHook(() => useAnalysisQueue({ role: 'manager', userId: 'u-1' }, 0), { wrapper })
    await act(async () => {
      result.current.start(['CALL-2'])
    })
    await waitFor(() => expect(result.current.entries['CALL-2']?.state).toBe('busy'))
  })
})
