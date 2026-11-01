import { useQuery } from '@tanstack/react-query'
import { apiFetch, type RoleHeaders } from './client'
import type { Dashboard } from './types'

export function useDashboard(headers: RoleHeaders | null) {
  return useQuery({
    // Role and user are part of the key: switching identity never shows another identity's numbers.
    queryKey: ['dashboard', headers?.role, headers?.userId],
    queryFn: () => apiFetch<Dashboard>('/api/dashboard', { headers: headers ?? undefined }),
    enabled: headers !== null,
  })
}
