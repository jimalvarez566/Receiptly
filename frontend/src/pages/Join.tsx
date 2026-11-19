import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import type { ReactNode } from 'react'
import { useAuth } from '@/auth/authContext'
import { useTenant } from '@/auth/tenantContext'
import { api, ApiError } from '@/lib/api'
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
  const [error, setError] = useState<{ message: string; final: boolean } | null>(null)
  const [attempt, setAttempt] = useState(0)
  const [joinedNoRefresh, setJoinedNoRefresh] = useState<string | null>(null)
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
        // Only a definitive answer (not found / gone) discards the token.
        const final = err instanceof ApiError && (err.status === 404 || err.status === 410)
        if (final) clearPendingInvite()
        setError({
          message: err instanceof Error ? err.message : 'Could not load this invitation',
          final,
        })
      })
    return () => {
      active = false
    }
  }, [token, attempt])

  // Escape hatch: clear the token first so the redirect gate doesn't bounce back.
  const onDiscard = () => {
    clearPendingInvite()
    navigate(session != null ? '/' : '/login', { replace: true })
  }

  const onRetry = () => {
    setError(null)
    setAttempt((a) => a + 1)
  }

  const loggedIn = session != null

  const onJoin = async () => {
    if (!token) return
    setBusy(true)
    let result: InvitationAccepted
    try {
      result = await api.acceptInvitation(token)
    } catch (err) {
      // 401: api.ts is already signing out and redirecting to /login. Keep the
      // token so the invite resumes after login.
      if (err instanceof ApiError && err.status === 401) return
      clearPendingInvite()
      setError({
        message: err instanceof Error ? err.message : 'Could not accept this invitation',
        final: true,
      })
      setBusy(false)
      return
    }
    clearPendingInvite()
    try {
      await refreshMemberships()
    } catch {
      setJoinedNoRefresh(result.tenant_name)
      setBusy(false)
      return
    }
    if (result.already_member) {
      setAccepted(result)
      setBusy(false)
      return
    }
    setActiveTenant(result.tenant_id)
    navigate('/', { replace: true })
  }

  const onContinueMember = () => {
    if (!accepted) return
    setActiveTenant(accepted.tenant_id)
    navigate('/', { replace: true })
  }

  if (joinedNoRefresh) {
    return (
      <Panel>
        <p className="text-sm">
          You joined {joinedNoRefresh}, but we couldn't load your businesses. Reload the page to
          continue.
        </p>
        <Button size="lg" onClick={() => window.location.reload()}>
          Reload
        </Button>
      </Panel>
    )
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
        <p className="text-sm text-red-400">{error.message}</p>
        {error.final ? (
          <Link to="/" replace className="text-sm underline">
            Continue to the app
          </Link>
        ) : (
          <div className="flex flex-col gap-2">
            <Button size="lg" onClick={onRetry}>
              Try again
            </Button>
            <Button size="lg" variant="outline" onClick={onDiscard}>
              Discard invite and continue
            </Button>
          </div>
        )}
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
        <div className="flex flex-col gap-2">
          <Button size="lg" onClick={onJoin} disabled={busy}>
            {busy ? 'Joining…' : `Join ${preview.tenant_name}`}
          </Button>
          <Button size="lg" variant="outline" onClick={onDiscard} disabled={busy}>
            No thanks, continue to the app
          </Button>
        </div>
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
