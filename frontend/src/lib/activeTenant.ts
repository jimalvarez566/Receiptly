const KEY = 'activeTenantId'

export function getActiveTenantId(): number | null {
  try {
    const v = localStorage.getItem(KEY)
    return v ? Number(v) : null
  } catch {
    return null
  }
}

export function setActiveTenantId(id: number | null): void {
  try {
    if (id == null) localStorage.removeItem(KEY)
    else localStorage.setItem(KEY, String(id))
  } catch {
    /* private mode / storage disabled — ignore */
  }
}
