import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { FlagList } from './FlagList'
import type { Violation } from '../../api/types'
import * as violationsApi from '../../api/violations'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

function makeViolation(overrides: Partial<Violation> = {}): Violation {
  return {
    violation_id: 'v-1',
    rule_code: 'R3',
    rule_name: 'No threats or intimidation',
    severity: 'critical',
    segment_id: 's-1',
    start_time_seconds: 41.0,
    quote: 'hum aapke ghar bhi aayenge',
    explanation: 'Agent threatens a home visit.',
    confidence: 0.93,
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

function renderWithQueryClient(children: React.ReactNode) {
  const queryClient = new QueryClient()
  return render(<QueryClientProvider client={queryClient}>{children}</QueryClientProvider>)
}

describe('FlagList / ReviewPanel', () => {
  it('does not render review controls at all for a caller (canReview=false)', () => {
    renderWithQueryClient(
      <FlagList
        violations={[makeViolation()]}
        isLoading={false}
        canReview={false}
        headers={null}
        onSeek={() => {}}
      />,
    )
    expect(screen.queryByText('Confirm')).not.toBeInTheDocument()
    expect(screen.queryByText('False alarm')).not.toBeInTheDocument()
  })

  it('renders review controls for a manager (canReview=true)', () => {
    renderWithQueryClient(
      <FlagList
        violations={[makeViolation()]}
        isLoading={false}
        canReview={true}
        headers={{ role: 'manager', userId: 'u-mgr1' }}
        onSeek={() => {}}
      />,
    )
    expect(screen.getByText('Confirm')).toBeInTheDocument()
    expect(screen.getByText('False alarm')).toBeInTheDocument()
  })

  it('disables False alarm submit until a comment is entered', async () => {
    const user = userEvent.setup()
    renderWithQueryClient(
      <FlagList
        violations={[makeViolation()]}
        isLoading={false}
        canReview={true}
        headers={{ role: 'manager', userId: 'u-mgr1' }}
        onSeek={() => {}}
      />,
    )
    const falseAlarmButton = screen.getByText('False alarm') as HTMLButtonElement
    expect(falseAlarmButton).toBeDisabled()

    const textarea = screen.getByPlaceholderText(/comment/i)
    await user.type(textarea, 'Not actually a threat')
    expect(falseAlarmButton).not.toBeDisabled()
  })

  it('"▶ Play" calls onSeek with the violation start_time_seconds', async () => {
    const user = userEvent.setup()
    const onSeek = vi.fn()
    renderWithQueryClient(
      <FlagList
        violations={[makeViolation({ start_time_seconds: 41.0 })]}
        isLoading={false}
        canReview={false}
        headers={null}
        onSeek={onSeek}
      />,
    )
    await user.click(screen.getByText('▶ Play'))
    expect(onSeek).toHaveBeenCalledWith(41.0)
  })

  it('R1 violation (start_time_seconds=0.0) seeks to 0', async () => {
    const user = userEvent.setup()
    const onSeek = vi.fn()
    renderWithQueryClient(
      <FlagList
        violations={[makeViolation({ rule_code: 'R1', start_time_seconds: 0.0, segment_id: null })]}
        isLoading={false}
        canReview={false}
        headers={null}
        onSeek={onSeek}
      />,
    )
    await user.click(screen.getByText('▶ Play'))
    expect(onSeek).toHaveBeenCalledWith(0)
  })

  it('after a successful review submission, buttons are replaced by decision text', () => {
    // Simulate the post-review state directly: once the backend reports a
    // non-pending decision, ReviewPanel renders the confirmation text
    // instead of the Confirm/False-alarm buttons.
    const reviewed = makeViolation({
      review: {
        decision: 'confirmed',
        reviewer: 'Priya Sharma',
        comment: null,
        reviewed_at: '2026-10-28T11:02:00+05:30',
      },
    })
    renderWithQueryClient(
      <FlagList
        violations={[reviewed]}
        isLoading={false}
        canReview={true}
        headers={{ role: 'manager', userId: 'u-mgr1' }}
        onSeek={() => {}}
      />,
    )
    expect(screen.queryByText('Confirm')).not.toBeInTheDocument()
    expect(screen.queryByText('False alarm')).not.toBeInTheDocument()
    expect(screen.getByText(/Confirmed by Priya Sharma/)).toBeInTheDocument()
  })
})

describe('FlagList / speaker-review badge', () => {
  const reviewedBy = (decision: 'confirmed' | 'false_alarm') => ({
    decision,
    reviewer: 'Priya Sharma',
    comment: null,
    reviewed_at: '2026-10-28T11:02:00+05:30',
  })

  function renderFlags(violations: Violation[], canReview = false) {
    return renderWithQueryClient(
      <FlagList
        violations={violations}
        isLoading={false}
        canReview={canReview}
        headers={canReview ? { role: 'manager', userId: 'u-mgr1' } : null}
        onSeek={() => {}}
      />,
    )
  }

  it('shows no badge and no score note when needs_speaker_review is false', () => {
    renderFlags([makeViolation({ needs_speaker_review: false })])
    expect(screen.queryByTestId('speaker-review-badge')).not.toBeInTheDocument()
    expect(screen.queryByRole('note')).not.toBeInTheDocument()
  })

  it('shows "Needs speaker review" for a flagged pending violation', () => {
    renderFlags([makeViolation({ needs_speaker_review: true })])
    const badge = screen.getByTestId('speaker-review-badge')
    expect(badge).toHaveTextContent('Needs speaker review')
    expect(badge).toHaveAttribute('data-awaiting', 'true')
  })

  it('explains that awaiting flags are excluded from the score until reviewed', () => {
    renderFlags([
      makeViolation({ violation_id: 'v-1', needs_speaker_review: true }),
      makeViolation({ violation_id: 'v-2', needs_speaker_review: true }),
    ])
    expect(screen.getByRole('note')).toHaveTextContent(
      /2 flags are awaiting speaker review and are not counted in the call score until reviewed/,
    )
  })

  it('uses singular wording for a single awaiting flag', () => {
    renderFlags([makeViolation({ needs_speaker_review: true })])
    expect(screen.getByRole('note')).toHaveTextContent(
      /1 flag is awaiting speaker review and is not counted in the call score/,
    )
  })

  it('does not call a confirmed flagged violation "needs review" and drops the exclusion note', () => {
    renderFlags([makeViolation({ needs_speaker_review: true, review: reviewedBy('confirmed') })])
    const badge = screen.getByTestId('speaker-review-badge')
    expect(badge).toHaveTextContent('Speaker attribution reviewed')
    expect(badge).not.toHaveTextContent('Needs speaker review')
    expect(badge).toHaveAttribute('data-awaiting', 'false')
    expect(screen.queryByRole('note')).not.toBeInTheDocument()
  })

  it('treats a false-alarm flagged violation as reviewed, not awaiting', () => {
    renderFlags([makeViolation({ needs_speaker_review: true, review: reviewedBy('false_alarm') })])
    expect(screen.getByTestId('speaker-review-badge')).toHaveAttribute('data-awaiting', 'false')
    expect(screen.queryByRole('note')).not.toBeInTheDocument()
  })

  it('counts only the awaiting flags when mixed with other violations', () => {
    renderFlags([
      makeViolation({ violation_id: 'v-1', needs_speaker_review: true }),
      makeViolation({ violation_id: 'v-2', needs_speaker_review: false }),
      makeViolation({ violation_id: 'v-3', needs_speaker_review: true, review: reviewedBy('confirmed') }),
    ])
    expect(screen.getAllByTestId('speaker-review-badge')).toHaveLength(2)
    expect(screen.getByRole('note')).toHaveTextContent(/1 flag is awaiting speaker review/)
  })

  it('still offers Confirm / False alarm on a flagged pending violation for a manager', () => {
    renderFlags([makeViolation({ needs_speaker_review: true })], true)
    expect(screen.getByTestId('speaker-review-badge')).toBeInTheDocument()
    expect(screen.getByText('Confirm')).toBeInTheDocument()
    expect(screen.getByText('False alarm')).toBeInTheDocument()
  })
})

describe('FlagList / incident groups', () => {
  const GROUP = 'inc-R3-s-12'

  function renderFlags(violations: Violation[], canReview = false) {
    return renderWithQueryClient(
      <FlagList
        violations={violations}
        isLoading={false}
        canReview={canReview}
        headers={canReview ? { role: 'manager', userId: 'u-mgr1' } : null}
        onSeek={() => {}}
      />,
    )
  }

  const fragment = (id: string, counts: boolean, overrides: Partial<Violation> = {}) =>
    makeViolation({
      violation_id: id,
      incident_group_id: GROUP,
      counts_toward_score: counts,
      ...overrides,
    })

  it('shows no group note or summary when no flag belongs to an incident', () => {
    renderFlags([makeViolation(), makeViolation({ violation_id: 'v-2' })])
    expect(screen.queryByTestId('incident-group-note')).not.toBeInTheDocument()
    expect(screen.queryByTestId('incident-group-summary')).not.toBeInTheDocument()
  })

  it('explains that the carrier is counted once and the fragment is not counted separately', () => {
    renderFlags([fragment('v-a', true), fragment('v-b', false)])
    const notes = screen.getAllByTestId('incident-group-note')
    expect(notes).toHaveLength(2)
    expect(notes[0]).toHaveTextContent('Part of one incident (2 flags) — counted once in the score.')
    expect(notes[1]).toHaveTextContent(/not counted separately; another flag in this incident carries the deduction/)
    expect(screen.getByTestId('incident-group-summary')).toHaveTextContent(
      /not every flag below deducts separately/,
    )
  })

  it('does not claim the incident is counted when no row in it counts', () => {
    renderFlags([
      fragment('v-a', false, { needs_speaker_review: true }),
      fragment('v-b', false, { needs_speaker_review: true }),
    ])
    for (const note of screen.getAllByTestId('incident-group-note')) {
      expect(note).toHaveTextContent(/this incident is not currently counted in the score/)
      expect(note).not.toHaveTextContent(/counted once/)
    }
  })

  it('shows no group note on a dismissed fragment but keeps it on the others', () => {
    const dismissed = fragment('v-a', false, {
      review: { decision: 'false_alarm', reviewer: 'Priya Sharma', comment: 'no', reviewed_at: null },
    })
    renderFlags([dismissed, fragment('v-b', true)])
    const notes = screen.getAllByTestId('incident-group-note')
    expect(notes).toHaveLength(1)
    expect(notes[0]).toHaveTextContent('(2 flags) — counted once in the score')
  })

  it('keeps every evidence row and its review controls', () => {
    renderFlags([fragment('v-a', true), fragment('v-b', false)], true)
    expect(screen.getAllByText('Confirm')).toHaveLength(2)
    expect(screen.getAllByText('False alarm')).toHaveLength(2)
    expect(screen.getAllByText('▶ Play')).toHaveLength(2)
  })

  it('keeps the speaker-review badge and banner working alongside a group note', () => {
    renderFlags([
      fragment('v-a', true),
      fragment('v-b', false, { needs_speaker_review: true }),
    ])
    expect(screen.getByTestId('speaker-review-badge')).toHaveTextContent('Needs speaker review')
    expect(screen.getByRole('note')).toHaveTextContent(/1 flag is awaiting speaker review/)
    expect(screen.getAllByTestId('incident-group-note')).toHaveLength(2)
  })

  it('surfaces R3/R4 overlap on both rows without implying either is suppressed', () => {
    renderFlags([
      makeViolation({ violation_id: 'v-r3', rule_code: 'R3', overlapping_rule_codes: ['R4'] }),
      makeViolation({ violation_id: 'v-r4', rule_code: 'R4', overlapping_rule_codes: ['R3'] }),
    ])
    const notes = screen.getAllByTestId('rule-overlap-note')
    expect(notes).toHaveLength(2)
    expect(notes[0]).toHaveTextContent('also flagged under R4')
    expect(notes[1]).toHaveTextContent('also flagged under R3')
    expect(notes[0]).toHaveTextContent('scored independently')
    expect(screen.queryByTestId('incident-group-note')).not.toBeInTheDocument()
  })

  it('hides the overlap note once the row is dismissed', () => {
    renderFlags([
      makeViolation({
        overlapping_rule_codes: ['R4'],
        review: { decision: 'false_alarm', reviewer: null, comment: null, reviewed_at: null },
      }),
    ])
    expect(screen.queryByTestId('rule-overlap-note')).not.toBeInTheDocument()
  })
})

describe('useSubmitReview wiring sanity', () => {
  it('module exports the expected hook', () => {
    expect(typeof violationsApi.useSubmitReview).toBe('function')
  })
})
