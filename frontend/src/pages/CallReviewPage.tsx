import { useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useParams, Link } from 'react-router-dom'
import { useCall, useCallTranscript, useCallViolations } from '../api/calls'
import { useCallAudioUrl } from '../api/media'
import { ApiError } from '../api/client'
import { ErrorState } from '../components/ui/ErrorState'
import { AnalysisAction } from '../components/analysis/AnalysisAction'
import { FailureNotice } from '../components/analysis/FailureNotice'
import { CallInsights } from '../components/call-review/CallInsights'
import { StatusChip } from '../components/ui/StatusChip'
import { OutcomeChip } from '../components/ui/OutcomeChip'
import { ProvisionalNote, ScoreCell } from '../components/ui/ScoreCell'
import { ReprocessFailedNote } from '../components/call-review/ReprocessFailedNote'
import { AudioPlayer, type AudioPlayerHandle } from '../components/audio/AudioPlayer'
import { TranscriptPanel } from '../components/call-review/TranscriptPanel'
import { FlagList } from '../components/call-review/FlagList'
import { AddFindingPanel } from '../components/call-review/AddFindingPanel'
import { CorrectionHistory } from '../components/call-review/CorrectionHistory'
import { ReviewCompletePanel } from '../components/call-review/ReviewCompletePanel'
import { useRoleContext } from '../context/RoleContext'

const PROCESSING_STATUSES = new Set(['transcribing', 'analyzing'])

