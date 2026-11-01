import { useMemo } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useCalls } from '../api/calls'
import { useOffices, useUsers } from '../api/meta'
import { ApiError } from '../api/client'
import { DataTable, type DataTableColumn } from '../components/ui/DataTable'
import { FilterBar, FilterInput, FilterSelect } from '../components/ui/FilterBar'
import { StatusChip } from '../components/ui/StatusChip'
import { OutcomeChip } from '../components/ui/OutcomeChip'
import { ReviewStatusChip } from '../components/ui/ReviewStatusChip'
import { RiskChip } from '../components/ui/RiskChip'
import { ScoreCell } from '../components/ui/ScoreCell'
import { useRoleContext } from '../context/RoleContext'
import type { CallListFilters, CallListItem } from '../api/types'

const PAGE_SIZE = 25

function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

function formatDuration(seconds: number | null): string {
  if (seconds === null) return '—'
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return `${m}m ${s.toString().padStart(2, '0')}s`
}

const REVIEW_TABS: { value: string; label: string }[] = [
  { value: '', label: 'All calls' },
  { value: 'pending_review', label: 'Pending review' },
  { value: 'reviewed', label: 'Reviewed' },
]

const SORTS = [
  { value: 'date_desc', label: 'Newest first' },
  { value: 'date_asc', label: 'Oldest first' },
  { value: 'risk', label: 'Riskiest first (lowest score)' },
  { value: 'score_asc', label: 'Score: low to high' },
  { value: 'score_desc', label: 'Score: high to low' },
]

const panelStyle = {
  background: 'var(--surface-1)',
  border: '1px solid var(--border)',
  borderRadius: 'var(--radius-card)',
}

const controlLabelStyle = {
  display: 'flex',
  flexDirection: 'column' as const,
  gap: '5px',
  fontSize: 'var(--text-xs)',
  color: 'var(--text-secondary)',
  fontWeight: 600,
}

const controlStyle = {
  minHeight: '36px',
  padding: '7px 10px',
  borderRadius: 'var(--radius-input)',
  border: '1px solid var(--border)',
  background: 'var(--surface-1)',
  color: 'var(--text-primary)',
  fontSize: 'var(--text-sm)',
}

