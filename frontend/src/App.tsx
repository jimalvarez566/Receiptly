import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { AuthProvider } from '@/auth/AuthProvider'
import { useAuth } from '@/auth/authContext'
import { TenantProvider } from '@/auth/TenantProvider'
import { useTenant, useRole } from '@/auth/tenantContext'
import { FullScreenSpinner } from '@/components/FullScreenSpinner'
import { Layout } from '@/components/Layout'
import { Login } from '@/pages/Login'
import { Signup } from '@/pages/Signup'
import { SelectBusiness } from '@/pages/SelectBusiness'
import { Upload } from '@/pages/Upload'
import { ReceiptsList } from '@/pages/ReceiptsList'
import { ReceiptDetail } from '@/pages/ReceiptDetail'
import { Dashboard } from '@/pages/Dashboard'
import { Policies } from '@/pages/Policies'
import { Team } from '@/pages/Team'
import { Join } from '@/pages/Join'
import { getPendingInvite } from '@/lib/pendingInvite'

function AdminOnly({ children }: { children: React.ReactNode }) {
  const { isAdmin } = useRole()
  return isAdmin ? <>{children}</> : <Navigate to="/" replace />
}

// A logged-in user with a stored invite token is sent to /join once; Join clears
// the token, so after a join (or an error) this no longer redirects.
function PendingInviteRedirect() {
  const { pathname } = useLocation()
  if (pathname !== '/join' && getPendingInvite() != null) return <Navigate to="/join" replace />
  return null
}

function AuthedRoutes() {
  const { loading, activeTenantId } = useTenant()

  if (loading) return <FullScreenSpinner />

  if (activeTenantId == null) {
    return (
      <>
        <PendingInviteRedirect />
        <Routes>
          <Route path="/join" element={<Join />} />
          <Route path="*" element={<SelectBusiness />} />
        </Routes>
      </>
    )
  }

  return (
    <>
      <PendingInviteRedirect />
      <Routes>
        <Route path="/join" element={<Join />} />
        <Route path="/select-business" element={<SelectBusiness />} />
        <Route element={<Layout />}>
          <Route path="/" element={<Dashboard />} />
          <Route path="/receipts" element={<ReceiptsList />} />
          <Route path="/receipts/:id" element={<ReceiptDetail />} />
          <Route path="/upload" element={<Upload />} />
          <Route
            path="/policies"
            element={
              <AdminOnly>
                <Policies />
              </AdminOnly>
            }
          />
          <Route
            path="/team"
            element={
              <AdminOnly>
                <Team />
              </AdminOnly>
            }
          />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </>
  )
}

function AppRoutes() {
  const { loading, session } = useAuth()

  if (loading) return <FullScreenSpinner />

  if (!session) {
    return (
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/signup" element={<Signup />} />
        <Route path="/join" element={<Join />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    )
  }

  return (
    <TenantProvider>
      <AuthedRoutes />
    </TenantProvider>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
    </AuthProvider>
  )
}