export function CallReviewPage() {
  const { callId } = useParams<{ callId: string }>()
  const { role, activeUserId } = useRoleContext()
  const headers = activeUserId ? { role, userId: activeUserId } : null

  const [currentTime, setCurrentTime] = useState(0)
  // The transcript segment the reviewer jumped to (by clicking it or one of its findings).
  const [focus, setFocus] = useState<{ segmentId: string | null; quote: string | null }>({ segmentId: null, quote: null })
  const audioPlayerRef = useRef<AudioPlayerHandle>(null)

  const { data: call, isLoading: callLoading, isError: callError, error } = useCall(callId, headers)
  const { data: segments, isLoading: transcriptLoading } = useCallTranscript(callId, headers)
  const { data: violations, isLoading: violationsLoading } = useCallViolations(callId, headers)
  const audio = useCallAudioUrl(callId, headers, call?.has_audio ?? false)

  const seek = (seconds: number) => audioPlayerRef.current?.seekTo(seconds)

  // A call opened later must not inherit the previous call's highlight.
  useEffect(() => {
    setFocus({ segmentId: null, quote: null })
  }, [callId])

  const canReview = role !== 'caller'
  const canRunAnalysis = role === 'manager' || role === 'head'

  // When a call that was still processing becomes ready (started here, by
  // someone else, or in another tab), load its transcript and flags.
  const queryClient = useQueryClient()
  const previousStatus = useRef<string | undefined>(undefined)
  useEffect(() => {
    const status = call?.status
    if (status === 'ready' && previousStatus.current !== undefined && previousStatus.current !== 'ready') {
      queryClient.invalidateQueries({ queryKey: ['call-transcript'] })
      queryClient.invalidateQueries({ queryKey: ['call-violations'] })
    }
    previousStatus.current = status
  }, [call?.status, queryClient])

  return (
    <div>
      <Link to="/calls" style={{ color: 'var(--brand)', textDecoration: 'none' }}>
        ← Back to Calls
      </Link>

      {callLoading && <p style={{ color: 'var(--text-secondary)', marginTop: '16px' }}>Loading…</p>}

      {callError && (
        <div style={{ marginTop: '16px' }}>
          <ErrorState message={error instanceof ApiError ? error.message : 'Failed to load this call.'} />
        </div>
      )}

      {call && (
        <>
          <div style={{ marginTop: '12px', marginBottom: '16px' }}>
            <h1 style={{ fontSize: 'var(--text-2xl)', fontWeight: 600 }}>{call.call_id}</h1>
            <p style={{ color: 'var(--text-secondary)', marginTop: '4px' }}>
              {call.caller.name} · {call.office.name} ·{' '}
              {new Date(call.start_time).toLocaleString(undefined, {
                month: 'short',
                day: 'numeric',
                hour: 'numeric',
                minute: '2-digit',
              })}{' '}
              · {call.language ?? '—'} · {call.account_type} ·{' '}
              <StatusChip status={call.status} /> · <OutcomeChip outcome={call.analysis_outcome} /> · Score:{' '}
              <ScoreCell score={call.score} provisional={call.provisional_score} />
            </p>
            <ProvisionalNote
              unverified={call.findings_unverified}
              pendingReview={call.findings_pending_review}
            />
            <ReprocessFailedNote
              failed={call.last_reprocess_failed}
              kind={call.failure_kind}
              reason={call.failure_reason}
            />
            <CallInsights
              sentiment={call.sentiment}
              agentStayedCalm={call.agent_stayed_calm}
              status={call.status}
            />
            <AnalysisAction call={call} headers={headers} canRun={canRunAnalysis} />
            <ReviewCompletePanel call={call} headers={headers} canReview={canReview} />
          </div>

          {!call.has_audio && (
            <EmptyAudioNotice message="No recording available for this call (metadata only)." />
          )}

          {call.has_audio && PROCESSING_STATUSES.has(call.status) && (
            <EmptyAudioNotice message={`This call is still being processed (${call.status}). This page updates automatically.`} />
          )}

          {call.has_audio && call.status === 'queued' && (
            <EmptyAudioNotice message="This call has not been analysed yet." />
          )}

          {call.has_audio && call.status === 'failed' && (
            <FailureNotice kind={call.failure_kind} reason={call.failure_reason ?? 'Processing failed for this call.'} />
          )}

          {call.has_audio && call.status === 'ready' && (
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'minmax(280px, 40%) 1fr',
                gap: '24px',
                alignItems: 'start',
              }}
            >
              <div style={{ position: 'sticky', top: '16px' }}>
                <AudioPlayer
                  ref={audioPlayerRef}
                  src={audio.url}
                  isLoading={audio.isLoading}
                  error={audio.error}
                  onTimeUpdate={setCurrentTime}
                />
              </div>

              <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
                <section>
                  <h2 style={{ fontSize: 'var(--text-lg)', fontWeight: 600, marginBottom: '8px' }}>
                    Flags
                  </h2>
                  <FlagList
                    violations={violations ?? []}
                    isLoading={violationsLoading}
                    canReview={canReview}
                    headers={headers}
                    onSeek={seek}
                    onFocus={(v) => setFocus({ segmentId: v.segment_id, quote: v.evidence_status === 'verified' ? v.quote : null })}
                    analysisOutcome={call.analysis_outcome}
                  />
                  {canReview && headers && (
                    <>
                      <AddFindingPanel callId={call.call_id} segments={segments ?? []} headers={headers} />
                      <CorrectionHistory callId={call.call_id} headers={headers} />
                    </>
                  )}
                </section>

                <section>
                  <h2 style={{ fontSize: 'var(--text-lg)', fontWeight: 600, marginBottom: '8px' }}>
                    Transcript
                  </h2>
                  <TranscriptPanel
                    segments={segments ?? []}
                    isLoading={transcriptLoading}
                    currentTime={currentTime}
                    onSeek={seek}
                    canCorrect={canReview}
                    callId={call.call_id}
                    headers={headers}
                    focusedSegmentId={focus.segmentId}
                    focusedQuote={focus.quote}
                    onFocusSegment={(s) => setFocus({ segmentId: s.id, quote: null })}
                  />
                </section>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}

function EmptyAudioNotice({ message }: { message: string }) {
  return (
    <p
      style={{
        padding: '12px',
        background: 'var(--warning-bg)',
        borderRadius: 'var(--radius-input)',
        fontSize: 'var(--text-sm)',
      }}
    >
      {message}
    </p>
  )
}
