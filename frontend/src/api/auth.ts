import { apiFetch } from './client'
import type { AuthUser } from './types'

export interface AuthResponse {
  user: AuthUser
  expires_at?: string
}

export function login(userId: string, password: string) {
  return apiFetch<AuthResponse>('/api/auth/login', { method: 'POST', body: { user_id: userId, password } })
}

export function currentUser() {
  return apiFetch<AuthResponse>('/api/auth/me')
}

export function logout() {
  return apiFetch<{ ok: boolean }>('/api/auth/logout', { method: 'POST' })
}
