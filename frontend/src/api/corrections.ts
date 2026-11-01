import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch, type RoleHeaders } from './client'
import type {
  AuditEntry,
  ManualFindingRequest,
  ManualFindingResponse,
  ReviewCompleteResponse,
  SpeakerCorrectionResponse,
  TranscriptSegment,
} from './types'

// After either correction the transcript, the findings and the call's score
// may all have changed, so all of them are re-read from the server.
function useRefreshCall() {
  const queryClient = useQueryClient()
  return () => {
    queryClient.invalidateQueries({ queryKey: ['call-transcript'] })
    queryClient.invalidateQueries({ queryKey: ['call-violations'] })
    queryClient.invalidateQueries({ queryKey: ['call'] })
    queryClient.invalidateQueries({ queryKey: ['calls'] })
    queryClient.invalidateQueries({ queryKey: ['call-audit'] })
    queryClient.invalidateQueries({ queryKey: ['dashboard'] })
  }
}

export function useCorrectSpeaker(callId: string, headers: RoleHeaders | null) {
  const refresh = useRefreshCall()
  return useMutation({
    mutationFn: ({ segmentId, speaker, comment }: { segmentId: string; speaker: TranscriptSegment['speaker']; comment?: string }) => {
      if (!headers) throw new Error('No active identity selected.')
      return apiFetch<SpeakerCorrectionResponse>(`/api/calls/${callId}/segments/${segmentId}/speaker`, {
        method: 'POST',
        body: { speaker, comment: comment?.trim() || undefined },
        headers,
      })
    },
    onSuccess: refresh,
  })
}

export function useAddManualFinding(callId: string, headers: RoleHeaders | null) {
  const refresh = useRefreshCall()
  return useMutation({
    mutationFn: (body: ManualFindingRequest) => {
      if (!headers) throw new Error('No active identity selected.')
      return apiFetch<ManualFindingResponse>(`/api/calls/${callId}/violations`, { method: 'POST', body, headers })
    },
    onSuccess: refresh,
  })
}

export function useCallAudit(callId: string | undefined, headers: RoleHeaders | null, enabled: boolean) {
  return useQuery({
    queryKey: ['call-audit', headers?.role, headers?.userId, callId],
    queryFn: () => apiFetch<AuditEntry[]>(`/api/calls/${callId}/audit`, { headers: headers ?? undefined }),
    enabled: enabled && headers !== null && callId !== undefined,
  })
}

export function useCompleteReview(callId: string, headers: RoleHeaders | null) {
  const refresh = useRefreshCall()
  return useMutation({
    mutationFn: () => {
      if (!headers) throw new Error('No active identity selected.')
      return apiFetch<ReviewCompleteResponse>(`/api/calls/${callId}/review/complete`, { method: 'POST', headers })
    },
    onSuccess: refresh,
  })
}
