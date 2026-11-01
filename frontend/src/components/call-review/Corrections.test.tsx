import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render, screen, cleanup, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { TranscriptPanel } from './TranscriptPanel'
import { AddFindingPanel, selectedText, wordSpans } from './AddFindingPanel'
import { FlagList } from './FlagList'
import { ApiError, apiFetch } from '../../api/client'
import type { TranscriptSegment, Violation } from '../../api/types'

vi.mock('../../api/client', async (original) => ({
  ...(await original<typeof import('../../api/client')>()),
  apiFetch: vi.fn(),
}))
vi.mock('../../api/violations', () => ({ useSubmitReview: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }) }))

const api = vi.mocked(apiFetch)
const headers = { role: 'manager', userId: 'u-mgr1' }

function rejectWith(error: Error) {
  api.mockImplementation(async () => {
    throw error
  })
}

beforeEach(() => {
  api.mockReset() // (not returned: a function returned from beforeEach is run as teardown)
})
afterEach(() => cleanup())

function wrap(ui: ReactNode) {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false, throwOnError: false } } })}>{ui}</QueryClientProvider>)
}

const seg = (over: Partial<TranscriptSegment> = {}): TranscriptSegment => ({
  id: 's-1', seq: 0, speaker: 'borrower', start_time: 0, end_time: 5, text: 'aap bewakoof ho bilkul', sentiment: null,
  speaker_confidence: 0.95, ai_speaker: 'borrower', speaker_corrected: false, corrected_by: null, corrected_at: null, correction_comment: null, ...over,
})

describe('speaker correction', () => {
  it('shows the control only to a reviewer', () => {
    const { unmount } = wrap(<TranscriptPanel segments={[seg()]} isLoading={false} currentTime={0} onSeek={() => {}} canCorrect callId="C1" headers={headers} />)
    expect(screen.getByTestId('correct-speaker-s-1')).toHaveTextContent('Mark as agent')
    unmount()
    wrap(<TranscriptPanel segments={[seg()]} isLoading={false} currentTime={0} onSeek={() => {}} />)
    expect(screen.queryByTestId('correct-speaker-s-1')).not.toBeInTheDocument() // a caller never gets it in the DOM
  })

  it('sends the corrected speaker and the optional reason', async () => {
    api.mockResolvedValue({ segment: seg({ speaker: 'agent', speaker_corrected: true }), call: { call_id: 'C1', score: 40 } })
    const user = userEvent.setup()
    wrap(<TranscriptPanel segments={[seg()]} isLoading={false} currentTime={0} onSeek={() => {}} canCorrect callId="C1" headers={headers} />)

    await user.click(screen.getByTestId('correct-speaker-s-1'))
    await user.type(screen.getByLabelText('Reason (optional)'), 'heard the agent')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(api).toHaveBeenCalledTimes(1))
    expect(api).toHaveBeenCalledWith('/api/calls/C1/segments/s-1/speaker', { method: 'POST', body: { speaker: 'agent', comment: 'heard the agent' }, headers })
  })

  it('shows the server error and keeps the form open', async () => {
    rejectWith(new ApiError(403, 'REVIEW_FORBIDDEN', 'Only a manager or head of collections may correct a speaker label.'))
    const user = userEvent.setup()
    wrap(<TranscriptPanel segments={[seg()]} isLoading={false} currentTime={0} onSeek={() => {}} canCorrect callId="C1" headers={headers} />)
    await user.click(screen.getByTestId('correct-speaker-s-1'))
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api).toHaveBeenCalledTimes(1))
    expect(await screen.findByRole('alert')).toHaveTextContent('Only a manager or head')
    expect(screen.getByRole('button', { name: 'Save' })).toBeInTheDocument()
  })

  it('distinguishes a corrected label from an AI prediction and shows who corrected it', () => {
    const corrected = seg({ speaker: 'agent', ai_speaker: 'borrower', speaker_corrected: true, corrected_by: 'Priya Sharma', corrected_at: '2026-10-30T12:00:00' })
    wrap(<TranscriptPanel segments={[corrected, seg({ id: 's-2', seq: 1 })]} isLoading={false} currentTime={0} onSeek={() => {}} />)
    const badge = screen.getByTestId('speaker-corrected')
    expect(badge).toHaveTextContent('Corrected by Priya Sharma')
    expect(badge).toHaveTextContent('AI said borrower')
    expect(screen.getAllByTestId('speaker-corrected')).toHaveLength(1) // the uncorrected segment carries no badge
  })
})

