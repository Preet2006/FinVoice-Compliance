import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FlagList } from './FlagList'
import { ReprocessFailedNote } from './ReprocessFailedNote'
import { OutcomeChip, OUTCOME_LABELS } from '../ui/OutcomeChip'
import { ProvisionalNote, ScoreCell } from '../ui/ScoreCell'
import type { AnalysisOutcome, Violation } from '../../api/types'

const mutate = vi.fn()
const mutationState = { isPending: false, isError: false, error: null as unknown }

// The panel's network hook is replaced so the request body can be inspected.
vi.mock('../../api/violations', () => ({
  useSubmitReview: () => ({ mutate, ...mutationState }),
}))

beforeEach(() => {
  mutate.mockReset()
})
afterEach(() => cleanup())

function makeViolation(overrides: Partial<Violation> = {}): Violation {
  return {
    violation_id: 'v-1',
    rule_code: 'R3',
    rule_name: 'No threats or intimidation',
    severity: 'critical',
    segment_id: 's-1',
    start_time_seconds: 41.0,
    quote: 'the stored segment text',
    explanation: 'Agent threatens a home visit.',
    confidence: 0.9,
    needs_speaker_review: false,
    evidence_status: 'verified',
    evidence_reason: null,
    counts_toward_score: true,
    incident_group_id: null,
    overlapping_rule_codes: [],
    review: { decision: 'pending', reviewer: null, comment: null, reviewed_at: null },
    ...overrides,
  }
}

const unverified = (overrides: Partial<Violation> = {}) =>
  makeViolation({
    violation_id: 'v-u',
    evidence_status: 'unverified',
    evidence_reason: 'The quote does not appear in the cited segment. Proposed quote: “they will burn it”',
    counts_toward_score: false,
    ...overrides,
  })

function renderFlags(violations: Violation[], canReview = false, analysisOutcome?: AnalysisOutcome) {
  return render(
    <FlagList
      violations={violations}
      isLoading={false}
      canReview={canReview}
      headers={canReview ? { role: 'manager', userId: 'u-mgr1' } : null}
      onSeek={() => {}}
      analysisOutcome={analysisOutcome}
    />,
  )
}

describe('unverified evidence in the flag list', () => {
  it('marks a pending unverified finding and explains that it is not counted', () => {
    renderFlags([unverified()])
    expect(screen.getByTestId('unverified-evidence-badge')).toHaveTextContent('Unverified evidence')
    expect(screen.getByTestId('unverified-evidence-badge')).toHaveAttribute('data-unresolved', 'true')
    expect(screen.getByTestId('unverified-evidence-banner')).toHaveTextContent(
      /1 finding has evidence that could not be verified against the transcript and is not counted/,
    )
  })

  it('shows the stored transcript text, labelled, and the validation reason with the proposed quote', () => {
    renderFlags([unverified()])
    expect(screen.getByTestId('evidence-label')).toHaveTextContent('Stored transcript text')
    expect(screen.getByText(/the stored segment text/)).toBeInTheDocument()
    expect(screen.getByTestId('evidence-reason')).toHaveTextContent(/does not appear in the cited segment/)
    expect(screen.getByTestId('evidence-reason')).toHaveTextContent(/they will burn it/)
  })

  it('does not claim transcript evidence when the finding has no segment, and hides the Play button', () => {
    renderFlags([unverified({ segment_id: null, start_time_seconds: null, quote: '(no verifiable evidence)' })])
    expect(screen.getByTestId('evidence-label')).toHaveTextContent('No transcript evidence')
    expect(screen.queryByText('▶ Play')).not.toBeInTheDocument()
  })

  it('keeps Play for findings that have timing, including R1 at 0', () => {
    renderFlags([makeViolation({ start_time_seconds: 0, segment_id: null })])
    expect(screen.getByText('▶ Play')).toBeInTheDocument()
  })

  it('treats a resolved unverified finding as resolved: no banner, neutral badge', () => {
    renderFlags([unverified({ review: { decision: 'false_alarm', reviewer: 'P', comment: 'x', reviewed_at: null } })])
    expect(screen.getByTestId('unverified-evidence-badge')).toHaveTextContent('Unverified evidence — resolved')
    expect(screen.queryByTestId('unverified-evidence-banner')).not.toBeInTheDocument()
  })

  it('counts only the unresolved unverified findings in the banner', () => {
    renderFlags([unverified({ violation_id: 'a' }), unverified({ violation_id: 'b' }), makeViolation({ violation_id: 'c' })])
    expect(screen.getByTestId('unverified-evidence-banner')).toHaveTextContent(/2 findings have evidence/)
  })

  it('shows no unverified markup for ordinary verified findings', () => {
    renderFlags([makeViolation()])
    expect(screen.queryByTestId('unverified-evidence-badge')).not.toBeInTheDocument()
    expect(screen.queryByTestId('evidence-label')).not.toBeInTheDocument()
    expect(screen.queryByTestId('unverified-evidence-banner')).not.toBeInTheDocument()
  })

  it('keeps the speaker-review banner working next to the unverified banner', () => {
    renderFlags([unverified(), makeViolation({ violation_id: 'h', needs_speaker_review: true, counts_toward_score: false })])
    expect(screen.getByRole('note')).toHaveTextContent(/1 flag is awaiting speaker review/)
    expect(screen.getByTestId('unverified-evidence-banner')).toBeInTheDocument()
  })
})

