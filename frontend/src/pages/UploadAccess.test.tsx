import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { UploadPage } from './UploadPage'
import { AppShell } from '../components/layout/AppShell'

const identity = { role: 'manager' as 'manager' | 'head' | 'caller' }
vi.mock('../context/RoleContext', () => ({
  useRoleContext: () => ({ role: identity.role, activeUserId: 'u-1', setRole: vi.fn(), setActiveUserId: vi.fn() }),
}))
vi.mock('../components/layout/RoleSwitcher', () => ({ RoleSwitcher: () => <div /> }))

afterEach(() => cleanup())

function app(path: string) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/calls" element={<p>calls list</p>} />
            <Route path="/upload" element={<UploadPage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('upload access by role', () => {
  it.each(['manager', 'head'] as const)('%s sees the Upload link and the upload form', (role) => {
    identity.role = role
    app('/upload')
    expect(screen.getByRole('link', { name: 'Upload' })).toBeInTheDocument()
    expect(screen.queryByTestId('upload-denied')).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Upload Calls' })).toBeInTheDocument()
  })

  it('a caller has no Upload link', () => {
    identity.role = 'caller'
    app('/calls')
    expect(screen.queryByRole('link', { name: 'Upload' })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Calls' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Dashboard' })).toBeInTheDocument()
  })

  it('a caller who opens /upload directly gets an explanation, not the form', () => {
    identity.role = 'caller'
    app('/upload')
    expect(screen.getByTestId('upload-denied')).toHaveTextContent('Only a manager or the head of collections can upload')
    expect(screen.queryByRole('heading', { name: 'Upload Calls' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Start Processing Batch' })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Go to Calls' })).toHaveAttribute('href', '/calls')
  })
})