describe('manual finding', () => {
  const segments = [seg({ id: 's-a', speaker: 'agent', text: 'aap bewakoof ho bilkul', ai_speaker: 'agent' }), seg({ id: 's-b', seq: 1, speaker: 'borrower', text: 'nahi bataunga' })]

  it('tokenises words the way the server does, across scripts and spacing', () => {
    expect(wordSpans('a  b\tc').length).toBe(3)
    expect(selectedText('ہم آپ  کے گھر', 1, 2)).toBe('آپ  کے') // original spacing kept
    expect(selectedText('एक दो तीन', 2, 2)).toBe('तीन')
    expect(selectedText('एक दो', 0, 5)).toBe('')
  })

  it('offers R2-R6 and not R1', async () => {
    const user = userEvent.setup()
    wrap(<AddFindingPanel callId="C1" segments={segments} headers={headers} />)
    await user.click(screen.getByRole('button', { name: /Add Manual Finding/ }))
    const options = within(screen.getByLabelText('Rule')).getAllByRole('option').map((o) => o.textContent ?? '')
    expect(options.map((o) => o.slice(0, 2))).toEqual(['R2', 'R3', 'R4', 'R5', 'R6'])
  })

  it('previews the selected words and posts the word range', async () => {
    api.mockResolvedValue({ violation: { rule_code: 'R2' }, call: { call_id: 'C1', score: 40 } })
    const user = userEvent.setup()
    wrap(<AddFindingPanel callId="C1" segments={segments} headers={headers} />)
    await user.click(screen.getByRole('button', { name: /Add Manual Finding/ }))
    await user.selectOptions(screen.getByLabelText('Transcript segment'), 's-a')
    const save = screen.getByRole('button', { name: 'Save finding' })
    expect(save).toBeDisabled() // nothing selected, no comment

    const words = document.querySelectorAll('[data-word-index]')
    await user.click(words[1]) // first word of the evidence
    await user.click(words[2]) // last word
    expect(screen.getByTestId('selected-evidence')).toHaveTextContent('bewakoof ho')
    expect(save).toBeDisabled() // still needs a comment
    await user.type(screen.getByLabelText(/Why is this a violation/), 'Insults the borrower')
    expect(save).toBeEnabled()
    await user.click(save)

    await waitFor(() => expect(api).toHaveBeenCalledTimes(1))
    expect(api).toHaveBeenCalledWith('/api/calls/C1/violations', {
      method: 'POST', body: { rule_code: 'R2', segment_id: 's-a', word_start: 1, word_end: 2, comment: 'Insults the borrower' }, headers,
    })
    expect(await screen.findByTestId('manual-finding-added')).toHaveTextContent('Call score is now 40')
  })

  it('will not save evidence from a segment labelled as the borrower', async () => {
    const user = userEvent.setup()
    wrap(<AddFindingPanel callId="C1" segments={segments} headers={headers} />)
    await user.click(screen.getByRole('button', { name: /Add Manual Finding/ }))
    await user.selectOptions(screen.getByLabelText('Transcript segment'), 's-b')
    expect(screen.getByRole('alert')).toHaveTextContent('correct the speaker')
    expect(screen.getByRole('button', { name: 'Save finding' })).toBeDisabled()
    expect(document.querySelectorAll('[data-word-index]').length).toBe(0)
  })

  it('shows the server rejection', async () => {
    rejectWith(new ApiError(422, 'INVALID_EVIDENCE', 'Word range 0-9 is outside segment 0.'))
    const user = userEvent.setup()
    wrap(<AddFindingPanel callId="C1" segments={segments} headers={headers} />)
    await user.click(screen.getByRole('button', { name: /Add Manual Finding/ }))
    await user.selectOptions(screen.getByLabelText('Transcript segment'), 's-a')
    await user.click(document.querySelectorAll('[data-word-index]')[0])
    await user.type(screen.getByLabelText(/Why is this a violation/), 'x')
    await user.click(screen.getByRole('button', { name: 'Save finding' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('outside segment')
  })
})

describe('FlagList', () => {
  const base: Violation = {
    violation_id: 'v-m001', rule_code: 'R2', rule_name: 'No abusive language', severity: 'critical', segment_id: 's-a',
    start_time_seconds: 1, quote: 'bewakoof', explanation: 'Insult', confidence: null, needs_speaker_review: false,
    evidence_status: 'verified', evidence_reason: null, counts_toward_score: true, incident_group_id: null,
    overlapping_rule_codes: [], review: { decision: 'confirmed', reviewer: 'Priya', comment: 'Insult', reviewed_at: null },
  }

  it('labels a manually added finding and not an AI one', () => {
    wrap(
      <FlagList
        violations={[{ ...base, source: 'manual', created_by: 'Priya Sharma' }, { ...base, violation_id: 'v-1', source: 'ai' }]}
        isLoading={false} canReview={false} headers={null} onSeek={() => {}}
      />,
    )
    const badges = screen.getAllByTestId('manual-badge')
    expect(badges).toHaveLength(1)
    expect(badges[0]).toHaveTextContent('Added manually by Priya Sharma')
  })
})
