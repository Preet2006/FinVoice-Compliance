import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BatchAnalysisPanel } from './BatchAnalysisPanel'
import { AnalysisAction } from './AnalysisAction'
import { FakeBackend, makeCall } from '../../test/fakeBackend'
import type { UploadAcceptedItem } from '../../api/types'

const holder = vi.hoisted(() => ({ backend: null as unknown as FakeBackend }))

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>()
  return {
    ...actual,
    apiFetch: (path: string, options?: { method?: string }) => holder.backend.handler(path, options),
  }
})

const HEADERS = { role: 'manager', userId: 'u-mgr1' }
const accepted = (...ids: string[]): UploadAcceptedItem[] =>
  ids.map((id) => ({ call_id: id, filename: `${id}.wav`, status: 'queued' }))

function renderPanel(ids: string[], canRun = true) {
  holder.backend = new FakeBackend()
  for (const id of ids) holder.backend.add(makeCall(id))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const utils = render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <BatchAnalysisPanel accepted={accepted(...ids)} headers={HEADERS} canRun={canRun} paceMs={0} />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return { ...utils, backend: holder.backend }
}

const row = (id: string) => within(screen.getByTestId(`batch-row-${id}`))

afterEach(cleanup)
beforeEach(() => {
  vi.clearAllMocks()
})

describe('BatchAnalysisPanel — starting analysis', () => {
  it('does not start anything when it loads or re-renders', async () => {
    const { backend, rerender } = renderPanel(['A', 'B'])
    await waitFor(() => expect(backend.gets).toContain('A'))
    rerender(<div />)
    expect(backend.posts).toEqual([])
  })

  it('shows each uploaded call as queued with a single clearly labelled start action', async () => {
    renderPanel(['A', 'B', 'C'])
    expect(screen.getByTestId('start-analysis')).toHaveTextContent('Start analysis (3 calls)')
    await waitFor(() => expect(row('A').getByText('Queued')).toBeInTheDocument())
    expect(row('B').getByText('Queued')).toBeInTheDocument()
  })

  it('runs every call after one click, then shows outcome, score and a link to review', async () => {
    const user = userEvent.setup()
    const { backend } = renderPanel(['A', 'B'])
    await user.click(screen.getByTestId('start-analysis'))

    await waitFor(() => expect(screen.getByTestId('analysis-summary')).toHaveTextContent('2 completed'))
    expect(backend.posts).toEqual(['A', 'B'])
    await waitFor(() => expect(row('A').getByText('Clean')).toBeInTheDocument())
    expect(row('A').getByRole('link', { name: 'Open review' })).toHaveAttribute('href', '/calls/A')
    expect(row('B').getByRole('link', { name: 'Open review' })).toHaveAttribute('href', '/calls/B')
  })

  it('cannot be started twice: the button is disabled while running and afterwards', async () => {
    const user = userEvent.setup()
    const { backend } = renderPanel(['A', 'B'])
    let release!: () => void
    backend.gate = new Promise<void>((resolve) => (release = resolve))

    const button = screen.getByTestId('start-analysis')
    await user.click(button)
    await waitFor(() => expect(button).toBeDisabled())
    expect(button).toHaveTextContent('Analysis running')
    await user.click(button)
    await user.click(button)
    release()
    await waitFor(() => expect(screen.getByTestId('analysis-summary')).toHaveTextContent('2 completed'))

    expect(backend.posts).toEqual(['A', 'B']) // one request per call, however often it was clicked
    expect(button).toBeDisabled()
    expect(button).toHaveTextContent('Analysis started')
  })

  it('shows calls that are waiting, then the one in progress', async () => {
    const user = userEvent.setup()
    const { backend } = renderPanel(['A', 'B'])
    backend.gate = new Promise<void>(() => {})
    await user.click(screen.getByTestId('start-analysis'))
    await waitFor(() => expect(row('B').getByTestId('row-state')).toHaveTextContent('Waiting in queue'))
    expect(screen.getByTestId('analysis-summary')).toHaveTextContent('2 in progress or waiting')
  })

  it('says a started call is starting at once, never a stale "Queued"', async () => {
    const user = userEvent.setup()
    const { backend } = renderPanel(['A'])
    backend.gate = new Promise<void>(() => {})
    await user.click(screen.getByTestId('start-analysis'))
    await waitFor(() => expect(row('A').getByTestId('row-state')).toHaveTextContent('Starting'))
    expect(row('A').queryByText('Queued')).not.toBeInTheDocument()
  })

  it('then shows the backend status (transcribing) once the status poll sees it', async () => {
    const user = userEvent.setup()
    const { backend } = renderPanel(['A'])
    backend.gate = new Promise<void>(() => {})
    await user.click(screen.getByTestId('start-analysis'))
    // the running row re-reads the call every few seconds
    await waitFor(() => expect(row('A').getByText('Transcribing')).toBeInTheDocument(), { timeout: 7000 })
  }, 10000)
})

