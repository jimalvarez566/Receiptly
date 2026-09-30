import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AuthProvider } from '@/auth/AuthProvider'
import { useAuth } from '@/auth/authContext'
import { TenantProvider } from '@/auth/TenantProvider'
import { useTenant } from '@/auth/tenantContext'
import { FullScreenSpinner } from '@/components/FullScreenSpinner'
import { Layout } from '@/components/Layout'
import { Login } from '@/pages/Login'
import { Signup } from '@/pages/Signup'
import { SelectBusiness } from '@/pages/SelectBusiness'
import { Upload } from '@/pages/Upload'
import { ReceiptsList } from '@/pages/ReceiptsList'
import { ReceiptDetail } from '@/pages/ReceiptDetail'
import { Dashboard } from '@/pages/Dashboard'

function AuthedRoutes() {
  const { loading, activeTenantId } = useTenant()

  if (loading) return <FullScreenSpinner />

  if (activeTenantId == null) {
    return (
      <Routes>
        <Route path="*" element={<SelectBusiness />} />
      </Routes>
    )
  }

  return (
    <Routes>
      <Route path="/select-business" element={<SelectBusiness />} />
      <Route element={<Layout />}>
        <Route path="/" element={<Dashboard />} />
        <Route path="/receipts" element={<ReceiptsList />} />
        <Route path="/receipts/:id" element={<ReceiptDetail />} />
        <Route path="/upload" element={<Upload />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
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
