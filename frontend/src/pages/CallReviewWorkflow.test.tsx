import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render, screen, cleanup, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { CallReviewPage } from './CallReviewPage'
import { ApiError, apiFetch } from '../api/client'
import type { CallDetail, TranscriptSegment, Violation } from '../api/types'

const identity = { role: 'manager' as 'manager' | 'head' | 'caller' }
const data: { call: CallDetail; segments: TranscriptSegment[]; violations: Violation[] } = { call: {} as CallDetail, segments: [], violations: [] }

vi.mock('../api/client', async (original) => ({ ...(await original<typeof import('../api/client')>()), apiFetch: vi.fn() }))
vi.mock('../api/calls', () => ({
  useCall: () => ({ data: data.call, isLoading: false, isError: false, error: null }),
  useCallTranscript: () => ({ data: data.segments, isLoading: false }),
  useCallViolations: () => ({ data: data.violations, isLoading: false }),
}))
vi.mock('../api/media', () => ({ useCallAudioUrl: () => ({ url: 'blob:recording', isLoading: false, error: null }) }))
vi.mock('../context/RoleContext', () => ({ useRoleContext: () => ({ role: identity.role, activeUserId: 'u-mgr1' }) }))
const api = vi.mocked(apiFetch)

const seg = (over: Partial<TranscriptSegment> = {}): TranscriptSegment => ({
  id: 's-0', seq: 0, speaker: 'agent', start_time: 0, end_time: 4, text: 'Namaste main Pooja bol rahi hoon', sentiment: null, speaker_confidence: 0.95,
  ai_speaker: 'agent', speaker_corrected: false, ...over,
})
const finding = (over: Partial<Violation> = {}): Violation => ({
  violation_id: 'v-1', rule_code: 'R3', rule_name: 'No threats or intimidation', severity: 'critical', segment_id: 's-2', start_time_seconds: 8,
  quote: 'police bhej dunga', explanation: 'Threat', confidence: 0.9, needs_speaker_review: false, evidence_status: 'verified', evidence_reason: null,
  counts_toward_score: true, score_deduction: 30, incident_group_id: null, overlapping_rule_codes: [], source: 'ai',
  review: { decision: 'pending', reviewer: null, comment: null, reviewed_at: null }, ...over,
})
const call = (over: Partial<CallDetail> = {}): CallDetail => ({
  call_id: 'CALL-1', caller: { id: 'u-1', name: 'Rahul Kumar' }, office: { id: 'o-1', name: 'Jogeshwari' }, start_time: '2026-10-04T10:10:00',
  duration_seconds: 80, language: 'hi', account_type: 'personal_loan', score: 70, status: 'ready', analysis_outcome: 'violations_found',
  counted_violations: 1, findings_pending_review: 0, findings_unverified: 0, provisional_score: false, last_reprocess_failed: false,
  sentiment: 'neutral', agent_stayed_calm: true, has_audio: true, is_ground_truth: false, failure_reason: null, failure_kind: null,
  review_status: 'pending_review', findings_total: 1, ...over,
})

function renderPage() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={['/calls/CALL-1']}>
        <Routes>
          <Route path="/calls/:callId" element={<CallReviewPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}
const audio = () => document.querySelector('audio') as HTMLAudioElement

beforeEach(() => {
  api.mockReset()
  identity.role = 'manager'
  HTMLMediaElement.prototype.play = vi.fn(() => Promise.resolve())
  data.call = call()
  data.segments = [
    seg(),
    seg({ id: 's-1', seq: 1, speaker: 'borrower', ai_speaker: 'borrower', start_time: 4, end_time: 8, text: 'ہاں بولیے' }), // Urdu script
    seg({ id: 's-2', seq: 2, start_time: 8, end_time: 12, text: 'agar paisa nahi aaya to police bhej dunga aur ghar aayenge' }),
  ]
  data.violations = [finding()]
})
afterEach(() => cleanup())