describe('BatchAnalysisPanel — permissions', () => {
  it('offers no start button to a role that may not start analysis, and says why', () => {
    const { backend } = renderPanel(['A'], false)
    expect(screen.queryByTestId('start-analysis')).not.toBeInTheDocument()
    expect(screen.getByTestId('analysis-forbidden-note')).toHaveTextContent(/manager or head/i)
    expect(backend.posts).toEqual([])
  })

  it('offers no retry to such a role even for a failed call', async () => {
    holder.backend = new FakeBackend()
    holder.backend.add(makeCall('A', { status: 'failed', failure_kind: 'timeout', failure_reason: 'The analysis provider timed out. Try again.' }))
    render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
          <BatchAnalysisPanel accepted={accepted('A')} headers={HEADERS} canRun={false} paceMs={0} />
        </MemoryRouter>
      </QueryClientProvider>,
    )
    await waitFor(() => expect(row('A').getByTestId('failure-notice')).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: /retry/i })).not.toBeInTheDocument()
  })
})

describe('BatchAnalysisPanel — failures and retries', () => {
  it('shows the specific failure for a call that failed, and does not call it clean', async () => {
    const user = userEvent.setup()
    const { backend } = renderPanel(['A', 'B'])
    backend.plans.set('A', { kind: 'fail', failure: 'timeout', reason: 'The analysis provider timed out. Try again.' })
    await user.click(screen.getByTestId('start-analysis'))

    await waitFor(() => expect(row('A').getByTestId('failure-notice')).toHaveAttribute('data-failure-kind', 'timeout'))
    expect(row('A').queryByText('Clean')).not.toBeInTheDocument()
    expect(row('A').queryByRole('link', { name: 'Open review' })).not.toBeInTheDocument()
    expect(screen.getByTestId('analysis-summary')).toHaveTextContent('1 completed · 1 failed')
  })

  it('stops after a rate limit: later calls show "Not started" and are never sent', async () => {
    const user = userEvent.setup()
    const { backend } = renderPanel(['A', 'B', 'C'])
    backend.plans.set('A', { kind: 'fail', failure: 'rate_limit', reason: "The analysis provider's rate or usage limit was reached. Try again later." })
    await user.click(screen.getByTestId('start-analysis'))

    await waitFor(() => expect(row('C').getByTestId('row-state')).toHaveTextContent('Not started'))
    expect(backend.posts).toEqual(['A'])
    expect(row('A').getByTestId('failure-title')).toHaveTextContent(/rate or usage limit/i)
    expect(row('B').getByText(/provider limit was reached/i)).toBeInTheDocument()
    expect(screen.getByTestId('analysis-summary')).toHaveTextContent('2 not started')
  })

  it('tells a rejected API key apart from a rate limit', async () => {
    const user = userEvent.setup()
    const { backend } = renderPanel(['A'])
    backend.plans.set('A', { kind: 'fail', failure: 'auth' })
    await user.click(screen.getByTestId('start-analysis'))
    await waitFor(() => expect(row('A').getByTestId('failure-title')).toHaveTextContent(/rejected the api key/i))
    expect(row('A').getByTestId('failure-title')).not.toHaveTextContent(/rate/i)
  })

  it('offers Retry only for a failed call, and a retry runs just that call', async () => {
    const user = userEvent.setup()
    const { backend } = renderPanel(['A', 'B'])
    backend.plans.set('A', { kind: 'fail', failure: 'timeout' })
    await user.click(screen.getByTestId('start-analysis'))
    await waitFor(() => expect(row('A').getByRole('button', { name: 'Retry analysis' })).toBeInTheDocument())
    expect(row('B').queryByRole('button', { name: /retry/i })).not.toBeInTheDocument()

    backend.plans.set('A', { kind: 'ok', outcome: 'violations_found', score: 70 })
    await user.click(row('A').getByRole('button', { name: 'Retry analysis' }))

    await waitFor(() => expect(row('A').getByText('Violations found')).toBeInTheDocument())
    expect(backend.posts).toEqual(['A', 'B', 'A']) // B was not run again
    expect(row('A').getByRole('link', { name: 'Open review' })).toBeInTheDocument()
  })

  it('shows an unreachable server as a connection problem', async () => {
    const user = userEvent.setup()
    const { backend } = renderPanel(['A'])
    backend.plans.set('A', { kind: 'network' })
    await user.click(screen.getByTestId('start-analysis'))
    await waitFor(() => expect(row('A').getByTestId('failure-title')).toHaveTextContent(/could not reach the server/i))
  })

  it('shows an incomplete analysis as incomplete with a provisional score, never as clean', async () => {
    const user = userEvent.setup()
    const { backend } = renderPanel(['A'])
    backend.plans.set('A', { kind: 'ok', outcome: 'incomplete', score: 100 })
    await user.click(screen.getByTestId('start-analysis'))
    await waitFor(() => expect(row('A').getByText('Incomplete')).toBeInTheDocument())
    expect(row('A').queryByText('Clean')).not.toBeInTheDocument()
  })

  it('reports a call someone else is already processing without calling it an error', async () => {
    const user = userEvent.setup()
    const { backend } = renderPanel(['A'])
    backend.plans.set('A', { kind: 'http', status: 409, code: 'ALREADY_PROCESSING', message: 'busy' })
    await user.click(screen.getByTestId('start-analysis'))
    await waitFor(() => expect(row('A').getByTestId('row-state')).toHaveTextContent('Already being processed'))
    expect(row('A').queryByTestId('failure-notice')).not.toBeInTheDocument()
  })
})