// Filters live in the URL, so a dashboard link, a refresh or the browser's back
// button all land on the same filtered list.
export function CallListPage() {
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const { role, activeUserId } = useRoleContext()

  const get = (key: string) => params.get(key) ?? ''
  const reviewStatus = get('review_status')
  const analysisStatus = get('analysis_status')
  const officeId = get('office_id')
  const callerId = get('caller_id')
  const severity = get('severity')
  const dateFrom = get('date_from')
  const dateTo = get('date_to')
  const search = get('q')
  const sort = get('sort') || 'date_desc'
  const page = Math.max(1, Number(get('page')) || 1)

  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    if (key !== 'page') next.delete('page') // any filter change starts again at page 1
    setParams(next, { replace: true })
  }

  const headers = activeUserId ? { role, userId: activeUserId } : null

  const filters: CallListFilters = useMemo(
    () => ({
      office_id: officeId || undefined,
      caller_id: callerId || undefined,
      severity: (severity || undefined) as CallListFilters['severity'],
      // Calls are stored in IST wall-clock time, so the picked dates are sent as they are (YYYY-MM-DD):
      // the server reads them as whole IST days. They are never converted through UTC.
      date_from: dateFrom || undefined,
      date_to: dateTo || undefined,
      q: search || undefined,
      analysis_status: (analysisStatus || undefined) as CallListFilters['analysis_status'],
      review_status: (reviewStatus || undefined) as CallListFilters['review_status'],
      sort: sort as CallListFilters['sort'],
      page,
      page_size: PAGE_SIZE,
    }),
    [officeId, callerId, severity, dateFrom, dateTo, search, analysisStatus, reviewStatus, sort, page],
  )

  const { data, isLoading, isError, error, refetch } = useCalls(filters, headers)
  const { data: offices } = useOffices()
  const { data: callers } = useUsers('caller')

  const showOfficeFilter = role === 'head'
  const showCallerFilter = role === 'head'
  const showCallerColumn = role !== 'caller'

  const columns: DataTableColumn<CallListItem>[] = [
    { key: 'call_id', header: 'Call ID', render: (c) => <span className="font-mono">{c.call_id}</span> },
    ...(showCallerColumn ? [{ key: 'caller', header: 'Caller', render: (c: CallListItem) => c.caller.name }] : []),
    { key: 'office', header: 'Office', render: (c) => c.office.name },
    { key: 'start_time', header: 'Date/Time', render: (c) => <span className="font-mono">{formatDateTime(c.start_time)}</span> },
    { key: 'duration', header: 'Duration', render: (c) => <span className="font-mono">{formatDuration(c.duration_seconds)}</span> },
    { key: 'status', header: 'Analysis', render: (c) => <StatusChip status={c.status} /> },
    {
      key: 'score',
      header: 'Score',
      align: 'right',
      render: (c) => <ScoreCell score={c.score} provisional={c.provisional_score} />,
    },
    {
      key: 'violations',
      header: 'Violations',
      align: 'right',
      render: (c) => (c.status === 'ready' ? <span data-testid="violation-count" title="Findings not dismissed as false alarms">{c.findings_total ?? 0}</span> : '—'),
    },
    { key: 'risk', header: 'Risk', render: (c) => <RiskChip severity={c.top_severity} analysed={c.status === 'ready'} /> },
    { key: 'review', header: 'Review', render: (c) => <ReviewStatusChip status={c.review_status} /> },
    {
      key: 'outcome',
      header: 'Outcome',
      render: (c) => (
        <span>
          <OutcomeChip outcome={c.analysis_outcome} />
          {c.last_reprocess_failed && (
            <span
              data-testid="reprocess-failed-flag"
              title="The latest reprocessing attempt failed; the previous analysis is shown."
              style={{ marginLeft: '6px', fontSize: 'var(--text-xs)', color: 'var(--critical)', fontWeight: 600 }}
            >
              reprocess failed
            </span>
          )}
        </span>
      ),
    },
  ]

  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  const scopeLabel =
    role === 'head' ? 'Head of Collections view — all offices' : role === 'manager' ? "Manager view — your team's calls" : 'Caller view — your calls'

  const activeFilterCount = [officeId, callerId, analysisStatus, severity, dateFrom, dateTo, search].filter(Boolean).length

  return (
    <div style={{ display: 'grid', gap: '20px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', gap: '16px', flexWrap: 'wrap' }}>
        <div>
          <p style={{ color: 'var(--brand)', fontSize: 'var(--text-xs)', fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase' }}>Operations console</p>
          <h1 style={{ marginTop: '6px', fontSize: 'var(--text-2xl)', fontWeight: 700, letterSpacing: '-0.02em' }}>All calls</h1>
          <p style={{ color: 'var(--text-secondary)', marginTop: '6px' }}>
            {isLoading ? 'Loading call records…' : `${total.toLocaleString()} call${total === 1 ? '' : 's'} in scope · ${scopeLabel}`}
          </p>
        </div>
        <div style={{ ...panelStyle, padding: '12px 16px', minWidth: '160px' }}>
          <div style={{ color: 'var(--text-secondary)', fontSize: 'var(--text-xs)', fontWeight: 600 }}>Current view</div>
          <div className="font-mono" style={{ marginTop: '4px', fontSize: 'var(--text-lg)', fontWeight: 700 }}>{reviewStatus ? (reviewStatus === 'pending_review' ? 'Pending review' : 'Reviewed') : 'All calls'}</div>
        </div>
      </div>

      <div role="tablist" aria-label="Review status" style={{ ...panelStyle, display: 'flex', gap: '4px', padding: '5px', width: 'fit-content', maxWidth: '100%', overflowX: 'auto' }}>
        {REVIEW_TABS.map((tab) => {
          const active = reviewStatus === tab.value
          return (
            <button
              key={tab.value || 'all'}
              role="tab"
              aria-selected={active}
              data-testid={`tab-${tab.value || 'all'}`}
              onClick={() => setFilter('review_status', tab.value)}
              style={{
                padding: '8px 14px',
                borderRadius: 'var(--radius-input)',
                border: '1px solid transparent',
                cursor: 'pointer',
                fontWeight: 600,
                background: active ? 'var(--brand)' : 'transparent',
                color: active ? '#fff' : 'inherit',
              }}
            >
              {tab.label}
            </button>
          )
        })}
      </div>

      <div style={{ ...panelStyle, padding: '16px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px', flexWrap: 'wrap', marginBottom: '14px' }}>
          <div>
            <h2 style={{ fontSize: 'var(--text-base)', fontWeight: 700 }}>Find and filter calls</h2>
            <p style={{ marginTop: '3px', color: 'var(--text-secondary)', fontSize: 'var(--text-sm)' }}>Use risk, review, and date filters to focus the queue.</p>
          </div>
          {activeFilterCount > 0 && <span style={{ color: 'var(--brand)', fontSize: 'var(--text-xs)', fontWeight: 700 }}>{activeFilterCount} filter{activeFilterCount === 1 ? '' : 's'} active</span>}
        </div>
        <FilterBar>
        {showOfficeFilter && (
          <label style={controlLabelStyle}>Office
          <FilterSelect aria-label="Office" value={officeId} onChange={(e) => setFilter('office_id', e.target.value)} style={controlStyle}>
            <option value="">All offices</option>
            {offices?.map((o) => (
              <option key={o.id} value={o.id}>
                {o.name}
              </option>
            ))}
          </FilterSelect>
          </label>
        )}

        {showCallerFilter && (
          <label style={controlLabelStyle}>Caller
          <FilterSelect aria-label="Caller" value={callerId} onChange={(e) => setFilter('caller_id', e.target.value)} style={controlStyle}>
            <option value="">All callers</option>
            {callers?.map((u) => (
              <option key={u.id} value={u.id}>
                {u.name}
              </option>
            ))}
          </FilterSelect>
          </label>
        )}

        <label style={controlLabelStyle}>Analysis status
        <FilterSelect aria-label="Analysis status" value={analysisStatus} onChange={(e) => setFilter('analysis_status', e.target.value)} style={controlStyle}>
          <option value="">Any analysis status</option>
          <option value="queued">Queued</option>
          <option value="processing">Processing</option>
          <option value="completed">Completed</option>
          <option value="failed">Failed</option>
        </FilterSelect>
        </label>

        <label style={controlLabelStyle}>Severity
        <FilterSelect aria-label="Severity" value={severity} onChange={(e) => setFilter('severity', e.target.value)} style={controlStyle}>
          <option value="">Any severity</option>
          <option value="critical">Critical</option>
          <option value="high">High</option>
          <option value="medium">Medium</option>
          <option value="low">Low</option>
        </FilterSelect>
        </label>

        <label style={controlLabelStyle}>From date
        <FilterInput type="date" aria-label="From date" value={dateFrom} onChange={(e) => setFilter('date_from', e.target.value)} style={controlStyle} />
        </label>
        <label style={controlLabelStyle}>To date
        <FilterInput type="date" aria-label="To date" value={dateTo} onChange={(e) => setFilter('date_to', e.target.value)} style={controlStyle} />
        </label>

        <label style={{ ...controlLabelStyle, flex: '1 1 220px' }}>Search
        <FilterInput
          type="search"
          aria-label="Search calls"
          placeholder="Call ID, caller or transcript…"
          value={search}
          onChange={(e) => setFilter('q', e.target.value)}
          style={{ ...controlStyle, width: '100%' }}
        />
        </label>

        <label style={controlLabelStyle}>Sort by
        <FilterSelect aria-label="Sort by" value={sort} onChange={(e) => setFilter('sort', e.target.value === 'date_desc' ? '' : e.target.value)} style={controlStyle}>
          {SORTS.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </FilterSelect>
        </label>
        </FilterBar>
      </div>

      <div style={{ ...panelStyle, overflow: 'hidden' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '16px 18px', borderBottom: '1px solid var(--border)', gap: '12px', flexWrap: 'wrap' }}>
          <div>
            <h2 style={{ fontSize: 'var(--text-base)', fontWeight: 700 }}>Call queue</h2>
            <p style={{ marginTop: '3px', color: 'var(--text-secondary)', fontSize: 'var(--text-sm)' }}>Select a row to open the transcript and compliance findings.</p>
          </div>
          <span className="font-mono" style={{ color: 'var(--text-secondary)', fontSize: 'var(--text-sm)' }}>{total.toLocaleString()} records</span>
        </div>
        <DataTable
          columns={columns}
          rows={data?.items ?? []}
          rowKey={(c) => c.call_id}
          isLoading={isLoading}
          isError={isError}
          errorMessage={error instanceof ApiError ? error.message : 'Failed to load calls.'}
          onRetry={() => refetch()}
          emptyTitle={reviewStatus === 'pending_review' ? 'Nothing is waiting for review' : 'No calls match these filters'}
          emptyDescription={
            reviewStatus === 'pending_review'
              ? 'Every analysed call in your scope has been reviewed.'
              : 'Try clearing filters or widening the date range.'
          }
          onRowClick={(c) => navigate(`/calls/${c.call_id}`)}
        />
      </div>

      {total > 0 && (
        <div style={{ ...panelStyle, display: 'flex', justifyContent: 'center', alignItems: 'center', gap: '16px', padding: '12px 16px', width: 'fit-content', maxWidth: '100%', margin: '0 auto' }}>
          <button
            disabled={page <= 1}
            onClick={() => setFilter('page', String(Math.max(1, page - 1)))}
            style={{ ...controlStyle, cursor: page <= 1 ? 'not-allowed' : 'pointer', opacity: page <= 1 ? 0.55 : 1 }}
          >
            Previous
          </button>
          <span style={{ fontSize: 'var(--text-sm)', color: 'var(--text-secondary)' }}>
            Page {page} of {totalPages}
          </span>
          <button
            disabled={page >= totalPages}
            onClick={() => setFilter('page', String(Math.min(totalPages, page + 1)))}
            style={{ ...controlStyle, cursor: page >= totalPages ? 'not-allowed' : 'pointer', opacity: page >= totalPages ? 0.55 : 1 }}
          >
            Next
          </button>
        </div>
      )}
    </div>
  )
}
