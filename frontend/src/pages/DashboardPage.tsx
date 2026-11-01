import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { useDashboard } from '../api/dashboard'
import { ApiError } from '../api/client'
import { ErrorState } from '../components/ui/ErrorState'
import { EmptyState } from '../components/ui/EmptyState'
import { useRoleContext } from '../context/RoleContext'
import type { Dashboard, DashboardRow, EvaluationMetrics } from '../api/types'

const SCOPE_TITLE: Record<string, string> = {
  head: 'All offices',
  manager: 'Your team',
  caller: 'Your calls',
}

type Tone = 'brand' | 'good' | 'warn' | 'bad' | 'muted'
const TONE_COLOR: Record<Tone, string> = {
  brand: 'var(--brand)',
  good: 'var(--success)',
  warn: 'var(--high)',
  bad: 'var(--critical)',
  muted: 'var(--text-secondary)',
}

const cardStyle = {
  border: '1px solid var(--border)',
  borderRadius: 'var(--radius-card)',
  background: 'var(--surface-1)',
  padding: '16px 20px',
  boxShadow: 'var(--shadow-elevated)',
} as const

function Card({ title, subtitle, children, testId }: { title: string; subtitle?: string; children: ReactNode; testId?: string }) {
  return (
    <section data-testid={testId} style={cardStyle}>
      <h2 style={{ fontSize: 'var(--text-lg)', fontWeight: 600 }}>{title}</h2>
      {subtitle && <p style={{ color: 'var(--text-secondary)', fontSize: 'var(--text-sm)', marginTop: '2px' }}>{subtitle}</p>}
      <div style={{ marginTop: '12px' }}>{children}</div>
    </section>
  )
}

// `to` makes the card a link to the matching filtered Call List.
function Kpi({ label, value, hint, to, tone = 'brand' }: { label: string; value: string | number; hint?: string; to?: string; tone?: Tone }) {
  const card = (
    <div
      data-testid={`kpi-${label.toLowerCase().replace(/\s+/g, '-')}`}
      style={{ ...cardStyle, padding: '14px 16px', height: '100%', borderTop: `3px solid ${TONE_COLOR[tone]}` }}
    >
      <div style={{ fontSize: 'var(--text-sm)', color: 'var(--text-secondary)' }}>{label}</div>
      <div className="font-mono" style={{ fontSize: 'var(--text-3xl)', fontWeight: 600, lineHeight: 1.2, color: TONE_COLOR[tone] }}>
        {value}
      </div>
      {hint && <div style={{ fontSize: 'var(--text-xs)', color: 'var(--text-secondary)' }}>{hint}</div>}
    </div>
  )
  return to ? (
    <Link to={to} aria-label={`${label}: ${value}. Open these calls`} style={{ color: 'inherit', textDecoration: 'none' }}>
      {card}
    </Link>
  ) : (
    card
  )
}

function Bar({ value, max, color, label, height = 12 }: { value: number; max: number; color: string; label: string; height?: number }) {
  const pct = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0
  return (
    <div style={{ flex: 1, background: 'var(--surface-2)', borderRadius: '999px', height: `${height}px`, overflow: 'hidden' }}>
      <div role="img" aria-label={label} title={label} style={{ width: `${pct}%`, minWidth: value > 0 ? '4px' : 0, height: '100%', background: color, borderRadius: '999px' }} />
    </div>
  )
}

function RuleBars({ data }: { data: Dashboard['breaches_by_rule'] }) {
  const max = Math.max(1, ...data.map((d) => d.count))
  return (
    <div data-testid="breaches-by-rule" style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
      {data.map((d) => (
        <div key={d.rule_code} style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <span className="font-mono" style={{ width: '32px', fontWeight: 600 }}>{d.rule_code}</span>
          <Bar value={d.count} max={max} color="var(--brand)" label={`${d.rule_code}: ${d.count}`} />
          <span className="font-mono" style={{ width: '28px', textAlign: 'right', fontWeight: 600 }}>{d.count}</span>
        </div>
      ))}
    </div>
  )
}

function ScoreBar({ score }: { score: number | null }) {
  if (score === null) return <span>—</span>
  const color = score >= 85 ? 'var(--success)' : score >= 60 ? 'var(--high)' : 'var(--critical)'
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '8px', minWidth: '120px' }}>
      <span style={{ width: '64px', display: 'inline-flex' }}>
        <Bar value={score} max={100} color={color} label={`Average score ${score}`} height={8} />
      </span>
      <span className="font-mono">{score}</span>
    </span>
  )
}

