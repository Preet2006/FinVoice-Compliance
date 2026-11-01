import { useEffect, useRef } from 'react'
import { EmptyState } from '../ui/EmptyState'
import { SpeakerControl } from './SpeakerControl'
import type { RoleHeaders } from '../../api/client'
import type { TranscriptSegment } from '../../api/types'

interface TranscriptPanelProps {
  segments: TranscriptSegment[]
  isLoading: boolean
  currentTime: number
  onSeek: (seconds: number) => void
  // Manager / head only: show the Agent/Borrower correction control.
  canCorrect?: boolean
  callId?: string
  headers?: RoleHeaders | null
  // The segment the reviewer jumped to (a clicked segment or a clicked finding), and
  // the evidence words to mark inside it.
  focusedSegmentId?: string | null
  focusedQuote?: string | null
  onFocusSegment?: (segment: TranscriptSegment) => void
}

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

function formatSpeaker(speaker: TranscriptSegment['speaker']): string {
  return speaker === 'third_party' ? 'Third party' : speaker.charAt(0).toUpperCase() + speaker.slice(1)
}

export function isSpeakerUncertain(segment: Pick<TranscriptSegment, 'speaker_uncertain' | 'speaker_confidence'>): boolean {
  return Boolean(segment.speaker_uncertain) || segment.speaker_confidence === null || segment.speaker_confidence === undefined || segment.speaker_confidence < 0.6
}

// null/undefined -> "Unknown" (never shown as 0%). The value is the labelling
// model's own uncalibrated self-estimate, not a measured probability.
export function formatSpeakerConfidence(confidence: number | null | undefined): string {
  if (confidence === null || confidence === undefined || Number.isNaN(confidence)) return 'Unknown'
  return `${Math.round(confidence * 100)}%`
}

// The segment text, with the evidence words marked when they occur in it verbatim.
function SegmentText({ text, quote }: { text: string; quote?: string | null }) {
  const at = quote ? text.indexOf(quote) : -1
  if (!quote || at < 0) return <span dir="auto">{text}</span>
  return (
    <span dir="auto">
      {text.slice(0, at)}
      <mark data-testid="evidence-mark" style={{ background: 'var(--warning-bg)', color: 'inherit', padding: '0 2px' }}>
        {text.slice(at, at + quote.length)}
      </mark>
      {text.slice(at + quote.length)}
    </span>
  )
}

export function TranscriptPanel({
  segments, isLoading, currentTime, onSeek, canCorrect = false, callId, headers,
  focusedSegmentId = null, focusedQuote = null, onFocusSegment,
}: TranscriptPanelProps) {
  const focusedRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    // jsdom has no scrollIntoView; browsers do.
    focusedRef.current?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' })
  }, [focusedSegmentId])

  if (isLoading) {
    return <p style={{ color: 'var(--text-secondary)' }}>Loading transcript…</p>
  }

  if (segments.length === 0) {
    return <EmptyState title="No transcript yet" description="This call hasn't been processed yet." />
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
      {segments.map((seg) => {
        const isActive = currentTime >= seg.start_time && currentTime < seg.end_time
        const isFocused = seg.id === focusedSegmentId
        return (
          <div key={seg.id} ref={isFocused ? focusedRef : undefined} data-focused={isFocused}>
          <button
            onClick={() => {
              onSeek(seg.start_time)
              onFocusSegment?.(seg)
            }}
            data-segment-id={seg.id}
            data-active={isActive}
            style={{
              display: 'flex',
              gap: '12px',
              textAlign: 'left',
              padding: '8px 10px',
              border: 'none',
              borderLeft: seg.speaker_corrected ? '3px solid var(--brand)' : '3px solid transparent',
              borderRadius: 'var(--radius-input)',
              background: isFocused ? 'var(--warning-bg)' : isActive ? 'var(--surface-2)' : 'transparent',
              outline: isFocused ? '2px solid var(--medium)' : 'none',
              cursor: 'pointer',
              width: '100%',
            }}
          >
            <span
              className="font-mono"
              style={{ color: 'var(--text-secondary)', flexShrink: 0, width: '48px' }}
            >
              {formatTime(seg.start_time)}
            </span>
            <span style={{ flexShrink: 0, width: '120px', display: 'flex', flexDirection: 'column' }}>
              <span
                style={{
                  fontWeight: 600,
                  color: seg.speaker === 'agent' ? 'var(--brand)' : seg.speaker === 'unknown' || seg.speaker === 'mixed' ? 'var(--critical)' : 'var(--text-secondary)',
                }}
              >
                {formatSpeaker(seg.speaker)}
              </span>
              <span
                data-testid="speaker-confidence"
                title="Speaker label confidence: the model's own estimate, not a measured probability of being correct."
                style={{ fontSize: 'var(--text-xs)', color: 'var(--text-secondary)' }}
              >
                Confidence: {formatSpeakerConfidence(seg.speaker_confidence)}
              </span>
              {isSpeakerUncertain(seg) && (
                <span data-testid="speaker-uncertain" style={{ fontSize: 'var(--text-xs)', color: 'var(--critical)', fontWeight: 600 }}>
                  Needs speaker review
                </span>
              )}
              {!seg.speaker_corrected && (
                <span data-testid="speaker-source" style={{ fontSize: 'var(--text-xs)', color: 'var(--text-secondary)' }}>
                  AI label
                </span>
              )}
              {seg.speaker_corrected && (
                <span
                  data-testid="speaker-corrected"
                  title={seg.correction_comment ?? undefined}
                  style={{ fontSize: 'var(--text-xs)', color: 'var(--brand)', fontWeight: 600 }}
                >
                  Corrected by {seg.corrected_by ?? 'a reviewer'}
                  {seg.corrected_at ? ` · ${new Date(seg.corrected_at).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}` : ''}
                  {seg.ai_speaker ? ` · AI said ${seg.ai_speaker}` : ''}
                </span>
              )}
            </span>
            <SegmentText text={seg.text} quote={isFocused ? focusedQuote : null} />
          </button>
          {canCorrect && callId && headers && <SpeakerControl callId={callId} segment={seg} headers={headers} />}
          </div>
        )
      })}
    </div>
  )
}
