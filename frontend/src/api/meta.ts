import { useQuery } from '@tanstack/react-query'
import { apiFetch } from './client'
import type { Office, Role, User } from './types'

export function useOffices() {
  return useQuery({
    queryKey: ['meta', 'offices'],
    queryFn: () => apiFetch<Office[]>('/api/meta/offices'),
  })
}

export function useUsers(role?: Role) {
  return useQuery({
    queryKey: ['meta', 'users', role],
    queryFn: () => apiFetch<User[]>('/api/meta/users', { params: { role } }),
  })
}
