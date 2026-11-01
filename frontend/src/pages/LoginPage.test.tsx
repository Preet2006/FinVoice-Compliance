import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { LoginPage } from './LoginPage'

const login = vi.fn()
vi.mock('../context/RoleContext', () => ({ useRoleContext: () => ({ login }) }))

afterEach(() => {
  cleanup()
  login.mockReset()
})

function renderPage() {
  return render(
    <MemoryRouter>
      <LoginPage />
    </MemoryRouter>,
  )
}

describe('LoginPage', () => {
  it('submits the user ID and password to the authentication context', async () => {
    login.mockResolvedValue(undefined)
    renderPage()
    const user = userEvent.setup()
    await user.type(screen.getByLabelText('User ID'), 'u-head')
    await user.type(screen.getByLabelText('Password'), 'a secure demo password')
    await user.click(screen.getByRole('button', { name: /Sign in securely/ }))
    expect(login).toHaveBeenCalledWith('u-head', 'a secure demo password')
  })

  it('shows a generic error when login fails', async () => {
    login.mockRejectedValue(new Error('bad login'))
    renderPage()
    const user = userEvent.setup()
    await user.type(screen.getByLabelText('User ID'), 'u-head')
    await user.type(screen.getByLabelText('Password'), 'wrong password')
    await user.click(screen.getByRole('button', { name: /Sign in securely/ }))
    expect(screen.getByRole('alert')).toHaveTextContent('Unable to sign in.')
  })
})
