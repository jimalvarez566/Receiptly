import { useState } from 'react'
import type { FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '@/auth/authContext'
import { Button } from '@/components/ui/button'
import logo from '@/assets/logo.png'

const inputClass =
  'w-full rounded border border-[#262626] bg-[#111111] px-3 py-2 text-sm text-[#e5e5e5] outline-none focus:border-[#404040]'

export function Login() {
  const { signIn } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    const { error } = await signIn(email, password)
    setBusy(false)
    if (error) setError(error)
    // success: onAuthStateChange re-renders into the authed tree
  }

  return (
    <div className="flex h-screen items-center justify-center bg-[#0a0a0a] text-[#e5e5e5]">
      <div className="w-80 rounded-lg border border-[#262626] bg-[#0f0f0f] p-6">
        <img src={logo} alt="Receiptly" className="mx-auto mb-4 h-16 w-auto mix-blend-screen" />
        <h1 className="mb-4 text-center text-sm text-[#737373]">Log in to continue</h1>
        <form onSubmit={onSubmit} className="flex flex-col gap-3">
          <input
            className={inputClass}
            type="email"
            placeholder="Email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
          <input
            className={inputClass}
            type="password"
            placeholder="Password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
          {error && <p className="text-xs text-red-400">{error}</p>}
          <Button type="submit" size="lg" disabled={busy}>
            {busy ? 'Logging in…' : 'Log in'}
          </Button>
        </form>
        <p className="mt-4 text-center text-xs text-[#737373]">
          No account?{' '}
          <Link to="/signup" className="text-[#e5e5e5] underline">
            Sign up
          </Link>
        </p>
      </div>
    </div>
  )
}
