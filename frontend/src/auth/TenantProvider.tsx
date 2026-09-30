import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { api } from '@/lib/api'
import type { Membership } from '@/lib/api'
import { getActiveTenantId, setActiveTenantId } from '@/lib/activeTenant'
import { TenantContext } from '@/auth/tenantContext'
import type { TenantContextValue } from '@/auth/tenantContext'

export function TenantProvider({ children }: { children: ReactNode }) {
  const [memberships, setMemberships] = useState<Membership[]>([])
  const [loading, setLoading] = useState(true)
  const [activeTenantId, setActive] = useState<number | null>(null)

  const load = useCallback(async () => {
    const me = await api.getMe()
    setMemberships(me.memberships)
    const stored = getActiveTenantId()
    const stillValid = stored != null && me.memberships.some((m) => m.tenant_id === stored)
    setActive(stillValid ? stored : null)
    if (!stillValid) setActiveTenantId(null)
  }, [])

  useEffect(() => {
    let active = true
    load()
      .catch(() => {
        /* getMe failure (e.g. transient 5xx). 401 already redirected in api.ts. */
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [load])

  const value = useMemo<TenantContextValue>(
    () => ({
      memberships,
      loading,
      activeTenantId,
      setActiveTenant: (id) => {
        setActiveTenantId(id)
        setActive(id)
      },
      refreshMemberships: load,
    }),
    [memberships, loading, activeTenantId, load],
  )

  return <TenantContext.Provider value={value}>{children}</TenantContext.Provider>
}