describe('AnalysisAction — start or retry on one call', () => {
  function renderAction(call: ReturnType<typeof makeCall>, canRun = true) {
    holder.backend = new FakeBackend().add(call)
    return render(
      <QueryClientProvider client={new QueryClient()}>
        <AnalysisAction call={call} headers={HEADERS} canRun={canRun} paceMs={0} />
      </QueryClientProvider>,
    )
  }

  it('offers "Start analysis" for a queued call and runs only that call, once', async () => {
    const user = userEvent.setup()
    renderAction(makeCall('A'))
    const button = screen.getByTestId('analysis-action')
    expect(button).toHaveTextContent('Start analysis')
    await user.click(button)
    await user.click(button)
    await waitFor(() => expect(holder.backend.posts).toEqual(['A']))
  })

  it('offers "Retry analysis" for a failed call', () => {
    renderAction(makeCall('A', { status: 'failed', failure_kind: 'timeout' }))
    expect(screen.getByTestId('analysis-action')).toHaveTextContent('Retry analysis')
  })

  it.each(['ready', 'transcribing', 'analyzing'] as const)(
    'offers nothing for a call that is %s (it would replace results or double-run)',
    (status) => {
      renderAction(makeCall('A', { status }))
      expect(screen.queryByTestId('analysis-action')).not.toBeInTheDocument()
    },
  )

  it('offers nothing for a call with no recording', () => {
    renderAction(makeCall('A', { has_audio: false, status: 'metadata_only' }))
    expect(screen.queryByTestId('analysis-action')).not.toBeInTheDocument()
  })

  it('explains, instead of offering a button, to a role that may not run analysis', () => {
    renderAction(makeCall('A'), false)
    expect(screen.queryByTestId('analysis-action')).not.toBeInTheDocument()
    expect(screen.getByTestId('analysis-forbidden-note')).toHaveTextContent(/manager or head/i)
  })

  it('says when the backend reports the call is already being processed', async () => {
    const user = userEvent.setup()
    holder.backend = new FakeBackend().add(makeCall('A'), { kind: 'http', status: 409, code: 'ALREADY_PROCESSING', message: 'busy' })
    render(
      <QueryClientProvider client={new QueryClient()}>
        <AnalysisAction call={makeCall('A')} headers={HEADERS} canRun paceMs={0} />
      </QueryClientProvider>,
    )
    await user.click(screen.getByTestId('analysis-action'))
    await waitFor(() => expect(screen.getByTestId('analysis-busy')).toBeInTheDocument())
  })
})
