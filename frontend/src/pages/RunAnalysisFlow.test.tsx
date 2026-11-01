import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor, within, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { CallReviewPage } from './CallReviewPage'
import { UploadPage } from './UploadPage'
import { FakeBackend, makeCall } from '../test/fakeBackend'
import type { Role, UploadBatchResponse } from '../api/types'

const holder = vi.hoisted(() => ({
  backend: null as unknown as FakeBackend,
  role: 'manager' as Role,
  upload: null as unknown as () => Promise<UploadBatchResponse>,
}))

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return {
    ...actual,
    apiFetch: (path: string, options?: { method?: string }) => holder.backend.handler(path, options),
    apiUpload: () => holder.upload(),
  }
})
vi.mock('../api/media', () => ({
  useCallAudioUrl: () => ({ url: null, isLoading: false, error: null }),
}))
vi.mock('../context/RoleContext', () => ({
  useRoleContext: () => ({ role: holder.role, activeUserId: `u-${holder.role}` }),
}))

function renderReview(callId: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/calls/${callId}`]}>
        <Routes>
          <Route path="/calls/:callId" element={<CallReviewPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function renderUpload() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/upload']}>
        <Routes>
          <Route path="/upload" element={<UploadPage />} />
          <Route path="/calls/:callId" element={<CallReviewPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  holder.role = 'manager'
  holder.backend = new FakeBackend()
  vi.clearAllMocks()
})
afterEach(cleanup)

describe('Call review page — sentiment and calmness', () => {
  it('shows the borrower sentiment and whether the agent stayed calm', async () => {
    holder.backend.add(makeCall('CALL-1', { status: 'ready', score: 70, analysis_outcome: 'violations_found', sentiment: 'negative', agent_stayed_calm: false }))
    renderReview('CALL-1')
    await waitFor(() => expect(screen.getByTestId('sentiment-value')).toHaveTextContent('Negative'))
    expect(screen.getByTestId('calm-value')).toHaveTextContent('No')
  })

  it('shows "Not available" when an analysed call has no sentiment data', async () => {
    holder.backend.add(makeCall('CALL-1', { status: 'ready', score: 100, analysis_outcome: 'clean' }))
    renderReview('CALL-1')
    await waitFor(() => expect(screen.getByTestId('sentiment-value')).toHaveTextContent('Not available'))
    expect(screen.getByTestId('calm-value')).toHaveTextContent('Not available')
  })
})

describe('Call review page — starting and retrying analysis', () => {
  it('loading the page never starts analysis', async () => {
    holder.backend.add(makeCall('CALL-1'))
    renderReview('CALL-1')
    await screen.findByText('This call has not been analysed yet.')
    expect(holder.backend.posts).toEqual([])
  })

  it('lets a manager start a queued call, then shows the outcome, sentiment and calmness', async () => {
    const user = userEvent.setup()
    holder.backend.add(makeCall('CALL-1'), { kind: 'ok', outcome: 'clean', score: 100 })
    renderReview('CALL-1')

    await user.click(await screen.findByTestId('analysis-action'))

    await waitFor(() => expect(screen.getByTestId('outcome-chip')).toHaveAttribute('data-outcome', 'clean'))
    expect(holder.backend.posts).toEqual(['CALL-1'])
    expect(screen.getByTestId('sentiment-value')).toHaveTextContent('Neutral')
    expect(screen.queryByTestId('analysis-action')).not.toBeInTheDocument() // done: no second run offered
    expect(screen.queryByText('This call has not been analysed yet.')).not.toBeInTheDocument()
  })

  it('does not offer a caller the start action, and explains why', async () => {
    holder.role = 'caller'
    holder.backend.add(makeCall('CALL-1'))
    renderReview('CALL-1')
    await screen.findByText('This call has not been analysed yet.')
    expect(screen.queryByTestId('analysis-action')).not.toBeInTheDocument()
    expect(screen.getByTestId('analysis-forbidden-note')).toBeInTheDocument()
  })

  it('shows what kind of failure a failed call had, and lets a manager retry it', async () => {
    const user = userEvent.setup()
    holder.backend.add(
      makeCall('CALL-1', { status: 'failed', failure_kind: 'rate_limit', failure_reason: "The analysis provider's rate or usage limit was reached. Try again later." }),
      { kind: 'ok', outcome: 'violations_found', score: 70 },
    )
    renderReview('CALL-1')

    const notice = await screen.findByTestId('failure-notice')
    expect(notice).toHaveAttribute('data-failure-kind', 'rate_limit')
    expect(within(notice).getByTestId('failure-title')).toHaveTextContent(/rate or usage limit/i)
    expect(screen.queryByTestId('outcome-chip')).toHaveAttribute('data-outcome', 'not_analyzed')

    await user.click(screen.getByRole('button', { name: 'Retry analysis' }))
    await waitFor(() => expect(screen.getByTestId('outcome-chip')).toHaveAttribute('data-outcome', 'violations_found'))
    expect(screen.queryByTestId('failure-notice')).not.toBeInTheDocument()
  })

  it('a call that failed and a caller cannot retry shows the failure but no retry button', async () => {
    holder.role = 'caller'
    holder.backend.add(makeCall('CALL-1', { status: 'failed', failure_kind: 'auth', failure_reason: 'The analysis provider rejected the API key.' }))
    renderReview('CALL-1')
    expect(await screen.findByTestId('failure-title')).toHaveTextContent(/rejected the api key/i)
    expect(screen.queryByRole('button', { name: /retry/i })).not.toBeInTheDocument()
  })

  it('keeps showing the earlier analysis, with a note, when a re-run failed', async () => {
    holder.backend.add(
      makeCall('CALL-1', {
        status: 'ready', score: 70, analysis_outcome: 'violations_found', sentiment: 'negative', agent_stayed_calm: true,
        last_reprocess_failed: true, failure_kind: 'timeout',
        failure_reason: 'Reprocessing failed; the previous results were kept. The analysis provider timed out. Try again.',
      }),
    )
    renderReview('CALL-1')
    await screen.findByTestId('reprocess-failed-note')
    expect(screen.getByTestId('reprocess-failed-kind')).toHaveTextContent(/timed out/i)
    expect(screen.getByTestId('outcome-chip')).toHaveAttribute('data-outcome', 'violations_found')
    expect(screen.getByTestId('sentiment-value')).toHaveTextContent('Negative')
    expect(screen.queryByTestId('analysis-action')).not.toBeInTheDocument() // would replace the reviewed results
  })

  it('shows a call that is being processed as such and offers no start button', async () => {
    holder.backend.add(makeCall('CALL-1', { status: 'analyzing' }))
    renderReview('CALL-1')
    await screen.findByText(/still being processed \(analyzing\)/i)
    expect(screen.queryByTestId('analysis-action')).not.toBeInTheDocument()
  })
})

describe('Upload page — the full flow, with mocked pipeline and provider', () => {
  const files = () => [new File(['x'], 'a.wav', { type: 'audio/wav' }), new File(['y'], 'b.wav', { type: 'audio/wav' })]

  async function uploadBatch(result: UploadBatchResponse) {
    holder.upload = vi.fn().mockResolvedValue(result)
    renderUpload()
    const inputs = document.querySelectorAll<HTMLInputElement>('input[type="file"]')
    fireEvent.change(inputs[0], { target: { files: files() } })
    fireEvent.change(inputs[1], { target: { files: [new File(['id'], 'meta.csv', { type: 'text/csv' })] } })
    await userEvent.setup().click(screen.getByRole('button', { name: 'Start Processing Batch' }))
    await screen.findByTestId('batch-analysis-panel')
  }

  const BATCH: UploadBatchResponse = {
    batch_id: 'b-1',
    accepted: [
      { call_id: 'CALL-A', filename: 'a.wav', status: 'queued' },
      { call_id: 'CALL-B', filename: 'b.wav', status: 'queued' },
    ],
    rejected: [{ identifier: 'c.wav', reason: 'unknown_caller_id', detail: null }],
  }

  it('uploading does not start analysis; the calls wait for an explicit start', async () => {
    holder.backend.add(makeCall('CALL-A')).add(makeCall('CALL-B'))
    await uploadBatch(BATCH)
    expect(holder.backend.posts).toEqual([])
    expect(screen.getByTestId('start-analysis')).toHaveTextContent('Start analysis (2 calls)')
    expect(screen.queryByText(/not started in this phase/i)).not.toBeInTheDocument()
  })

  it('still reports rejected rows from the upload', async () => {
    holder.backend.add(makeCall('CALL-A')).add(makeCall('CALL-B'))
    await uploadBatch(BATCH)
    expect(screen.getByText(/Rejected — unknown_caller_id/)).toBeInTheDocument()
  })

  it('a manager starts the batch, watches it complete, and opens a call for review', async () => {
    const user = userEvent.setup()
    holder.backend
      .add(makeCall('CALL-A'), { kind: 'ok', outcome: 'violations_found', score: 70 })
      .add(makeCall('CALL-B'), { kind: 'fail', failure: 'timeout', reason: 'The analysis provider timed out. Try again.' })
    await uploadBatch(BATCH)

    // paceMs is the default (15 s) on the real page, so run it with fake timers' equivalent:
    // the first call finishes immediately; the second waits for the pause.
    await user.click(screen.getByTestId('start-analysis'))
    await waitFor(() => expect(within(screen.getByTestId('batch-row-CALL-A')).getByText('Violations found')).toBeInTheDocument())
    expect(holder.backend.posts).toEqual(['CALL-A'])
    expect(within(screen.getByTestId('batch-row-CALL-B')).getByTestId('row-state')).toHaveTextContent('Waiting in queue')

    await user.click(within(screen.getByTestId('batch-row-CALL-A')).getByRole('link', { name: 'Open review' }))
    await waitFor(() => expect(screen.getByRole('heading', { name: 'CALL-A' })).toBeInTheDocument())
    expect(screen.getByTestId('outcome-chip')).toHaveAttribute('data-outcome', 'violations_found')
    expect(screen.getByTestId('sentiment-value')).toHaveTextContent('Neutral')
  }, 15000)

  it('a caller cannot reach the upload form at all (upload is manager and head only)', async () => {
    holder.role = 'caller'
    holder.upload = vi.fn()
    renderUpload()
    expect(screen.getByTestId('upload-denied')).toBeInTheDocument()
    expect(document.querySelectorAll('input[type="file"]')).toHaveLength(0)
    expect(screen.queryByRole('button', { name: 'Start Processing Batch' })).not.toBeInTheDocument()
    expect(holder.upload).not.toHaveBeenCalled()
    expect(holder.backend.posts).toEqual([])
  })

  it('shows no analysis panel when every row was rejected', async () => {
    holder.upload = vi.fn().mockResolvedValue({ batch_id: 'b-2', accepted: [], rejected: [{ identifier: 'a.wav', reason: 'no_metadata_row', detail: null }] })
    renderUpload()
    const inputs = document.querySelectorAll<HTMLInputElement>('input[type="file"]')
    fireEvent.change(inputs[0], { target: { files: files() } })
    fireEvent.change(inputs[1], { target: { files: [new File(['id'], 'meta.csv', { type: 'text/csv' })] } })
    await userEvent.setup().click(screen.getByRole('button', { name: 'Start Processing Batch' }))
    await screen.findByText(/Rejected — no_metadata_row/)
    expect(screen.queryByTestId('batch-analysis-panel')).not.toBeInTheDocument()
  })
})
