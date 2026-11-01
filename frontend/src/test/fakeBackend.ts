import { vi } from 'vitest'
import { ApiError } from '../api/client'
import type { AnalysisOutcome, CallDetail, FailureKind, ReprocessResponse } from '../api/types'

// An in-memory stand-in for the backend's call endpoints. No network, no
// providers: tests drive the UI against this.

export function makeCall(id: string, overrides: Partial<CallDetail> = {}): CallDetail {
  return {
    call_id: id,
    caller: { id: 'u-1', name: 'Test Caller' },
    office: { id: 'o-1', name: 'Jogeshwari' },
    start_time: '2026-10-04T10:10:00',
    duration_seconds: 70,
    language: 'hi',
    account_type: 'personal_loan',
    score: null,
    status: 'queued',
    analysis_outcome: 'not_analyzed',
    counted_violations: 0,
    findings_pending_review: 0,
    findings_unverified: 0,
    provisional_score: false,
    last_reprocess_failed: false,
    sentiment: null,
    agent_stayed_calm: null,
    has_audio: true,
    is_ground_truth: false,
    failure_reason: null,
    failure_kind: null,
    ...overrides,
  }
}

export type Plan =
  | { kind: 'ok'; outcome?: AnalysisOutcome; score?: number }
  | { kind: 'fail'; failure: FailureKind; reason?: string; keepResults?: boolean }
  | { kind: 'http'; status: number; code: string; message: string }
  | { kind: 'network' }

export class FakeBackend {
  calls = new Map<string, CallDetail>()
  plans = new Map<string, Plan>()
  posts: string[] = []
  gets: string[] = []
  inFlight = 0
  maxInFlight = 0
  // Resolves each POST only when the test says so (to observe in-progress states).
  gate: Promise<void> | null = null

  add(call: CallDetail, plan?: Plan) {
    this.calls.set(call.call_id, call)
    if (plan) this.plans.set(call.call_id, plan)
    return this
  }

  handler = vi.fn(async (path: string, options: { method?: string } = {}): Promise<unknown> => {
    const post = path.match(/^\/api\/calls\/([^/]+)\/reprocess$/)
    if (post && options.method === 'POST') return this.reprocess(post[1])

    const get = path.match(/^\/api\/calls\/([^/]+)$/)
    if (get) {
      this.gets.push(get[1])
      const call = this.calls.get(get[1])
      if (!call) throw new ApiError(404, 'CALL_NOT_FOUND', 'Call not found.')
      return { ...call }
    }
    if (path === '/api/calls') {
      return { items: [...this.calls.values()], total: this.calls.size, page: 1, page_size: 20 }
    }
    if (path.endsWith('/transcript') || path.endsWith('/violations')) return []
    throw new Error(`FakeBackend: unhandled ${options.method ?? 'GET'} ${path}`)
  })

  private async reprocess(id: string): Promise<ReprocessResponse> {
    this.posts.push(id)
    this.inFlight += 1
    this.maxInFlight = Math.max(this.maxInFlight, this.inFlight)
    try {
      const call = this.calls.get(id)
      if (!call) throw new ApiError(404, 'CALL_NOT_FOUND', 'Call not found.')
      this.calls.set(id, { ...call, status: 'transcribing' })
      if (this.gate) await this.gate
      else await Promise.resolve()

      const plan = this.plans.get(id) ?? { kind: 'ok' }
      if (plan.kind === 'network') {
        this.calls.set(id, { ...call })
        throw new TypeError('Failed to fetch')
      }
      if (plan.kind === 'http') {
        this.calls.set(id, { ...call })
        throw new ApiError(plan.status, plan.code, plan.message)
      }
      if (plan.kind === 'fail') {
        const reason = plan.reason ?? `The provider failed (${plan.failure}).`
        const kept = plan.keepResults === true
        this.calls.set(id, {
          ...call,
          status: kept ? 'ready' : 'failed',
          last_reprocess_failed: kept,
          failure_reason: reason,
          failure_kind: plan.failure,
        })
        return { call_id: id, status: kept ? 'ready' : 'failed', score: call.score, failure_reason: reason, failure_kind: plan.failure }
      }
      const outcome = plan.outcome ?? 'clean'
      const score = plan.score ?? 100
      this.calls.set(id, {
        ...call,
        status: 'ready',
        score,
        analysis_outcome: outcome,
        sentiment: 'neutral',
        agent_stayed_calm: true,
        failure_reason: null,
        failure_kind: null,
        last_reprocess_failed: false,
      })
      return { call_id: id, status: 'ready', score, failure_reason: null, failure_kind: null }
    } finally {
      this.inFlight -= 1
    }
  }
}
