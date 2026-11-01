import type { InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from 'react'

export function FilterBar({ children }: { children: ReactNode }) {
  return (
    <div
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        gap: '12px',
        alignItems: 'center',
        padding: '12px 16px',
        background: 'var(--surface-1)',
        border: '1px solid var(--border)',
        borderRadius: 'var(--radius-card)',
        marginBottom: '16px',
      }}
    >
      {children}
    </div>
  )
}

const controlStyle = {
  padding: '6px 10px',
  borderRadius: 'var(--radius-input)',
  border: '1px solid var(--border)',
  background: 'var(--surface-1)',
  fontSize: 'var(--text-sm)',
}

export function FilterSelect(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} style={{ ...controlStyle, ...props.style }} />
}

export function FilterInput(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} style={{ ...controlStyle, ...props.style }} />
}
