interface ErrorStateProps {
  message: string
  onRetry?: () => void
}

export function ErrorState({ message, onRetry }: ErrorStateProps) {
  return (
    <div
      style={{
        padding: '24px',
        borderRadius: 'var(--radius-card)',
        background: 'var(--error-bg)',
        color: 'var(--critical)',
      }}
      role="alert"
    >
      <p style={{ fontWeight: 600 }}>Something went wrong</p>
      <p style={{ marginTop: '4px', color: 'var(--text-primary)' }}>{message}</p>
      {onRetry && (
        <button
          onClick={onRetry}
          style={{
            marginTop: '12px',
            padding: '8px 16px',
            borderRadius: 'var(--radius-input)',
            border: '1px solid var(--border)',
            background: 'var(--surface-1)',
            cursor: 'pointer',
          }}
        >
          Retry
        </button>
      )}
    </div>
  )
}