describe('Call review: audio and transcript together', () => {
  it('shows the whole transcript with timestamps, speakers and where each label came from', () => {
    renderPage()
    for (const s of data.segments) expect(screen.getByText(s.text)).toBeInTheDocument() // every segment, not only findings
    expect(screen.getByText('ہاں بولیے')).toHaveAttribute('dir', 'auto') // right-to-left script reads correctly
    expect(screen.getAllByTestId('speaker-source')).toHaveLength(3) // each label says it is the AI's
    expect(screen.getByText('0:08')).toBeInTheDocument()
    expect(audio()).toHaveAttribute('src', 'blob:recording')
    expect(audio().hasAttribute('controls')).toBe(true) // play, pause, seek, current time and duration
  })

  it('clicking a transcript segment seeks the audio there and highlights it', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(screen.getByText('ہاں بولیے'))
    expect(audio().currentTime).toBe(4)
    expect(HTMLMediaElement.prototype.play).toHaveBeenCalled()
    const focused = document.querySelectorAll('[data-focused="true"]')
    expect(focused).toHaveLength(1)
    expect(focused[0].querySelector('[data-segment-id="s-1"]')).not.toBeNull()
  })

  it('clicking a finding seeks to its timestamp and marks the evidence in the transcript', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(screen.getByRole('button', { name: /Play/ }))
    expect(audio().currentTime).toBe(8)
    const mark = screen.getByTestId('evidence-mark')
    expect(mark).toHaveTextContent('police bhej dunga')
    expect(mark.closest('[data-focused="true"]')?.querySelector('[data-segment-id="s-2"]')).not.toBeNull()
  })

  it('does not invent a timestamp for a finding that has none', () => {
    data.violations = [finding({ segment_id: null, start_time_seconds: null, evidence_status: 'unverified', quote: '(no verifiable evidence)' })]
    renderPage()
    expect(screen.queryByRole('button', { name: /Play/ })).not.toBeInTheDocument()
  })

  it('shows each finding with its rule, evidence, severity, source, review state and score effect', () => {
    data.violations = [finding(), finding({ violation_id: 'v-2', rule_code: 'R2', rule_name: 'No abusive language', severity: 'critical', source: 'manual', created_by: 'Priya Sharma', score_deduction: 30, review: { decision: 'confirmed', reviewer: 'Priya Sharma', comment: 'x', reviewed_at: '2026-10-30T10:00:00' } }), finding({ violation_id: 'v-3', review: { decision: 'false_alarm', reviewer: 'Priya', comment: 'no', reviewed_at: null }, counts_toward_score: false, score_deduction: 0 })]
    renderPage()
    expect(screen.getAllByText(/R3 — No threats/).length).toBe(2)
    expect(screen.getAllByTestId('score-impact').map((e) => e.textContent)).toEqual([
      'Deducts 30 points from the score.', 'Deducts 30 points from the score.', 'Dismissed — no deduction.',
    ])
    expect(screen.getByTestId('manual-badge')).toHaveTextContent('Added manually by Priya Sharma')
  })

  it('shows corrected labels apart from AI labels', () => {
    data.segments = [seg({ speaker: 'borrower', ai_speaker: 'agent', speaker_corrected: true, corrected_by: 'Priya Sharma', corrected_at: '2026-10-30T10:00:00' })]
    renderPage()
    expect(screen.getByTestId('speaker-corrected')).toHaveTextContent('Corrected by Priya Sharma')
    expect(screen.queryByTestId('speaker-source')).not.toBeInTheDocument()
  })
})

describe('Call review: the manager workflow', () => {
  it('a clean call can be signed off without adding any finding', async () => {
    data.violations = []; data.call = call({ analysis_outcome: 'clean', score: 100, findings_total: 0 })
    api.mockResolvedValue({ call_id: 'CALL-1', review_status: 'reviewed', reviewed_by: 'Priya', reviewed_at: '2026-10-30T10:00:00', score: 100 })
    const user = userEvent.setup()
    renderPage()
    expect(screen.getByTestId('review-status')).toHaveTextContent('Pending review') // analysis is done, review is not
    await user.click(screen.getByTestId('complete-review'))
    await waitFor(() => expect(api).toHaveBeenCalledWith('/api/calls/CALL-1/review/complete', { method: 'POST', headers: { role: 'manager', userId: 'u-mgr1' } }))
  })

  it('shows why the review cannot be completed yet', async () => {
    api.mockRejectedValue(new ApiError(409, 'UNDECIDED_FINDINGS', '1 finding(s) still need a decision. Confirm each one or mark it a false alarm, then complete the review.'))
    const user = userEvent.setup()
    renderPage()
    await user.click(screen.getByTestId('complete-review'))
    expect(await screen.findByText(/still need a decision/)).toBeInTheDocument()
  })

  it('shows who signed a reviewed call off and no longer offers the button', () => {
    data.call = call({ review_status: 'reviewed', review_completed_by: 'Priya Sharma', review_completed_at: '2026-10-30T10:00:00' })
    renderPage()
    expect(screen.getByTestId('review-status')).toHaveTextContent('Reviewed')
    expect(screen.getByText(/Signed off by Priya Sharma/)).toBeInTheDocument()
    expect(screen.queryByTestId('complete-review')).not.toBeInTheDocument()
  })

  it('offers the correction, manual-finding and history tools to a manager', () => {
    renderPage()
    expect(screen.getByTestId('correct-speaker-s-0')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Add Manual Finding/ })).toBeInTheDocument()
    expect(screen.getByText('Correction history')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Confirm' })).toBeInTheDocument()
    expect(screen.getByTestId('reprocess-note')).toHaveTextContent('blocked')
  })

  it('gives a caller none of the review tools', () => {
    identity.role = 'caller'
    renderPage()
    for (const id of ['correct-speaker-s-0', 'complete-review', 'reprocess-note']) expect(screen.queryByTestId(id)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Add Manual Finding/ })).not.toBeInTheDocument()
    expect(screen.queryByText('Correction history')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Confirm' })).not.toBeInTheDocument()
    expect(screen.getByText(/Waiting for a manager to review/)).toBeInTheDocument()
    expect(screen.getByText('Namaste main Pooja bol rahi hoon')).toBeInTheDocument() // can still read the transcript
  })

  it('the head has the same review tools', () => {
    identity.role = 'head'
    renderPage()
    expect(screen.getByTestId('complete-review')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Add Manual Finding/ })).toBeInTheDocument()
  })
})
