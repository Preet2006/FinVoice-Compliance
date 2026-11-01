import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render, screen, cleanup, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { CallListPage } from './CallListPage'
import type { CallListFilters, CallListItem } from '../api/types'

const seen: CallListFilters[] = []
const state: { items: CallListItem[]; total: number } = { items: [], total: 0 }
const identity = { role: 'manager' as 'manager' | 'head' | 'caller' }

vi.mock('../api/calls', () => ({
  useCalls: (filters: CallListFilters) => {
    seen.push(filters)
    return { data: { items: state.items, total: state.total, page: filters.page ?? 1, page_size: 25 }, isLoading: false, isError: false, error: null, refetch: vi.fn() }
  },
}))
vi.mock('../api/meta', () => ({
  useOffices: () => ({ data: [{ id: 'o-1', name: 'Jogeshwari' }] }),
  useUsers: () => ({ data: [{ id: 'u-1', name: 'Rahul Kumar', role: 'caller', office_id: 'o-1' }] }),
}))
vi.mock('../context/RoleContext', () => ({ useRoleContext: () => ({ role: identity.role, activeUserId: 'u-x' }) }))

const item = (over: Partial<CallListItem> = {}): CallListItem => ({
  call_id: 'CALL-1', caller: { id: 'u-1', name: 'Rahul Kumar' }, office: { id: 'o-1', name: 'Jogeshwari' }, start_time: '2026-10-04T10:10:00',
  duration_seconds: 75, language: 'hi', account_type: 'personal_loan', score: 70, status: 'ready', analysis_outcome: 'violations_found',
  counted_violations: 1, findings_pending_review: 0, findings_unverified: 0, provisional_score: false, last_reprocess_failed: false,
  review_status: 'pending_review', findings_total: 2, ...over,
})

function Where() {
  const l = useLocation()
  return <div data-testid="where">{l.pathname}{l.search}</div>
}

function renderPage(url = '/calls') {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path="/calls" element={<><CallListPage /><Where /></>} />
        <Route path="/calls/:callId" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  )
}
const last = () => seen[seen.length - 1]

beforeEach(() => {
  seen.length = 0
  identity.role = 'manager'
  state.items = [item(), item({ call_id: 'CALL-2', review_status: 'reviewed', score: 100, findings_total: 0, status: 'ready' }), item({ call_id: 'CALL-3', status: 'queued', score: null, review_status: 'not_applicable', findings_total: 0 })]
  state.total = 3
})
afterEach(() => cleanup())

