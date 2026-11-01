import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render as plainRender, screen, cleanup } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import type { ReactElement } from 'react'
import { DashboardPage } from './DashboardPage'
import { ApiError } from '../api/client'
import type { Dashboard } from '../api/types'

const state: { data?: Dashboard; isLoading: boolean; isError: boolean; error: unknown } = { isLoading: false, isError: false, error: null }
const role = { value: 'head' as 'head' | 'manager' | 'caller', user: 'u-head' as string | null }

vi.mock('../api/dashboard', () => ({ useDashboard: () => ({ ...state, refetch: vi.fn() }) }))
vi.mock('../context/RoleContext', () => ({ useRoleContext: () => ({ role: role.value, activeUserId: role.user }) }))

const render = (ui: ReactElement) => plainRender(<MemoryRouter>{ui}</MemoryRouter>)

beforeEach(() => {
  state.data = undefined; state.isLoading = false; state.isError = false; state.error = null
  role.value = 'head'; role.user = 'u-head'
})
afterEach(() => cleanup())

const row = (over = {}) => ({ id: 'u-1', name: 'Imran Khan', calls: 2, violations: 3, counted: 2, average_score: 55, ...over })
const data = (over: Partial<Dashboard> = {}): Dashboard => ({
  scope: 'head', total_calls: 30, calls_analyzed: 10, calls_awaiting_review: 4, reviewed_calls: 6, verified_violations: 7, counted_violations: 5,
  unverified_findings: 2, average_score: 62.5,
  breaches_by_rule: ['R1', 'R2', 'R3', 'R4', 'R5', 'R6'].map((rule_code, i) => ({ rule_code, count: i })),
  breaches_by_caller: [row()], breaches_by_office: [row({ id: 'o-1', name: 'Jogeshwari' })], top_risky_callers: [row()],
  review_outcomes: { confirmed: 3, false_alarm: 1, pending: 6 },
  accuracy: { available: false, reason: 'Precision and recall need audio-verified ground-truth labels, and none are stored.' }, ...over,
})

describe('DashboardPage', () => {
  it('shows a loading state', () => {
    state.isLoading = true
    render(<DashboardPage />)
    expect(screen.getByText('Loading…')).toBeInTheDocument()
  })

  it('shows an error with a retry', () => {
    state.isError = true; state.error = new ApiError(500, 'X', 'The server fell over')
    render(<DashboardPage />)
    expect(screen.getByRole('alert')).toHaveTextContent('The server fell over')
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })

  it('shows an honest empty state when nothing has been analysed', () => {
    state.data = data({ calls_analyzed: 0, total_calls: 12 })
    render(<DashboardPage />)
    expect(screen.getByText('No analysed calls yet')).toBeInTheDocument()
    expect(screen.queryByTestId('breaches-by-rule')).not.toBeInTheDocument()
    expect(screen.queryByText('Average score')).not.toBeInTheDocument()
  })

  it('shows the stored numbers and nothing else', () => {
    state.data = data()
    render(<DashboardPage />)
    expect(screen.getByTestId('kpi-total-calls')).toHaveTextContent('30')
    expect(screen.getByTestId('kpi-calls-analyzed')).toHaveTextContent('10')
    expect(screen.getByTestId('kpi-pending-review')).toHaveTextContent('4')
    expect(screen.getByTestId('kpi-reviewed')).toHaveTextContent('6')
    expect(screen.getByTestId('kpi-verified-violations')).toHaveTextContent('7')
    expect(screen.getByTestId('kpi-verified-violations')).toHaveTextContent('5 counted in scores')
    expect(screen.getByTestId('kpi-unverified-findings')).toHaveTextContent('2')
    expect(screen.getByTestId('kpi-average-score')).toHaveTextContent('62.5')
    expect(screen.getByRole('img', { name: 'R5: 4' })).toBeInTheDocument()
    expect(screen.getByTestId('review-outcomes')).toHaveTextContent('Confirmed: 3')
    expect(screen.getByTestId('top-risky')).toHaveTextContent('Imran Khan')
    expect(screen.getByTestId('by-office')).toHaveTextContent('Jogeshwari')
  })

  it('links the headline numbers and the breakdown rows to the matching call list', () => {
    state.data = data()
    render(<DashboardPage />)
    const href = (testId: string) => screen.getByTestId(testId).closest('a')?.getAttribute('href')
    expect(href('kpi-pending-review')).toBe('/calls?review_status=pending_review')
    expect(href('kpi-reviewed')).toBe('/calls?review_status=reviewed')
    expect(href('kpi-calls-analyzed')).toBe('/calls?analysis_status=completed')
    expect(href('kpi-total-calls')).toBe('/calls')
    expect(screen.getByTestId('by-caller').querySelector('a')?.getAttribute('href')).toBe('/calls?caller_id=u-1')
    expect(screen.getByTestId('by-office').querySelector('a')?.getAttribute('href')).toBe('/calls?office_id=o-1')
  })

  it('says precision and recall are unavailable instead of inventing them', () => {
    state.data = data()
    render(<DashboardPage />)
    expect(screen.getByTestId('accuracy-note')).toHaveTextContent('Not available')
    expect(screen.getByTestId('accuracy-note')).toHaveTextContent('ground-truth')
    expect(screen.queryByText(/%/)).not.toBeInTheDocument()
  })

  it('shows a caller only their own view, without the caller and office breakdowns', () => {
    role.value = 'caller'; role.user = 'u-1'
    state.data = data({ scope: 'caller' })
    render(<DashboardPage />)
    expect(screen.getByText(/Your calls/)).toBeInTheDocument()
    expect(screen.queryByTestId('by-caller')).not.toBeInTheDocument()
    expect(screen.queryByTestId('by-office')).not.toBeInTheDocument()
  })

  it('asks for an identity before showing anything', () => {
    role.user = null
    render(<DashboardPage />)
    expect(screen.getByText('Select a user to see the dashboard.')).toBeInTheDocument()
  })
})

