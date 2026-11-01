import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { TranscriptPanel, formatSpeakerConfidence, isSpeakerUncertain } from './TranscriptPanel'
import type { TranscriptSegment } from '../../api/types'

const segments: TranscriptSegment[] = [
  { id: 's-1', seq: 0, speaker: 'agent', start_time: 0, end_time: 5, text: 'Namaste', sentiment: null, speaker_confidence: 0.95 },
  { id: 's-2', seq: 1, speaker: 'borrower', start_time: 5, end_time: 10, text: 'Ji boliye', sentiment: null, speaker_confidence: null },
]

describe('TranscriptPanel', () => {
  it('clicking a segment calls onSeek with that segment start_time', async () => {
    const user = userEvent.setup()
    const onSeek = vi.fn()
    render(<TranscriptPanel segments={segments} isLoading={false} currentTime={0} onSeek={onSeek} />)

    await user.click(screen.getByText('Ji boliye'))
    expect(onSeek).toHaveBeenCalledWith(5)
  })

  it('highlights the segment under the current playhead time', () => {
    render(<TranscriptPanel segments={segments} isLoading={false} currentTime={7} onSeek={() => {}} />)
    const active = screen.getByText('Ji boliye').closest('button')
    const inactive = screen.getByText('Namaste').closest('button')
    expect(active).toHaveAttribute('data-active', 'true')
    expect(inactive).toHaveAttribute('data-active', 'false')
  })

  it('shows an empty state when there are no segments', () => {
    render(<TranscriptPanel segments={[]} isLoading={false} currentTime={0} onSeek={() => {}} />)
    expect(screen.getByText('No transcript yet')).toBeInTheDocument()
  })

  it('shows speaker confidence as a percentage when available and "Unknown" when null', () => {
    render(<TranscriptPanel segments={segments} isLoading={false} currentTime={0} onSeek={() => {}} />)
    const labels = screen.getAllByTestId('speaker-confidence')
    expect(labels).toHaveLength(2)
    expect(labels[0]).toHaveTextContent('Confidence: 95%')
    expect(labels[1]).toHaveTextContent('Confidence: Unknown')
  })

  it('does not render a missing confidence as 0%', () => {
    render(<TranscriptPanel segments={[segments[1]]} isLoading={false} currentTime={0} onSeek={() => {}} />)
    expect(screen.queryByText(/0%/)).not.toBeInTheDocument()
  })

  it('renders third-party and mixed labels with explicit uncertainty', () => {
    render(<TranscriptPanel segments={[{
      id: 's-3', seq: 2, speaker: 'mixed', start_time: 10, end_time: 15,
      text: 'overlapping speech', sentiment: null, speaker_confidence: 0.5,
      speaker_uncertain: true,
    }]} isLoading={false} currentTime={0} onSeek={() => {}} />)
    expect(screen.getByText('Mixed')).toBeInTheDocument()
    expect(screen.getByTestId('speaker-uncertain')).toHaveTextContent('Needs speaker review')
  })

  it.each([
    ['Agent', 'agent'],
    ['Borrower', 'borrower'],
    ['Third party', 'third_party'],
    ['Unknown', 'unknown'],
    ['Mixed', 'mixed'],
  ] as const)('renders the %s speaker state', (label, speaker) => {
    render(<TranscriptPanel segments={[{
      id: `state-${speaker}`, seq: 0, speaker, start_time: 0, end_time: 1,
      text: 'state', sentiment: null, speaker_confidence: 0.95,
    }]} isLoading={false} currentTime={0} onSeek={() => {}} />)
    expect(screen.getByText(label)).toBeInTheDocument()
  })

  it('marks missing and low confidence as uncertain even without the persisted flag', () => {
    expect(isSpeakerUncertain({ speaker_uncertain: false, speaker_confidence: null })).toBe(true)
    expect(isSpeakerUncertain({ speaker_uncertain: false, speaker_confidence: 0.59 })).toBe(true)
    expect(isSpeakerUncertain({ speaker_uncertain: false, speaker_confidence: 0.6 })).toBe(false)
  })
})

describe('formatSpeakerConfidence', () => {
  it.each([
    [0.95, '95%'],
    [0.6, '60%'],
    [1, '100%'],
    [0, '0%'], // a real 0.0 is a value, distinct from "unknown"
    [0.856, '86%'],
    [null, 'Unknown'],
    [undefined, 'Unknown'],
    [Number.NaN, 'Unknown'],
  ])('formats %s as %s', (input, expected) => {
    expect(formatSpeakerConfidence(input as number | null | undefined)).toBe(expected)
  })
})
