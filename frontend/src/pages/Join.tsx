import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import type { ReactNode } from 'react'
import { useAuth } from '@/auth/authContext'
import { useTenant } from '@/auth/tenantContext'
import { api } from '@/lib/api'
import type { InvitationAccepted, InvitationPreview } from '@/lib/api'
import { parseUtc } from '@/lib/dates'
import { clearPendingInvite, getPendingInvite, setPendingInvite } from '@/lib/pendingInvite'
import { Button } from '@/components/ui/button'

// The token lives in the URL fragment so servers never see it. Read it from
// window.location (the router's location keeps the stale hash after replaceState).
function readFragmentToken(): string | null {
  return new URLSearchParams(window.location.hash.slice(1)).get('token') || null
}

function Panel({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-[#0a0a0a] p-8 text-[#e5e5e5]">
      <div className="flex w-96 flex-col gap-4 rounded-lg border border-[#262626] bg-[#0f0f0f] p-6">
        {children}
      </div>
    </div>
  )
}

export function Join() {
  const { session } = useAuth()
  const { setActiveTenant, refreshMemberships } = useTenant()
  const navigate = useNavigate()
  const [fragmentToken] = useState(readFragmentToken)
  const [token] = useState(() => fragmentToken ?? getPendingInvite())
  const [preview, setPreview] = useState<InvitationPreview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [accepted, setAccepted] = useState<InvitationAccepted | null>(null)

  // Persist the fragment token for the signup/login hand-off, then strip it.
  useEffect(() => {
    if (!readFragmentToken()) return
    if (fragmentToken) setPendingInvite(fragmentToken)
    window.history.replaceState(
      window.history.state,
      '',
      window.location.pathname + window.location.search,
    )
  }, [fragmentToken])

  useEffect(() => {
    if (!token) return
    let active = true
    api
      .previewInvitation(token)
      .then((p) => {
        if (active) setPreview(p)
      })
      .catch((err) => {
        if (!active) return
        clearPendingInvite()
        setError(err instanceof Error ? err.message : 'Could not load this invitation')
      })
    return () => {
      active = false
    }
  }, [token])

  const loggedIn = session != null

  const onJoin = async () => {
    if (!token) return
    setBusy(true)
    try {
      const result = await api.acceptInvitation(token)
      await refreshMemberships()
      clearPendingInvite()
      if (result.already_member) {
        setAccepted(result)
        setBusy(false)
        return
      }
      setActiveTenant(result.tenant_id)
      navigate('/', { replace: true })
    } catch (err) {
      clearPendingInvite()
      setError(err instanceof Error ? err.message : 'Could not accept this invitation')
      setBusy(false)
    }
  }

  const onContinueMember = () => {
    if (!accepted) return
    setActiveTenant(accepted.tenant_id)
    navigate('/', { replace: true })
  }

  if (accepted) {
    return (
      <Panel>
        <p className="text-sm">You're already a member of {accepted.tenant_name}.</p>
        <Button size="lg" onClick={onContinueMember}>
          Continue
        </Button>
      </Panel>
    )
  }

  if (!token) {
    return (
      <Panel>
        <p className="text-sm">This invitation link isn't valid.</p>
        <Button size="lg" onClick={() => navigate(loggedIn ? '/' : '/login', { replace: true })}>
          Continue
        </Button>
      </Panel>
    )
  }

  if (error) {
    return (
      <Panel>
        <p className="text-sm text-red-400">{error}</p>
        <Link to="/" replace className="text-sm underline">
          Continue to the app
        </Link>
      </Panel>
    )
  }

  if (!preview) {
    return (
      <Panel>
        <p className="text-sm text-[#737373]">Loading invitation…</p>
      </Panel>
    )
  }

  return (
    <Panel>
      <p className="text-sm">
        You've been invited to <strong>{preview.tenant_name}</strong> as {preview.role}.
      </p>
      <p className="text-xs text-[#737373]">
        Expires {parseUtc(preview.expires_at).toLocaleString()}
      </p>
      {loggedIn ? (
        <Button size="lg" onClick={onJoin} disabled={busy}>
          {busy ? 'Joining…' : `Join ${preview.tenant_name}`}
        </Button>
      ) : (
        <div className="flex flex-col gap-2">
          <Button size="lg" onClick={() => navigate('/signup')} disabled={busy}>
            Create an account
          </Button>
          <Button size="lg" variant="outline" onClick={() => navigate('/login')} disabled={busy}>
            Log in
          </Button>
        </div>
      )}
    </Panel>
  )
}
