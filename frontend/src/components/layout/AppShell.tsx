import { NavLink, Outlet } from 'react-router-dom'
import { RoleSwitcher } from './RoleSwitcher'
import { useRoleContext } from '../../context/RoleContext'

const navLinkStyle = (isActive: boolean) => ({
  color: isActive ? 'var(--brand)' : 'var(--text-secondary)',
  fontWeight: isActive ? 600 : 400,
  textDecoration: 'none',
})

export function AppShell() {
  const { role, user, logout } = useRoleContext()
  return (
    <div style={{ minHeight: '100vh', backgroundColor: 'var(--surface-0)' }}>
      <header
        style={{
          backgroundColor: 'var(--surface-1)',
          borderBottom: '1px solid var(--border)',
          padding: '16px 24px',
        }}
      >
        <div
          style={{
            maxWidth: 'var(--app-max-width, 1440px)',
            margin: '0 auto',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: '24px',
            flexWrap: 'wrap',
          }}
        >
          <div>
            <h1 style={{ fontSize: 'var(--text-xl)', fontWeight: 600, color: 'var(--brand)' }}>
              Shaha Finlease — Compliance Monitoring
            </h1>
            <p style={{ fontSize: 'var(--text-xs)', color: 'var(--text-secondary)', marginTop: '4px' }}>
              {user?.name} · {user?.role}
            </p>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <RoleSwitcher />
            <button onClick={() => void logout()}>Sign out</button>
          </div>
        </div>
      </header>

      <nav
        style={{
          backgroundColor: 'var(--surface-1)',
          borderBottom: '1px solid var(--border)',
          padding: '12px 24px',
        }}
      >
        <div style={{ maxWidth: '1440px', margin: '0 auto', display: 'flex', gap: '24px' }}>
          <NavLink to="/calls" style={({ isActive }) => navLinkStyle(isActive)}>
            Calls
          </NavLink>
          {role !== 'caller' && (
            <NavLink to="/upload" style={({ isActive }) => navLinkStyle(isActive)}>
              Upload
            </NavLink>
          )}
          <NavLink to="/dashboard" style={({ isActive }) => navLinkStyle(isActive)}>
            Dashboard
          </NavLink>
        </div>
      </nav>

      <main style={{ maxWidth: '1440px', margin: '0 auto', padding: '24px' }}>
        <Outlet />
      </main>
    </div>
  )
}
