import { Routes, Route, Navigate } from 'react-router-dom'
import { AppShell } from './components/layout/AppShell'
import { CallListPage } from './pages/CallListPage'
import { CallReviewPage } from './pages/CallReviewPage'
import { UploadPage } from './pages/UploadPage'
import { DashboardPage } from './pages/DashboardPage'
import { LoginPage } from './pages/LoginPage'
import { useRoleContext } from './context/RoleContext'

function ProtectedShell() {
  const { user, loading } = useRoleContext()
  if (loading) return <p style={{ padding: 24 }}>Loading session…</p>
  if (!user) return <Navigate to="/login" replace />
  return <AppShell />
}

function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<ProtectedShell />}>
        <Route path="/" element={<Navigate to="/calls" replace />} />
        <Route path="/calls" element={<CallListPage />} />
        <Route path="/calls/:callId" element={<CallReviewPage />} />
        <Route path="/upload" element={<UploadPage />} />
        <Route path="/dashboard" element={<DashboardPage />} />
      </Route>
    </Routes>
  )
}

export default App
