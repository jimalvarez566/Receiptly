const KEY = 'pendingInviteToken'

export function getPendingInvite(): string | null {
  try {
    return localStorage.getItem(KEY) || null
  } catch {
    return null
  }
}

export function setPendingInvite(token: string): void {
  try {
    localStorage.setItem(KEY, token)
  } catch {
    /* private mode / storage disabled — ignore */
  }
}

export function clearPendingInvite(): void {
  try {
    localStorage.removeItem(KEY)
  } catch {
    /* private mode / storage disabled — ignore */
  }
}