describe('DashboardPage evaluation results', () => {
  const metrics = {
    available: true, label: 'Script-derived evaluation metrics, not audio-verified real-world accuracy', reason: null, calls_scored: 20,
    per_rule: ['R1', 'R2', 'R3', 'R4', 'R5', 'R6'].map((rule_code) => ({ rule_code, tp: 2, fp: 1, fn: 0, tn: 17, precision: 0.667, recall: 1, f1: 0.8 })),
    notes: ['Labels are derived from the role-play scripts and are not audio-verified.'],
  }

  it('shows pending and failed counts next to the analysed count', () => {
    state.data = data({ calls_pending: 3, calls_failed: 2 })
    render(<DashboardPage />)
    expect(screen.getByTestId('kpi-pending-processing')).toHaveTextContent('3')
    expect(screen.getByTestId('kpi-failed')).toHaveTextContent('2')
  })

  it('shows per-rule precision, recall and F1 under the script-derived label', () => {
    state.data = data({ evaluation: metrics })
    render(<DashboardPage />)
    expect(screen.getByTestId('evaluation-label')).toHaveTextContent('Script-derived evaluation metrics, not audio-verified real-world accuracy')
    const r3 = screen.getByTestId('eval-row-R3')
    expect(r3).toHaveTextContent('0.67')
    expect(r3).toHaveTextContent('1.00')
    expect(r3).toHaveTextContent('0.80')
    expect(screen.getAllByTestId(/^eval-row-/)).toHaveLength(6)
  })

  it('shows an undefined figure as a dash, never as 0 or 1', () => {
    state.data = data({ evaluation: { ...metrics, per_rule: [{ rule_code: 'R4', tp: 0, fp: 0, fn: 0, tn: 20, precision: null, recall: null, f1: null }] } })
    render(<DashboardPage />)
    expect(screen.getByTestId('eval-row-R4').textContent).toMatch(/R4\s*0\s*0\s*0\s*20—\s*—\s*—|R4.*—.*—.*—/)
  })

  it('says why the metrics are unavailable instead of inventing them', () => {
    state.data = data({ evaluation: { available: false, label: metrics.label, reason: 'This database holds no analysed calls of the evaluation batch.', calls_scored: 0, per_rule: [], notes: [] } })
    render(<DashboardPage />)
    expect(screen.getByTestId('evaluation-metrics')).toHaveTextContent('Not available. This database holds no analysed calls')
    expect(screen.queryByTestId('eval-row-R1')).not.toBeInTheDocument()
  })

  it('shows no evaluation section when the backend sends none', () => {
    state.data = data()
    render(<DashboardPage />)
    expect(screen.queryByTestId('evaluation-metrics')).not.toBeInTheDocument()
  })
})
