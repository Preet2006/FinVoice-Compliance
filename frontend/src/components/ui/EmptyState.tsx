interface EmptyStateProps {
  title: string
  description?: string
  action?: { label: string; onClick: () => void }
}

export function EmptyState({ title, description, action }: EmptyStateProps) {
  return (
    <div
      style={{
        padding: '48px 24px',
        textAlign: 'center',
        color: 'var(--text-secondary)',
      }}
    >
      <p style={{ fontSize: 'var(--text-lg)', color: 'var(--text-primary)', fontWeight: 600 }}>
        {title}
      </p>
      {description && <p style={{ marginTop: '8px' }}>{description}</p>}
      {action && (
        <button
          onClick={action.onClick}
          style={{
            marginTop: '16px',
            padding: '8px 16px',
            borderRadius: 'var(--radius-input)',
            border: '1px solid var(--border)',
            background: 'var(--surface-1)',
            cursor: 'pointer',
          }}
        >
          {action.label}
        </button>
      )}
    </div>
  )
}
