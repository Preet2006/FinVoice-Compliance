import { useMutation, useQueryClient } from '@tanstack/react-query'
import { apiFetch, type RoleHeaders } from './client'
import type { ReviewRequest, ReviewResponse } from './types'

export function useSubmitReview(headers: RoleHeaders | null) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ violationId, body }: { violationId: string; body: ReviewRequest }) => {
      if (!headers) throw new Error('No active identity selected.')
      return apiFetch<ReviewResponse>(`/api/violations/${violationId}/review`, {
        method: 'POST',
        body,
        headers,
      })
    },
    onSuccess: () => {
      // Refetch the call's violations and the call itself (score changed)
      // rather than hand-rolling an optimistic merge — these are small
      // per-call lists, so a refetch is simple and always correct.
      queryClient.invalidateQueries({ queryKey: ['call-violations'] })
      queryClient.invalidateQueries({ queryKey: ['call'] })
      queryClient.invalidateQueries({ queryKey: ['calls'] }) // review status and counts in the list
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    },
  })
}
