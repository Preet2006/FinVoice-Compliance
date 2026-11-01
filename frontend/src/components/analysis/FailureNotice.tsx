import type { QueueFailureKind } from '../../api/analysis'

interface Description {
  title: string
  advice: string
}

// What each kind of failure means for the person looking at it. The message
// itself (`reason`) is already redacted by the backend; these add what to do.
export const FAILURE_DESCRIPTIONS: Record<QueueFailureKind, Description> = {
  rate_limit: {
    title: 'Provider rate or usage limit reached',
    advice: 'Wait for the limit to reset, then retry. This is not a problem with the recording.',
  },
  auth: {
    title: 'The provider rejected the API key',
    advice: 'The configured key is invalid or expired. Fix it in the backend configuration; retrying will fail until then.',
  },
  not_configured: {
    title: 'No API key is configured',
    advice: 'Add the provider key to the backend configuration, then retry.',
  },
  timeout: {
    title: 'The provider timed out',
    advice: 'Usually temporary. Retry in a moment.',
  },
  unavailable: {
    title: 'The provider is unavailable',
    advice: 'A connection or server error at the provider. Retry later.',
  },
  rejected: {
    title: 'The provider rejected the request',
    advice: 'The request was refused (for example an unsupported audio format). Retrying the same file is unlikely to help.',
  },
  empty_result: {
    title: 'No speech was detected',
    advice: 'The transcription returned nothing. Check that the recording contains audio.',
  },
  audio_missing: {
    title: 'The recording file is missing',
    advice: 'The audio file could not be read on the server. Re-upload the recording.',
  },
  invalid_response: {
    title: 'The analysis returned an invalid response',
    advice: 'The model answer could not be validated. Retrying often works.',
  },
  internal: {
    title: 'Unexpected processing error',
    advice: 'Something failed inside the server. See the server log; retry once, then report it.',
  },
  network: {
    title: 'Could not reach the server',
    advice: 'Check your connection and that the backend is running, then retry.',
  },
  forbidden: {
    title: 'Not allowed',
    advice: 'Only a manager or head of collections can start or retry analysis.',
  },
  not_found: {
    title: 'Call not found',
    advice: 'The call does not exist or is not visible to your role.',
  },
  other: {
    title: 'Processing failed',
    advice: 'Retry once. If it fails again, check the server log.',
  },
}

interface FailureNoticeProps {
  kind: QueueFailureKind | null | undefined
  reason: string | null | undefined
  // When earlier valid results were kept (the attempt failed but the call still has an analysis).
  keptPreviousResults?: boolean
}

export function FailureNotice({ kind, reason, keptPreviousResults = false }: FailureNoticeProps) {
  const description = FAILURE_DESCRIPTIONS[kind ?? 'other']
  return (
    <div
      role="alert"
      data-testid="failure-notice"
      data-failure-kind={kind ?? 'other'}
      style={{
        padding: '10px 12px',
        background: 'var(--error-bg)',
        borderRadius: 'var(--radius-input)',
        fontSize: 'var(--text-sm)',
      }}
    >
      <strong data-testid="failure-title">{description.title}</strong>
      <div style={{ marginTop: '2px' }}>{description.advice}</div>
      {keptPreviousResults && (
        <div style={{ marginTop: '2px' }}>The previous analysis was kept unchanged.</div>
      )}
      {reason && (
        <div data-testid="failure-reason" style={{ marginTop: '4px', color: 'var(--text-secondary)' }}>
          {reason}
        </div>
      )}
    </div>
  )
}
