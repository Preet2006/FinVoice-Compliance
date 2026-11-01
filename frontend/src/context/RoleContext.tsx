import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import type { Role, AuthUser } from '../api/types'
import { currentUser, login as loginRequest, logout as logoutRequest } from '../api/auth'

interface RoleContextValue {
  user: AuthUser | null
  loading: boolean
  role: Role
  activeUserId: string | null
  setRole: (role: Role) => void
  setActiveUserId: (userId: string | null) => void
  login: (userId: string, password: string) => Promise<void>
  logout: () => Promise<void>
}

const RoleContext = createContext<RoleContextValue | undefined>(undefined)

export function RoleProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    currentUser().then((result) => setUser(result.user)).catch(() => setUser(null)).finally(() => setLoading(false))
  }, [])

  const login = async (userId: string, password: string) => {
    const result = await loginRequest(userId, password)
    setUser(result.user)
  }

  const logout = async () => {
    await logoutRequest()
    setUser(null)
  }

  return (
    <RoleContext.Provider value={{
      user,
      loading,
      role: user?.role ?? 'caller',
      activeUserId: user?.id ?? null,
      // Compatibility no-ops for existing page props. Identity is now
      // exclusively supplied by the authenticated session.
      setRole: () => undefined,
      setActiveUserId: () => undefined,
      login,
      logout,
    }}>
      {children}
    </RoleContext.Provider>
  )
}

export function useRoleContext(): RoleContextValue {
  const context = useContext(RoleContext)
  if (!context) throw new Error('useRoleContext must be used within RoleProvider')
  return context
}