describe('empty flag list and the analysis outcome', () => {
  it('says "No violations detected" only for a clean call', () => {
    renderFlags([], false, 'clean')
    expect(screen.getByText('No violations detected')).toBeInTheDocument()
  })

  it.each(['incomplete', 'failed', 'not_analyzed', 'pending_review'] as AnalysisOutcome[])(
    'does not present a %s call with no findings as clean',
    (outcome) => {
      renderFlags([], false, outcome)
      expect(screen.queryByText('No violations detected')).not.toBeInTheDocument()
      expect(screen.getByText('No findings recorded')).toBeInTheDocument()
      expect(screen.getByText(new RegExp(`not a verified clean result.*${OUTCOME_LABELS[outcome]}`))).toBeInTheDocument()
    },
  )
})

describe('confirming unverified evidence requires an audio attestation', () => {
  it('disables Confirm until the audio box is ticked AND a comment is written', async () => {
    const user = userEvent.setup()
    renderFlags([unverified()], true)
    const confirm = screen.getByText('Confirm') as HTMLButtonElement
    expect(confirm).toBeDisabled()

    await user.click(screen.getByRole('checkbox'))
    expect(confirm).toBeDisabled() // attested but no comment yet

    await user.type(screen.getByPlaceholderText(/comment/i), 'Heard the threat at 0:41')
    expect(confirm).not.toBeDisabled()
  })

  it('does not enable Confirm on a comment alone', async () => {
    const user = userEvent.setup()
    renderFlags([unverified()], true)
    await user.type(screen.getByPlaceholderText(/comment/i), 'looks right')
    expect(screen.getByText('Confirm')).toBeDisabled()
  })

  it('sends evidence_verified with the comment when confirming', async () => {
    const user = userEvent.setup()
    renderFlags([unverified()], true)
    await user.click(screen.getByRole('checkbox'))
    await user.type(screen.getByPlaceholderText(/comment/i), 'Heard the threat at 0:41')
    await user.click(screen.getByText('Confirm'))

    expect(mutate).toHaveBeenCalledWith({
      violationId: 'v-u',
      body: { decision: 'confirmed', comment: 'Heard the threat at 0:41', evidence_verified: true },
    })
  })

  it('lets a manager dismiss unverified evidence with just a comment', async () => {
    const user = userEvent.setup()
    renderFlags([unverified()], true)
    await user.type(screen.getByPlaceholderText(/comment/i), 'Not what was said')
    await user.click(screen.getByText('False alarm'))
    expect(mutate).toHaveBeenCalledWith({ violationId: 'v-u', body: { decision: 'false_alarm', comment: 'Not what was said' } })
  })

  it('leaves ordinary findings untouched: no checkbox, Confirm is immediately available', async () => {
    const user = userEvent.setup()
    renderFlags([makeViolation()], true)
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    await user.click(screen.getByText('Confirm'))
    expect(mutate).toHaveBeenCalledWith({ violationId: 'v-1', body: { decision: 'confirmed', comment: undefined } })
  })

  it('does not offer review controls to a caller', () => {
    renderFlags([unverified()], false)
    expect(screen.queryByText('Confirm')).not.toBeInTheDocument()
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
  })
})

describe('outcome, provisional score and failed-reprocess note', () => {
  it.each(Object.keys(OUTCOME_LABELS) as AnalysisOutcome[])('shows a distinct label for %s', (outcome) => {
    render(<OutcomeChip outcome={outcome} />)
    expect(screen.getByTestId('outcome-chip')).toHaveTextContent(OUTCOME_LABELS[outcome])
    expect(screen.getByTestId('outcome-chip')).toHaveAttribute('data-outcome', outcome)
  })

  it('uses a different label for every outcome', () => {
    expect(new Set(Object.values(OUTCOME_LABELS)).size).toBe(6)
  })

  it('marks a score provisional while findings remain unresolved', () => {
    render(<ScoreCell score={100} provisional />)
    expect(screen.getByText('100')).toBeInTheDocument()
    expect(screen.getByTestId('provisional-marker')).toHaveTextContent('provisional')
  })

  it('shows a final score plainly and a missing score as a dash', () => {
    const { rerender } = render(<ScoreCell score={85} provisional={false} />)
    expect(screen.queryByTestId('provisional-marker')).not.toBeInTheDocument()
    rerender(<ScoreCell score={null} provisional />)
    expect(screen.getByText('—')).toBeInTheDocument()
    expect(screen.queryByTestId('provisional-marker')).not.toBeInTheDocument()
  })

  it('explains exactly why a score is provisional, with both counts', () => {
    render(<ProvisionalNote unverified={2} pendingReview={1} />)
    expect(screen.getByTestId('provisional-note')).toHaveTextContent(
      /provisional: 2 findings with unverified evidence and 1 finding awaiting speaker review are not counted/,
    )
  })

  it('shows no provisional note when nothing is outstanding', () => {
    render(<ProvisionalNote unverified={0} pendingReview={0} />)
    expect(screen.queryByTestId('provisional-note')).not.toBeInTheDocument()
  })

  it('tells the manager a reprocess failed and the previous analysis is unchanged', () => {
    render(<ReprocessFailedNote failed reason="Reprocessing failed; the previous results were kept. The analysis provider timed out." />)
    expect(screen.getByTestId('reprocess-failed-note')).toHaveTextContent(/latest reprocessing attempt failed/)
    expect(screen.getByTestId('reprocess-failed-note')).toHaveTextContent(/previous successful run and has not been changed/)
    expect(screen.getByTestId('reprocess-failed-reason')).toHaveTextContent(/timed out/)
  })

  it('renders nothing when the latest attempt succeeded', () => {
    render(<ReprocessFailedNote failed={false} reason={null} />)
    expect(screen.queryByTestId('reprocess-failed-note')).not.toBeInTheDocument()
  })
})
