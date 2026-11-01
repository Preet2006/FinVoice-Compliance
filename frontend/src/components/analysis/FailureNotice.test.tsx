import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { FAILURE_DESCRIPTIONS, FailureNotice } from './FailureNotice'
import { CallInsights } from '../call-review/CallInsights'
import { ReprocessFailedNote } from '../call-review/ReprocessFailedNote'
import type { QueueFailureKind } from '../../api/analysis'

afterEach(cleanup)

const KINDS = Object.keys(FAILURE_DESCRIPTIONS) as QueueFailureKind[]

describe('FailureNotice', () => {
  it('gives every failure kind its own distinct title and advice', () => {
    const titles = KINDS.map((k) => FAILURE_DESCRIPTIONS[k].title)
    expect(new Set(titles).size).toBe(KINDS.length)
    for (const k of KINDS) expect(FAILURE_DESCRIPTIONS[k].advice.length).toBeGreaterThan(10)
  })

  it.each([
    ['rate_limit', /rate or usage limit/i],
    ['auth', /rejected the api key/i],
    ['not_configured', /no api key is configured/i],
    ['timeout', /timed out/i],
    ['network', /could not reach the server/i],
    ['unavailable', /unavailable/i],
  ] as const)('renders %s distinctly', (kind, title) => {
    render(<FailureNotice kind={kind} reason="safe message" />)
    expect(screen.getByTestId('failure-title')).toHaveTextContent(title)
    expect(screen.getByTestId('failure-notice')).toHaveAttribute('data-failure-kind', kind)
    expect(screen.getByTestId('failure-reason')).toHaveTextContent('safe message')
  })

  it('does not call a quota problem an authentication problem, or the reverse', () => {
    render(<FailureNotice kind="rate_limit" reason={null} />)
    expect(screen.getByTestId('failure-title')).not.toHaveTextContent(/api key/i)
    cleanup()
    render(<FailureNotice kind="auth" reason={null} />)
    expect(screen.getByTestId('failure-title')).not.toHaveTextContent(/rate|usage limit/i)
  })

  it('falls back to a generic message for an unknown or missing kind', () => {
    render(<FailureNotice kind={null} reason="boom" />)
    expect(screen.getByTestId('failure-title')).toHaveTextContent('Processing failed')
  })

  it('says when earlier results were kept', () => {
    render(<FailureNotice kind="timeout" reason="x" keptPreviousResults />)
    expect(screen.getByText(/previous analysis was kept unchanged/i)).toBeInTheDocument()
  })

  it('shows no reason line when there is none', () => {
    render(<FailureNotice kind="timeout" reason={null} />)
    expect(screen.queryByTestId('failure-reason')).not.toBeInTheDocument()
  })
})

describe('ReprocessFailedNote with a failure kind', () => {
  it('adds what kind of failure it was', () => {
    render(<ReprocessFailedNote failed kind="rate_limit" reason="limit reached" />)
    expect(screen.getByTestId('reprocess-failed-kind')).toHaveTextContent(/rate or usage limit/i)
    expect(screen.getByText(/previous successful run/i)).toBeInTheDocument()
  })

  it('still renders without a kind, and not at all when nothing failed', () => {
    render(<ReprocessFailedNote failed reason="x" />)
    expect(screen.queryByTestId('reprocess-failed-kind')).not.toBeInTheDocument()
    cleanup()
    render(<ReprocessFailedNote failed={false} kind="timeout" reason="x" />)
    expect(screen.queryByTestId('reprocess-failed-note')).not.toBeInTheDocument()
  })
})

describe('CallInsights — sentiment and calmness', () => {
  it.each([
    ['positive', 'Positive'],
    ['neutral', 'Neutral'],
    ['negative', 'Negative'],
  ] as const)('shows %s borrower sentiment', (sentiment, label) => {
    render(<CallInsights sentiment={sentiment} agentStayedCalm status="ready" />)
    expect(screen.getByTestId('sentiment-value')).toHaveTextContent(label)
  })

  it('shows whether the agent stayed calm, yes or no', () => {
    render(<CallInsights sentiment="negative" agentStayedCalm status="ready" />)
    expect(screen.getByTestId('calm-value')).toHaveTextContent('Yes')
    cleanup()
    render(<CallInsights sentiment="negative" agentStayedCalm={false} status="ready" />)
    expect(screen.getByTestId('calm-value')).toHaveTextContent('No')
  })

  it('shows "Not available" for missing values on an analysed call, never a default', () => {
    render(<CallInsights sentiment={null} agentStayedCalm={null} status="ready" />)
    expect(screen.getByTestId('sentiment-value')).toHaveTextContent('Not available')
    expect(screen.getByTestId('calm-value')).toHaveTextContent('Not available')
    expect(screen.queryByText('Neutral')).not.toBeInTheDocument()
    expect(screen.queryByText('Yes')).not.toBeInTheDocument()
  })

  it('says the values come after analysis for a call that has not been analysed', () => {
    render(<CallInsights sentiment={null} agentStayedCalm={null} status="queued" />)
    expect(screen.getByTestId('sentiment-value')).toHaveTextContent('After analysis')
    expect(screen.getByTestId('calm-value')).toHaveTextContent('After analysis')
  })

  it('treats "agent not calm" as different from "unknown"', () => {
    render(<CallInsights sentiment="neutral" agentStayedCalm={false} status="ready" />)
    expect(screen.getByTestId('calm-value')).toHaveTextContent('No')
    expect(screen.getByTestId('calm-value')).not.toHaveTextContent('Not available')
  })
})