describe('Manager call list', () => {
  it('shows the risk level of each analysed call and nothing for one that is not analysed', () => {
    state.items = [item({ top_severity: 'critical' }), item({ call_id: 'CALL-2', top_severity: null }), item({ call_id: 'CALL-3', status: 'queued', score: null, top_severity: null })]
    renderPage()
    const risk = (id: string) => within(screen.getByText(id).closest('tr') as HTMLElement)
    expect(risk('CALL-1').getByTestId('risk-chip')).toHaveAttribute('data-risk', 'critical')
    expect(risk('CALL-2').getByTestId('risk-chip')).toHaveAttribute('data-risk', 'none')
    expect(risk('CALL-3').queryByTestId('risk-chip')).not.toBeInTheDocument()
  })

  it('shows the fields a manager needs for each call', () => {
    renderPage()
    const row = screen.getByText('CALL-1').closest('tr') as HTMLElement
    expect(within(row).getByText('Rahul Kumar')).toBeInTheDocument() // caller
    expect(within(row).getByText('Jogeshwari')).toBeInTheDocument() // office
    expect(within(row).getByText('1m 15s')).toBeInTheDocument() // duration
    expect(within(row).getByText('Completed')).toBeInTheDocument() // analysis status
    expect(within(row).getByText('70')).toBeInTheDocument() // score
    expect(within(row).getByTestId('violation-count')).toHaveTextContent('2')
    expect(within(row).getByTestId('review-status')).toHaveTextContent('Pending review')
    const reviewed = screen.getByText('CALL-2').closest('tr') as HTMLElement
    expect(within(reviewed).getByTestId('review-status')).toHaveTextContent('Reviewed')
    const queued = screen.getByText('CALL-3').closest('tr') as HTMLElement
    expect(within(queued).getByText('Queued')).toBeInTheDocument()
    expect(within(queued).getByTestId('review-status')).toHaveTextContent('—') // not analysed, so nothing to review
  })

  it('has a clear Pending review view that filters the list and lives in the URL', async () => {
    const user = userEvent.setup()
    renderPage()
    expect(screen.getByTestId('tab-all')).toHaveAttribute('aria-selected', 'true')
    await user.click(screen.getByTestId('tab-pending_review'))
    expect(last().review_status).toBe('pending_review')
    expect(screen.getByTestId('tab-pending_review')).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByTestId('where')).toHaveTextContent('review_status=pending_review')
    await user.click(screen.getByTestId('tab-reviewed'))
    expect(last().review_status).toBe('reviewed')
    await user.click(screen.getByTestId('tab-all'))
    expect(last().review_status).toBeUndefined()
  })

  it('opens already filtered from a link (for example from the dashboard)', () => {
    renderPage('/calls?review_status=pending_review&analysis_status=completed&caller_id=u-1')
    expect(last()).toMatchObject({ review_status: 'pending_review', analysis_status: 'completed', caller_id: 'u-1' })
    expect(screen.getByTestId('tab-pending_review')).toHaveAttribute('aria-selected', 'true')
  })

  it('filters by analysis status, sorts, and searches by call id, caller or transcript', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.selectOptions(screen.getByLabelText('Analysis status'), 'processing')
    expect(last().analysis_status).toBe('processing')
    await user.selectOptions(screen.getByLabelText('Sort by'), 'risk')
    expect(last().sort).toBe('risk')
    await user.type(screen.getByLabelText('Search calls'), 'rahul')
    expect(last().q).toBe('rahul')
    expect(screen.getByPlaceholderText('Call ID, caller or transcript…')).toBeInTheDocument()
  })

  it('sends the picked dates as plain IST dates, never converted through UTC, and returns to page 1', async () => {
    const user = userEvent.setup()
    state.total = 80
    renderPage('/calls?page=3')
    expect(last().page).toBe(3)
    await user.type(screen.getByLabelText('From date'), '2026-10-12')
    expect(last().date_from).toBe('2026-10-12') // not "2026-10-12T00:00:00.000Z" and not shifted by the browser's zone
    expect(last().page).toBe(1)
    await user.type(screen.getByLabelText('To date'), '2026-10-12')
    expect(last().date_to).toBe('2026-10-12')
    for (const value of [last().date_from, last().date_to]) expect(value).not.toMatch(/T|Z|\+/)
    expect(screen.getByTestId('where')).toHaveTextContent('date_from=2026-10-12')
    expect(screen.getByTestId('where')).toHaveTextContent('date_to=2026-10-12')
  })

  it('applies dates from the URL as they are written', () => {
    renderPage('/calls?date_from=2026-10-01&date_to=2026-10-31')
    expect(last()).toMatchObject({ date_from: '2026-10-01', date_to: '2026-10-31' })
  })

  it('pages through a long list', async () => {
    const user = userEvent.setup()
    state.total = 80
    renderPage()
    expect(screen.getByText('Page 1 of 4')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Next' }))
    expect(last().page).toBe(2)
    expect(screen.getByText('Page 2 of 4')).toBeInTheDocument()
  })

  it('opens the call when a row is clicked', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(screen.getByText('CALL-1'))
    expect(screen.getByTestId('where')).toHaveTextContent('/calls/CALL-1')
  })

  it('says so when nothing is waiting for review', () => {
    state.items = []; state.total = 0
    renderPage('/calls?review_status=pending_review')
    expect(screen.getByText('Nothing is waiting for review')).toBeInTheDocument()
  })

  it('offers the office and caller filters to the head only', () => {
    const { unmount } = renderPage()
    expect(screen.queryByLabelText('Office')).not.toBeInTheDocument()
    unmount()
    identity.role = 'head'
    renderPage()
    expect(screen.getByLabelText('Office')).toBeInTheDocument()
    expect(screen.getByLabelText('Caller')).toBeInTheDocument()
  })
})
