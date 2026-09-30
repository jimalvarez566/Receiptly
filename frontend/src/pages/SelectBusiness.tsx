import { useState } from 'react'
import type { FormEvent } from 'react'
import { useAuth } from '@/auth/authContext'
import { useTenant } from '@/auth/tenantContext'
import { api } from '@/lib/api'
import { Button } from '@/components/ui/button'

const inputClass =
  'w-full rounded border border-[#262626] bg-[#111111] px-3 py-2 text-sm text-[#e5e5e5] outline-none focus:border-[#404040]'

export function SelectBusiness() {
  const { signOut } = useAuth()
  const { memberships, setActiveTenant, refreshMemberships } = useTenant()
  const [name, setName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const hasBusinesses = memberships.length > 0

  const onCreate = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const created = await api.createTenant(name.trim())
      await refreshMemberships()
      setActiveTenant(created.id)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create business')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-6 bg-[#0a0a0a] p-8 text-[#e5e5e5]">
      <div className="w-96">
        {hasBusinesses && (
          <>
            <h1 className="mb-3 text-sm text-[#737373]">Select a business</h1>
            <ul className="mb-8 flex flex-col gap-2">
              {memberships.map((m) => (
                <li key={m.tenant_id}>
                  <button
                    onClick={() => setActiveTenant(m.tenant_id)}
                    className="flex w-full items-center justify-between rounded border border-[#262626] bg-[#0f0f0f] px-4 py-3 text-left text-sm hover:border-[#404040]"
                  >
                    <span>{m.name}</span>
                    <span className="text-xs text-[#737373]">{m.role}</span>
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}

        <h2 className="mb-3 text-sm text-[#737373]">
          {hasBusinesses ? 'Or create a new business' : 'Create your first business'}
        </h2>
        <form onSubmit={onCreate} className="flex flex-col gap-3">
          <input
            className={inputClass}
            placeholder="Business name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
          {error && <p className="text-xs text-red-400">{error}</p>}
          <Button type="submit" size="lg" disabled={busy || !name.trim()}>
            {busy ? 'Creating…' : 'Create business'}
          </Button>
        </form>
      </div>

      <button onClick={signOut} className="text-xs text-[#737373] underline">
        Sign out
      </button>
    </div>
  )
}
