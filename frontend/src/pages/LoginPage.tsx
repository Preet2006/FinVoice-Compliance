import { FormEvent, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError } from '../api/client'
import { useRoleContext } from '../context/RoleContext'

export function LoginPage() {
  const { login } = useRoleContext()
  const navigate = useNavigate()
  const [userId, setUserId] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [showPassword, setShowPassword] = useState(false)

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await login(userId, password)
      navigate('/calls', { replace: true })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Unable to sign in.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="login-page">
      <div className="login-brandbar">
        <div className="login-brandmark" aria-hidden="true"><span>SF</span></div>
        <span className="login-brandname">Shaha <strong>Finlease</strong></span>
        <span className="login-brandtag">Speech Analytics &amp; Compliance</span>
      </div>

      <div className="login-layout">
        <section className="login-intro" aria-label="Product introduction">
          <div className="login-eyebrow"><span className="login-live-dot" /> Compliance operations</div>
          <h1>Speech analytics<br /><em>for better review.</em></h1>
          <p className="login-intro-copy">
            Review collection calls, investigate findings, and keep every compliance decision traceable.
          </p>
          <div className="login-highlights">
            <div><span className="login-highlight-icon">✓</span><span><strong>Evidence-led review</strong><small>Find the exact moment that matters</small></span></div>
            <div><span className="login-highlight-icon">↗</span><span><strong>Team visibility</strong><small>See calls, findings, and scores in one place</small></span></div>
          </div>
          <div className="login-rule" />
          <p className="login-legal">For responsible recovery operations<br />and fair-practice compliance.</p>
        </section>

        <section className="login-card" aria-labelledby="login-title">
          <div className="login-card-header">
            <div className="login-lock" aria-hidden="true">⌁</div>
            <div>
              <p className="login-card-kicker">Welcome back</p>
              <h2 id="login-title">Sign in to your workspace</h2>
            </div>
          </div>
          <p className="login-card-subtitle">Use your assigned account to continue.</p>

          <form onSubmit={submit} className="login-form">
            <div className="login-field">
              <label htmlFor="login-user-id">User ID</label>
              <div className="login-input-wrap">
                <span className="login-input-icon" aria-hidden="true">◎</span>
                <input id="login-user-id" autoComplete="username" value={userId} onChange={(e) => setUserId(e.target.value)} placeholder="Enter your user ID" required />
              </div>
            </div>
            <div className="login-field">
              <div className="login-label-row"><label htmlFor="login-password">Password</label><span className="login-secure-label">Protected session</span></div>
              <div className="login-input-wrap">
                <span className="login-input-icon" aria-hidden="true">▣</span>
                <input id="login-password" type={showPassword ? 'text' : 'password'} autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="Enter your password" required />
                <button type="button" className="login-password-toggle" onClick={() => setShowPassword((visible) => !visible)} aria-label={showPassword ? 'Hide password' : 'Show password'}>{showPassword ? 'Hide' : 'Show'}</button>
              </div>
            </div>
            {error && <div className="login-error" role="alert"><span aria-hidden="true">!</span><span>{error}</span></div>}
            <button className="login-submit" type="submit" disabled={busy}><span>{busy ? 'Signing in…' : 'Sign in securely'}</span><span aria-hidden="true">→</span></button>
          </form>
          <p className="login-card-footer"><span className="login-shield" aria-hidden="true">◆</span> Your session is encrypted and role-protected.</p>
        </section>
      </div>
      <footer className="login-footer"><span>© 2026 Shaha Finlease</span><span>Internal operations platform</span></footer>
    </main>
  )
}
