import { createContext, useContext } from 'react'
import type { Membership } from '@/lib/api'

export interface TenantContextValue {
  memberships: Membership[]
  loading: boolean
  activeTenantId: number | null
  setActiveTenant: (id: number) => void
  refreshMemberships: () => Promise<void>
}

export const TenantContext = createContext<TenantContextValue | null>(null)

export function useTenant(): TenantContextValue {
  const ctx = useContext(TenantContext)
  if (!ctx) throw new Error('useTenant must be used within <TenantProvider>')
  return ctx
}

export function useRole(): { role: 'admin' | 'member' | null; isAdmin: boolean } {
  const { memberships, activeTenantId } = useTenant()
  const role = memberships.find((m) => m.tenant_id === activeTenantId)?.role ?? null
  return { role, isAdmin: role === 'admin' }
}
