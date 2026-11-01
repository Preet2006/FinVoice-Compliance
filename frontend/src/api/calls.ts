import { useQuery } from '@tanstack/react-query'
import { apiFetch, type RoleHeaders } from './client'
import { isInProgress, STATUS_POLL_MS } from './analysisStatus'
import type { CallDetail, CallListFilters, CallListItem, Page, TranscriptSegment, Violation } from './types'

export function useCalls(filters: CallListFilters, headers: RoleHeaders | null) {
  return useQuery({
    // Role/user in the query key: switching identity automatically refetches
    // scoped data instead of serving another identity's cached page.
    queryKey: ['calls', headers?.role, headers?.userId, filters],
    queryFn: () =>
      apiFetch<Page<CallListItem>>('/api/calls', {
        params: { ...filters },
        headers: headers ?? undefined,
      }),
    enabled: headers !== null,
    // Re-read only while something on the page is actually being processed.
    // Polling never starts processing; it only reads.
    refetchInterval: (query) =>
      query.state.data?.items.some((c) => isInProgress(c.status)) ? STATUS_POLL_MS : false,
  })
}

export function useCall(
  callId: string | undefined,
  headers: RoleHeaders | null,
  options: { forcePoll?: boolean } = {},
) {
  return useQuery({
    queryKey: ['call', headers?.role, headers?.userId, callId],
    queryFn: () =>
      apiFetch<CallDetail>(`/api/calls/${callId}`, { headers: headers ?? undefined }),
    enabled: headers !== null && callId !== undefined,
    refetchInterval: (query) =>
      options.forcePoll || isInProgress(query.state.data?.status) ? STATUS_POLL_MS : false,
  })
}

export function useCallTranscript(callId: string | undefined, headers: RoleHeaders | null) {
  return useQuery({
    queryKey: ['call-transcript', headers?.role, headers?.userId, callId],
    queryFn: () =>
      apiFetch<TranscriptSegment[]>(`/api/calls/${callId}/transcript`, {
        headers: headers ?? undefined,
      }),
    enabled: headers !== null && callId !== undefined,
  })
}

export function useCallViolations(callId: string | undefined, headers: RoleHeaders | null) {
  return useQuery({
    queryKey: ['call-violations', headers?.role, headers?.userId, callId],
    queryFn: () =>
      apiFetch<Violation[]>(`/api/calls/${callId}/violations`, {
        headers: headers ?? undefined,
      }),
    enabled: headers !== null && callId !== undefined,
  })
}
