import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '@/lib/api'
import type { Invitation, InviteRole, InviteStatus } from '@/lib/api'
import { useTenant } from '@/auth/tenantContext'

const STATUS_STYLES: Record<InviteStatus, string> = {
  pending: 'border-green-900 text-green-400',
  used: 'border-[#262626] text-[#a3a3a3]',
  revoked: 'border-red-900 text-red-400',
  expired: 'border-yellow-900 text-yellow-400',
}

const fmt = (iso: string) => new Date(iso).toLocaleString()

export function Team() {
  const { activeTenantId } = useTenant()
  const [invites, setInvites] = useState<Invitation[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [role, setRole] = useState<InviteRole>('member')
  const [creating, setCreating] = useState(false)
  const [link, setLink] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [confirmId, setConfirmId] = useState<number | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)
  const linkRef = useRef<HTMLInputElement>(null)

  const refresh = useCallback(async () => {
    if (activeTenantId == null) return
    try {
      setInvites(await api.listInvitations(activeTenantId))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load invitations')
    } finally {
      setLoading(false)
    }
  }, [activeTenantId])

  useEffect(() => {
    if (activeTenantId == null) return
    api
      .listInvitations(activeTenantId)
      .then((data) => setInvites(data))
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load invitations'))
      .finally(() => setLoading(false))
  }, [activeTenantId])

  const create = async () => {
    if (activeTenantId == null) return
    setError(null)
    setCopied(false)
    setCreating(true)
    try {
      const created = await api.createInvitation(activeTenantId, role)
      setLink(`${window.location.origin}/join#token=${created.token}`)
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create invite link')
    } finally {
      setCreating(false)
    }
  }

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(link ?? '')
      setCopied(true)
    } catch {
      linkRef.current?.focus()
      linkRef.current?.select()
    }
  }

  const revoke = async (inv: Invitation) => {
    if (activeTenantId == null) return
    setError(null)
    setBusyId(inv.id)
    try {
      await api.revokeInvitation(activeTenantId, inv.id)
      setConfirmId(null)
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to revoke invite link')
    } finally {
      setBusyId(null)
    }
  }

  const btn =
    'text-xs px-3 py-1 rounded border border-[#262626] text-[#e5e5e5] hover:bg-[#1a1a1a] transition-colors disabled:opacity-30 disabled:hover:bg-transparent'

  return (
    <div className="flex flex-col h-full">
      <div className="px-8 py-5 border-b border-[#262626]">
        <h1 className="text-base font-semibold text-white">Team</h1>
        <p className="text-xs text-[#737373] mt-0.5">Invite people to this business with a one-time link.</p>
      </div>

      <div className="px-8 py-4 border-b border-[#262626] flex flex-col gap-3">
        <div className="flex items-center gap-2">
          <label htmlFor="invite-role" className="text-xs text-[#737373]">
            Role
          </label>
          <select
            id="invite-role"
            value={role}
            onChange={(e) => setRole(e.target.value as InviteRole)}
            disabled={creating}
            className="rounded border border-[#262626] bg-[#111111] px-2 py-1 text-xs text-[#e5e5e5] outline-none focus:border-[#404040]"
          >
            <option value="member">Member</option>
            <option value="admin">Admin</option>
          </select>
          <button className={btn} disabled={creating} onClick={create}>
            {creating ? 'Creating…' : 'Create invite link'}
          </button>
        </div>

        {link && (
          <div className="flex flex-col gap-1.5">
            <div className="flex items-center gap-2">
              <input
                ref={linkRef}
                readOnly
                value={link}
                aria-label="Invite link"
                onFocus={(e) => e.currentTarget.select()}
                className="flex-1 rounded border border-[#262626] bg-[#111111] px-2 py-1.5 text-xs text-[#e5e5e5] mono outline-none focus:border-[#404040]"
              />
              <button className={btn} onClick={copy}>
                {copied ? 'Copied' : 'Copy'}
              </button>
            </div>
            <p className="text-xs text-[#737373]">
              This link is shown once. Anyone who has it can join once, until it expires or you revoke it.
            </p>
          </div>
        )}
      </div>

      {error && <div className="px-8 py-3 text-sm text-red-400">{error}</div>}

      <div className="flex-1 overflow-auto">
        {loading ? (
          <div className="p-8 text-sm text-[#737373]">Loading…</div>
        ) : invites.length === 0 ? (
          <div className="p-8 text-sm text-[#737373]">No invite links yet.</div>
        ) : (
          <ul>
            {invites.map((inv) => {
              const busy = busyId === inv.id
              return (
                <li key={inv.id} className="px-8 py-3 border-b border-[#1a1a1a] flex items-center gap-4">
                  <span className="text-sm text-[#e5e5e5] w-20 capitalize">{inv.role}</span>
                  <span className={`text-xs px-2 py-0.5 rounded border ${STATUS_STYLES[inv.status]}`}>
                    {inv.status}
                  </span>
                  <div className="flex-1 min-w-0 text-xs text-[#737373]">
                    Created {fmt(inv.created_at)} · Expires {fmt(inv.expires_at)}
                  </div>
                  {inv.status === 'pending' &&
                    (confirmId === inv.id ? (
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-[#e5e5e5]">
                          Revoke this invite link? Anyone who hasn't used it yet will no longer be able to.
                        </span>
                        <button className={btn} disabled={busy} onClick={() => setConfirmId(null)}>
                          Cancel
                        </button>
                        <button
                          className="text-xs px-3 py-1 rounded border border-red-900 text-red-400 hover:bg-red-950 transition-colors disabled:opacity-30"
                          disabled={busy}
                          onClick={() => revoke(inv)}
                        >
                          Revoke
                        </button>
                      </div>
                    ) : (
                      <button
                        className={btn}
                        disabled={busy}
                        onClick={() => {
                          setError(null)
                          setConfirmId(inv.id)
                        }}
                      >
                        Revoke
                      </button>
                    ))}
                </li>
              )
            })}
          </ul>
        )}
      </div>
    </div>
  )
}