function RowsTable({ title, rows, testId, nameLabel, filter }: { title: string; rows: DashboardRow[]; testId: string; nameLabel: string; filter: 'caller_id' | 'office_id' }) {
  return (
    <Card title={title}>
      {rows.length === 0 ? (
        <p style={{ color: 'var(--text-secondary)' }}>No analysed calls yet.</p>
      ) : (
        <table data-testid={testId} style={{ width: '100%', borderCollapse: 'collapse', fontSize: 'var(--text-sm)' }}>
          <thead>
            <tr style={{ textAlign: 'left', borderBottom: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
              <th style={{ padding: '6px 8px' }}>{nameLabel}</th>
              <th style={{ padding: '6px 8px' }}>Calls</th>
              <th style={{ padding: '6px 8px' }}>Breaches</th>
              <th style={{ padding: '6px 8px' }}>Counted in score</th>
              <th style={{ padding: '6px 8px' }}>Avg score</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} style={{ borderBottom: '1px solid var(--surface-2)' }}>
                <td style={{ padding: '8px' }}>
                  <Link to={`/calls?${filter}=${encodeURIComponent(r.id)}`} style={{ color: 'var(--brand)', fontWeight: 500 }}>{r.name}</Link>
                </td>
                <td className="font-mono" style={{ padding: '8px' }}>{r.calls}</td>
                <td className="font-mono" style={{ padding: '8px' }}>{r.violations}</td>
                <td className="font-mono" style={{ padding: '8px' }}>{r.counted}</td>
                <td style={{ padding: '8px' }}><ScoreBar score={r.average_score} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  )
}

// Confirmed / false alarm / pending as one stacked bar with a 2px gap between segments, and the same numbers as text.
function ReviewOutcomes({ outcomes }: { outcomes: Dashboard['review_outcomes'] }) {
  const total = outcomes.confirmed + outcomes.false_alarm + outcomes.pending
  const parts = [
    { key: 'confirmed', label: 'Confirmed', n: outcomes.confirmed, color: 'var(--success)' },
    { key: 'false_alarm', label: 'False alarm', n: outcomes.false_alarm, color: 'var(--low)' },
    { key: 'pending', label: 'Pending', n: outcomes.pending, color: 'var(--high)' },
  ]
  return (
    <div data-testid="review-outcomes">
      <div style={{ display: 'flex', gap: '2px', height: '14px', borderRadius: '999px', overflow: 'hidden', background: 'var(--surface-2)' }}>
        {parts.filter((p) => p.n > 0).map((p) => (
          <div key={p.key} role="img" aria-label={`${p.label} segment: ${p.n}`} title={`${p.label}: ${p.n}`} style={{ flex: p.n, background: p.color }} />
        ))}
      </div>
      <div style={{ display: 'flex', gap: '18px', flexWrap: 'wrap', marginTop: '10px', fontSize: 'var(--text-sm)' }}>
        {parts.map((p) => (
          <span key={p.key} style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
            <span style={{ width: '10px', height: '10px', borderRadius: '50%', background: p.color }} />
            {p.label}: <strong className="font-mono">{p.n}</strong>
          </span>
        ))}
        {total === 0 && <span style={{ color: 'var(--text-secondary)' }}>No findings yet.</span>}
      </div>
    </div>
  )
}

const metric = (v: number | null) => (v === null ? '—' : v.toFixed(2))

function MetricCell({ value }: { value: number | null }) {
  const color = value === null ? 'var(--border)' : value >= 0.8 ? 'var(--success)' : value >= 0.5 ? 'var(--high)' : 'var(--critical)'
  return (
    <td style={{ padding: '8px', minWidth: '110px' }}>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: '8px', width: '100%' }}>
        <span style={{ width: '48px', display: 'inline-flex' }}>
          <Bar value={value ?? 0} max={1} color={color} label={`value ${metric(value)}`} height={6} />
        </span>
        <span className="font-mono">{metric(value)}</span>
      </span>
    </td>
  )
}

// Per-rule precision / recall / F1 against the approved SCRIPT-DERIVED labels. Shown only when the backend computed
// them (an evaluation database); always carries the label saying what they are not.
function EvaluationSection({ evaluation }: { evaluation?: EvaluationMetrics | null }) {
  if (!evaluation) return null
  return (
    <Card title="Evaluation metrics per rule" testId="evaluation-metrics">
      <p
        data-testid="evaluation-label"
        style={{ display: 'inline-block', background: 'var(--warning-bg)', color: 'var(--high)', borderRadius: 'var(--radius-input)', padding: '4px 10px', fontSize: 'var(--text-sm)', fontWeight: 500, marginBottom: '10px' }}
      >
        {evaluation.label}
      </p>
      {!evaluation.available ? (
        <p style={{ color: 'var(--text-secondary)' }}>Not available. {evaluation.reason}</p>
      ) : (
        <>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 'var(--text-sm)' }}>
            <thead>
              <tr style={{ textAlign: 'left', borderBottom: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
                {['Rule', 'TP', 'FP', 'FN', 'TN', 'Precision', 'Recall', 'F1'].map((h) => (
                  <th key={h} style={{ padding: '6px 8px' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {evaluation.per_rule.map((r) => (
                <tr key={r.rule_code} data-testid={`eval-row-${r.rule_code}`} style={{ borderBottom: '1px solid var(--surface-2)' }}>
                  <td style={{ padding: '8px', fontWeight: 600 }}>{r.rule_code}</td>
                  <td className="font-mono" style={{ padding: '8px' }}>{r.tp}</td>
                  <td className="font-mono" style={{ padding: '8px' }}>{r.fp}</td>
                  <td className="font-mono" style={{ padding: '8px' }}>{r.fn}</td>
                  <td className="font-mono" style={{ padding: '8px' }}>{r.tn}</td>
                  <MetricCell value={r.precision} />
                  <MetricCell value={r.recall} />
                  <MetricCell value={r.f1} />
                </tr>
              ))}
            </tbody>
          </table>
          <p style={{ color: 'var(--text-secondary)', fontSize: 'var(--text-xs)', marginTop: '8px' }}>
            Scored on {evaluation.calls_scored} analysed call(s). A dash means the figure is undefined (no positive labels or no positive predictions).
            {' '}{evaluation.notes.join(' ')}
          </p>
        </>
      )}
    </Card>
  )
}

export function DashboardPage() {
  const { role, activeUserId } = useRoleContext()
  const headers = activeUserId ? { role, userId: activeUserId } : null
  const { data, isLoading, isError, error, refetch } = useDashboard(headers)

  return (
    <div>
      <h1 style={{ fontSize: 'var(--text-2xl)', fontWeight: 600 }}>Dashboard</h1>
      <p style={{ color: 'var(--text-secondary)', marginTop: '4px' }}>
        {SCOPE_TITLE[role] ?? ''}. Figures come from analysed calls and reviewer decisions stored in the application.
      </p>

      {!headers && <p style={{ marginTop: '16px', color: 'var(--text-secondary)' }}>Select a user to see the dashboard.</p>}
      {headers && isLoading && <p style={{ marginTop: '16px', color: 'var(--text-secondary)' }}>Loading…</p>}
      {headers && isError && (
        <div style={{ marginTop: '16px' }}>
          <ErrorState message={error instanceof ApiError ? error.message : 'Failed to load the dashboard.'} onRetry={() => refetch()} />
        </div>
      )}

      {data && data.calls_analyzed === 0 && (
        <div style={{ marginTop: '16px' }}>
          <EmptyState
            title="No analysed calls yet"
            description={`${data.total_calls} call(s) are in scope, but none has been analysed, so there are no breaches or scores to show.`}
          />
        </div>
      )}

      {data && data.calls_analyzed > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '20px', marginTop: '16px' }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', gap: '12px' }}>
            <Kpi label="Total calls" value={data.total_calls} to="/calls" tone="brand" />
            <Kpi label="Calls analyzed" value={data.calls_analyzed} to="/calls?analysis_status=completed" tone="brand" />
            <Kpi label="Pending processing" value={data.calls_pending ?? 0} hint="queued or in progress" to="/calls?analysis_status=queued" tone="muted" />
            <Kpi label="Failed" value={data.calls_failed ?? 0} hint="analysis failed" to="/calls?analysis_status=failed" tone={(data.calls_failed ?? 0) > 0 ? 'bad' : 'muted'} />
            <Kpi label="Pending review" value={data.calls_awaiting_review} hint="analysed, not yet signed off" to="/calls?review_status=pending_review" tone="warn" />
            <Kpi label="Reviewed" value={data.reviewed_calls ?? 0} hint="signed off by a manager" to="/calls?review_status=reviewed" tone="good" />
            <Kpi label="Verified violations" value={data.verified_violations} hint={`${data.counted_violations} counted in scores`} tone="bad" />
            <Kpi label="Unverified findings" value={data.unverified_findings} hint="not counted as breaches" tone="warn" />
            <Kpi label="Average score" value={data.average_score ?? '—'} tone={data.average_score !== null && data.average_score >= 85 ? 'good' : 'warn'} />
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '16px' }}>
            <Card title="Breaches by rule" subtitle="Verified findings, not dismissed as false alarms">
              <RuleBars data={data.breaches_by_rule} />
            </Card>
            <Card title="Review outcomes" subtitle="Manager decisions on findings">
              <ReviewOutcomes outcomes={data.review_outcomes} />
            </Card>
          </div>

          <RowsTable title="Top risky callers" rows={data.top_risky_callers} testId="top-risky" nameLabel="Caller" filter="caller_id" />
          {role !== 'caller' && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(420px, 1fr))', gap: '16px' }}>
              <RowsTable title="Breaches by caller" rows={data.breaches_by_caller} testId="by-caller" nameLabel="Caller" filter="caller_id" />
              <RowsTable title="Breaches by office" rows={data.breaches_by_office} testId="by-office" nameLabel="Office" filter="office_id" />
            </div>
          )}

          <EvaluationSection evaluation={data.evaluation} />

          <section data-testid="accuracy-note" style={{ ...cardStyle, background: 'var(--surface-2)', boxShadow: 'none' }}>
            <h2 style={{ fontSize: 'var(--text-lg)', fontWeight: 600, marginBottom: '8px' }}>Precision and recall</h2>
            {data.accuracy.available ? null : (
              <p style={{ color: 'var(--text-secondary)' }}>Not available. {data.accuracy.reason}</p>
            )}
          </section>
        </div>
      )}
    </div>
  )
}
