# Frontend Auth Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Supabase signup/login and active-business selection to the React frontend so it can authenticate against the multi-tenant backend.

**Architecture:** `supabase-js` owns the session (auto-refreshed). An `AuthProvider` context exposes session + `signIn`/`signUp`/`signOut`. A `TenantProvider` (mounted only when authenticated) loads the user's businesses from `GET /api/v1/auth/me` and tracks the active one, persisted in `localStorage`. `api.ts` attaches `Authorization: Bearer <token>` and `X-Tenant-ID` to every request via a `localStorage`-backed helper (no React coupling). Routing branches: loading → spinner; no session → login/signup; session but no active business → select-business screen; otherwise the existing app.

**Tech Stack:** React 19, react-router-dom 7, Vite 8, TypeScript (strict: `verbatimModuleSyntax`, `noUnusedLocals`, `noUnusedParameters`), Tailwind 4, `@base-ui/react`, `@supabase/supabase-js` (new).

---

## Reference

Spec: `docs/superpowers/specs/2026-09-10-frontend-auth-integration-design.md` — read it before starting.

## Context the engineer needs

- Branch: `feature/supabase-multitenant-auth`. Stay on it. Never touch `main`.
- Work from `/Users/syonchau/ai_fraud_payroll/frontend`. Commands: `npm run build` (= `tsc -b && vite build`) is the per-task gate. `npx tsc -b` alone is a faster typecheck-only pass while iterating.
- **No test runner exists** — do not add one. Verification is `npm run build` passing + the manual run-through in the final task.
- TypeScript is strict:
  - `verbatimModuleSyntax: true` → type-only imports MUST use `import type { X }` (or `import { type X }`).
  - `noUnusedLocals` / `noUnusedParameters: true` → no dead identifiers.
- `@/` is an alias for `src/` (configured in `tsconfig.app.json` and `vite.config.ts`).
- ESLint uses `react-refresh/only-export-components` (vite preset, `allowConstantExport: true`). **A file that exports a component must not also export a hook or a non-constant.** That is why the React context object + its `useX` hook live in a separate `*.ts` file from the `*Provider.tsx` component.
- Existing dark theme (from `Layout.tsx`): background `#0a0a0a`, panels `#0f0f0f`/`#111111`, borders `#262626`/`#404040`, text `#e5e5e5`/`#737373`. Reuse `Button` from `@/components/ui/button` (variants: `default`, `outline`, `ghost`, `link`, `destructive`; sizes `sm`/`default`/`lg`).
- Existing `src/lib/api.ts`: a generic `request<T>(path, init?)` using `fetch` with `Content-Type: application/json`, plus a separate `uploadReceipt` that posts `FormData`. Errors throw `new Error("<status>: <body>")`. `BASE_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'`.
- Backend contracts:
  - `GET /api/v1/auth/me` → `{ user_id: string, email: string | null, memberships: { tenant_id: number, name: string, role: string }[] }`. Needs `Authorization` only.
  - `POST /api/v1/tenants` body `{ name: string }` → `{ id: number, name: string, created_at: string }`. Needs `Authorization` only.
  - All `/api/v1/receipts/*` need `Authorization` **and** `X-Tenant-ID`.
  - `401` = bad/missing/expired JWT. `400` = missing/bad `X-Tenant-ID`. `403` = not a member of that tenant.

## File Structure

### New files

| Path | Responsibility |
|---|---|
| `frontend/.env.example` | documents `VITE_*` vars |
| `frontend/src/lib/supabase.ts` | the singleton `supabase` client |
| `frontend/src/lib/activeTenant.ts` | `get/setActiveTenantId()` — `localStorage`-backed, no React |
| `frontend/src/auth/authContext.ts` | `AuthContext`, `AuthContextValue` type, `useAuth()` |
| `frontend/src/auth/AuthProvider.tsx` | session state + `signIn`/`signUp`/`signOut` |
| `frontend/src/auth/tenantContext.ts` | `TenantContext`, `TenantContextValue` type, `useTenant()` |
| `frontend/src/auth/TenantProvider.tsx` | loads memberships, tracks active tenant |
| `frontend/src/components/FullScreenSpinner.tsx` | centered spinner on the dark bg |
| `frontend/src/pages/Login.tsx` | email/password sign-in |
| `frontend/src/pages/Signup.tsx` | email/password sign-up |
| `frontend/src/pages/SelectBusiness.tsx` | pick or create a business |

### Modified files

| Path | Change |
|---|---|
| `frontend/package.json` | add `@supabase/supabase-js` |
| `frontend/.gitignore` | add `.env` |
| `frontend/src/lib/api.ts` | `authHeaders()`, 401 handling, `getMe` / `createTenant`, drop `employeeId` |
| `frontend/src/App.tsx` | restructured routing with `AuthProvider` / `TenantProvider` |
| `frontend/src/components/Layout.tsx` | business `<select>`, "＋ New business" link, user email + Sign out |

`src/pages/Upload.tsx` calls `api.uploadReceipt(file)` with one arg already, so dropping the optional 2nd param is source-compatible — no edit needed.

---

## Task 1: Dependencies, env, supabase client, active-tenant module

**Files:**
- Modify: `frontend/package.json`, `frontend/.gitignore`
- Create: `frontend/.env.example`, `frontend/src/lib/supabase.ts`, `frontend/src/lib/activeTenant.ts`

- [ ] **Step 1: Install the SDK**

Run: `cd frontend && npm install @supabase/supabase-js`
Expected: adds `@supabase/supabase-js` (2.x) to `dependencies` in `package.json` and `package-lock.json`, no errors.

- [ ] **Step 2: Ignore `.env`**

Append a line to `frontend/.gitignore`:

```
# Local env
.env
```

- [ ] **Step 3: Create `frontend/.env.example`**

```
# Backend API base URL
VITE_API_URL=http://localhost:8000

# Supabase project (Settings → API). Use the ANON/publishable key, not service_role.
VITE_SUPABASE_URL=https://your-project-ref.supabase.co
VITE_SUPABASE_ANON_KEY=your-anon-key
```

- [ ] **Step 4: Create `frontend/src/lib/supabase.ts`**

```ts
import { createClient } from '@supabase/supabase-js'

const url = import.meta.env.VITE_SUPABASE_URL
const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY

if (!url || !anonKey) {
  throw new Error(
    'Missing VITE_SUPABASE_URL or VITE_SUPABASE_ANON_KEY. Copy frontend/.env.example to frontend/.env and fill them in.',
  )
}

export const supabase = createClient(url, anonKey)
```

- [ ] **Step 5: Create `frontend/src/lib/activeTenant.ts`**

```ts
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
```

- [ ] **Step 6: Typecheck**

Run: `cd frontend && npx tsc -b`
Expected: no errors. (`supabase.ts` / `activeTenant.ts` are not imported anywhere yet, so this only proves they compile.)

- [ ] **Step 7: Commit**

```bash
cd frontend && git add package.json package-lock.json .gitignore .env.example src/lib/supabase.ts src/lib/activeTenant.ts
git commit -m "feat(frontend): add supabase client, env config, active-tenant store"
```
(If git complains about the repo root being the parent, prefix with `git -C ..` and `frontend/`-prefixed paths.)

---

## Task 2: `api.ts` — auth headers, 401 handling, new methods

**Files:**
- Modify: `frontend/src/lib/api.ts`

- [ ] **Step 1: Add imports and the `authHeaders` helper**

At the top of `frontend/src/lib/api.ts`, below the existing `const BASE_URL = …` line, add:

```ts
import { supabase } from '@/lib/supabase'
import { getActiveTenantId } from '@/lib/activeTenant'

async function authHeaders(): Promise<Record<string, string>> {
  const { data } = await supabase.auth.getSession()
  const token = data.session?.access_token
  if (!token) return {}
  const headers: Record<string, string> = { Authorization: `Bearer ${token}` }
  const tenantId = getActiveTenantId()
  if (tenantId != null) headers['X-Tenant-ID'] = String(tenantId)
  return headers
}

async function handleUnauthorized(res: Response): Promise<void> {
  if (res.status === 401) {
    await supabase.auth.signOut()
    window.location.assign('/login')
  }
}
```

- [ ] **Step 2: Thread auth headers + 401 into `request<T>()`**

Replace the existing `request` function with:

```ts
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(await authHeaders()),
      ...init?.headers,
    },
  })
  if (!res.ok) {
    await handleUnauthorized(res)
    const text = await res.text()
    throw new Error(`${res.status}: ${text}`)
  }
  return res.json()
}
```

- [ ] **Step 3: Update `uploadReceipt` (auth headers, no `Content-Type`, drop `employeeId`)**

Replace the `uploadReceipt` method with:

```ts
  uploadReceipt: async (file: File): Promise<Receipt> => {
    const form = new FormData()
    form.append('file', file)
    const res = await fetch(`${BASE_URL}/api/v1/receipts/upload`, {
      method: 'POST',
      body: form,
      headers: await authHeaders(),
    })
    if (!res.ok) {
      await handleUnauthorized(res)
      const text = await res.text()
      throw new Error(`${res.status}: ${text}`)
    }
    return res.json()
  },
```

- [ ] **Step 4: Add types + `getMe` / `createTenant`**

Near the other `export interface` blocks add:

```ts
export interface Membership {
  tenant_id: number
  name: string
  role: string
}

export interface MeResponse {
  user_id: string
  email: string | null
  memberships: Membership[]
}

export interface TenantResponse {
  id: number
  name: string
  created_at: string
}
```

Inside the `export const api = { … }` object add two methods:

```ts
  getMe: (): Promise<MeResponse> => request('/api/v1/auth/me'),

  createTenant: (name: string): Promise<TenantResponse> =>
    request('/api/v1/tenants', { method: 'POST', body: JSON.stringify({ name }) }),
```

- [ ] **Step 5: Typecheck**

Run: `cd frontend && npx tsc -b`
Expected: no errors. (`Upload.tsx` already calls `api.uploadReceipt(file)` with one argument, so the dropped param does not break it.)

- [ ] **Step 6: Commit**

```bash
cd frontend && git add src/lib/api.ts
git commit -m "feat(frontend): attach auth + X-Tenant-ID headers, add getMe/createTenant"
```

---

## Task 3: Auth context + provider + spinner

**Files:**
- Create: `frontend/src/auth/authContext.ts`, `frontend/src/auth/AuthProvider.tsx`, `frontend/src/components/FullScreenSpinner.tsx`

- [ ] **Step 1: Create `frontend/src/components/FullScreenSpinner.tsx`**

```tsx
export function FullScreenSpinner() {
  return (
    <div className="flex h-screen items-center justify-center bg-[#0a0a0a]">
      <div className="h-6 w-6 animate-spin rounded-full border-2 border-[#262626] border-t-[#e5e5e5]" />
    </div>
  )
}
```

- [ ] **Step 2: Create `frontend/src/auth/authContext.ts`**

```ts
import { createContext, useContext } from 'react'
import type { Session, User } from '@supabase/supabase-js'

export interface AuthContextValue {
  session: Session | null
  user: User | null
  loading: boolean
  signIn: (email: string, password: string) => Promise<{ error: string | null }>
  signUp: (email: string, password: string) => Promise<{ error: string | null; needsConfirmation: boolean }>
  signOut: () => Promise<void>
}

export const AuthContext = createContext<AuthContextValue | null>(null)

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within <AuthProvider>')
  return ctx
}
```

- [ ] **Step 3: Create `frontend/src/auth/AuthProvider.tsx`**

```tsx
import { useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import type { Session } from '@supabase/supabase-js'
import { supabase } from '@/lib/supabase'
import { setActiveTenantId } from '@/lib/activeTenant'
import { AuthContext } from '@/auth/authContext'
import type { AuthContextValue } from '@/auth/authContext'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let active = true
    supabase.auth.getSession().then(({ data }) => {
      if (!active) return
      setSession(data.session)
      setLoading(false)
    })
    const { data: sub } = supabase.auth.onAuthStateChange((_event, next) => {
      setSession(next)
    })
    return () => {
      active = false
      sub.subscription.unsubscribe()
    }
  }, [])

  const value = useMemo<AuthContextValue>(
    () => ({
      session,
      user: session?.user ?? null,
      loading,
      signIn: async (email, password) => {
        const { error } = await supabase.auth.signInWithPassword({ email, password })
        return { error: error?.message ?? null }
      },
      signUp: async (email, password) => {
        const { data, error } = await supabase.auth.signUp({ email, password })
        return {
          error: error?.message ?? null,
          needsConfirmation: !error && !data.session,
        }
      },
      signOut: async () => {
        await supabase.auth.signOut()
        setActiveTenantId(null)
      },
    }),
    [session, loading],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
```

- [ ] **Step 4: Typecheck**

Run: `cd frontend && npx tsc -b`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
cd frontend && git add src/auth/authContext.ts src/auth/AuthProvider.tsx src/components/FullScreenSpinner.tsx
git commit -m "feat(frontend): AuthProvider + useAuth + full-screen spinner"
```

---

## Task 4: Tenant context + provider

**Files:**
- Create: `frontend/src/auth/tenantContext.ts`, `frontend/src/auth/TenantProvider.tsx`

- [ ] **Step 1: Create `frontend/src/auth/tenantContext.ts`**

```ts
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
```

- [ ] **Step 2: Create `frontend/src/auth/TenantProvider.tsx`**

```tsx
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
```

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npx tsc -b`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
cd frontend && git add src/auth/tenantContext.ts src/auth/TenantProvider.tsx
git commit -m "feat(frontend): TenantProvider + useTenant (loads memberships, tracks active business)"
```

---

## Task 5: Login + Signup pages

**Files:**
- Create: `frontend/src/pages/Login.tsx`, `frontend/src/pages/Signup.tsx`

- [ ] **Step 1: Create `frontend/src/pages/Login.tsx`**

```tsx
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
```

- [ ] **Step 2: Create `frontend/src/pages/Signup.tsx`**

```tsx
import { useState } from 'react'
import type { FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '@/auth/authContext'
import { Button } from '@/components/ui/button'
import logo from '@/assets/logo.png'

const inputClass =
  'w-full rounded border border-[#262626] bg-[#111111] px-3 py-2 text-sm text-[#e5e5e5] outline-none focus:border-[#404040]'

export function Signup() {
  const { signUp } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    setNotice(null)
    const { error, needsConfirmation } = await signUp(email, password)
    setBusy(false)
    if (error) {
      setError(error)
      return
    }
    if (needsConfirmation) {
      setNotice('Account created — check your email to confirm, then log in.')
    }
    // otherwise: session is live, onAuthStateChange re-renders into the authed tree
  }

  return (
    <div className="flex h-screen items-center justify-center bg-[#0a0a0a] text-[#e5e5e5]">
      <div className="w-80 rounded-lg border border-[#262626] bg-[#0f0f0f] p-6">
        <img src={logo} alt="Receiptly" className="mx-auto mb-4 h-16 w-auto mix-blend-screen" />
        <h1 className="mb-4 text-center text-sm text-[#737373]">Create an account</h1>
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
            placeholder="Password (min 6 characters)"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            minLength={6}
            required
          />
          {error && <p className="text-xs text-red-400">{error}</p>}
          {notice && <p className="text-xs text-[#7dd3a8]">{notice}</p>}
          <Button type="submit" size="lg" disabled={busy}>
            {busy ? 'Creating…' : 'Sign up'}
          </Button>
        </form>
        <p className="mt-4 text-center text-xs text-[#737373]">
          Have an account?{' '}
          <Link to="/login" className="text-[#e5e5e5] underline">
            Log in
          </Link>
        </p>
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npx tsc -b`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
cd frontend && git add src/pages/Login.tsx src/pages/Signup.tsx
git commit -m "feat(frontend): Login and Signup pages"
```

---

## Task 6: SelectBusiness page

**Files:**
- Create: `frontend/src/pages/SelectBusiness.tsx`

- [ ] **Step 1: Create `frontend/src/pages/SelectBusiness.tsx`**

```tsx
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
```

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc -b`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
cd frontend && git add src/pages/SelectBusiness.tsx
git commit -m "feat(frontend): SelectBusiness page (pick or create a business)"
```

---

## Task 7: `App.tsx` routing restructure

**Files:**
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Replace the whole file**

Replace `frontend/src/App.tsx` with:

```tsx
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
```

- [ ] **Step 2: Typecheck + full build**

Run: `cd frontend && npm run build`
Expected: `tsc -b` clean, `vite build` succeeds. (`Layout.tsx` still compiles — it is edited in Task 8; the app is temporarily missing the sidebar switcher but builds.)

- [ ] **Step 3: Commit**

```bash
cd frontend && git add src/App.tsx
git commit -m "feat(frontend): route on auth session + active business"
```

---

## Task 8: `Layout.tsx` — business switcher, user email, sign out

**Files:**
- Modify: `frontend/src/components/Layout.tsx`

- [ ] **Step 1: Replace the whole file**

Replace `frontend/src/components/Layout.tsx` with:

```tsx
import { NavLink, Link, Outlet } from 'react-router-dom'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { useAuth } from '@/auth/authContext'
import { useTenant } from '@/auth/tenantContext'
import logo from '@/assets/logo.png'

const links = [
  { to: '/', label: 'Dashboard', exact: true },
  { to: '/receipts', label: 'Receipts', exact: false },
  { to: '/upload', label: 'Upload', exact: false },
]

export function Layout() {
  const { user, signOut } = useAuth()
  const { memberships, activeTenantId, setActiveTenant } = useTenant()

  return (
    <div className="flex h-screen bg-[#0a0a0a] text-[#e5e5e5]">
      {/* Sidebar */}
      <aside className="w-52 shrink-0 border-r border-[#262626] flex flex-col">
        <div className="px-4 py-4 border-b border-[#262626] flex items-center">
          <NavLink to="/">
            <img src={logo} alt="Receiptly" className="h-20 w-auto mix-blend-screen" />
          </NavLink>
        </div>

        <div className="px-2 pt-2 flex flex-col gap-1">
          <select
            value={activeTenantId ?? ''}
            onChange={(e) => {
              setActiveTenant(Number(e.target.value))
              window.location.reload()
            }}
            className="w-full rounded border border-[#262626] bg-[#111111] px-2 py-1.5 text-xs text-[#e5e5e5] outline-none focus:border-[#404040]"
          >
            {memberships.map((m) => (
              <option key={m.tenant_id} value={m.tenant_id}>
                {m.name}
              </option>
            ))}
          </select>
          <Link
            to="/select-business"
            className="px-1 text-xs text-[#737373] hover:text-[#e5e5e5]"
          >
            ＋ New business
          </Link>
        </div>

        <nav className="flex flex-col gap-0.5 p-2 flex-1">
          {links.map(({ to, label, exact }) => (
            <NavLink
              key={to}
              to={to}
              end={exact}
              className={({ isActive }) =>
                `px-3 py-2.5 rounded text-base transition-colors ${
                  isActive
                    ? 'bg-[#1a1a1a] text-white'
                    : 'text-[#737373] hover:text-[#e5e5e5] hover:bg-[#111111]'
                }`
              }
            >
              {label}
            </NavLink>
          ))}
        </nav>

        <div className="px-4 py-3 border-t border-[#262626] flex flex-col gap-1">
          <p className="truncate text-xs text-[#737373]" title={user?.email ?? ''}>
            {user?.email}
          </p>
          <button
            onClick={signOut}
            className="self-start text-xs text-[#737373] underline hover:text-[#e5e5e5]"
          >
            Sign out
          </button>
          <p className="text-xs text-[#404040]">CPSC 490 · v1.0</p>
        </div>
      </aside>

      {/* Main */}
      <main className="flex-1 overflow-auto">
        <ErrorBoundary>
          <Outlet />
        </ErrorBoundary>
      </main>
    </div>
  )
}
```

- [ ] **Step 2: Full build**

Run: `cd frontend && npm run build`
Expected: `tsc -b` clean, `vite build` succeeds.

- [ ] **Step 3: Lint**

Run: `cd frontend && npm run lint`
Expected: no errors. If `react-refresh/only-export-components` fires on any file, move the offending non-component export into a `*.ts` sibling (should already be the case — contexts/hooks are in `authContext.ts` / `tenantContext.ts`).

- [ ] **Step 4: Commit**

```bash
cd frontend && git add src/components/Layout.tsx
git commit -m "feat(frontend): sidebar business switcher, user email, sign out"
```

---

## Task 9: Manual verification against the live backend

**Files:** none (may add small fixes + a docs note)

Prerequisites: the backend running (`cd backend && uvicorn app.main:app --reload`) pointed at Supabase (its `.env` is already set), and `frontend/.env` populated.

- [ ] **Step 1: Create `frontend/.env`**

```bash
cd frontend && cp .env.example .env
```
Then edit `frontend/.env`:
- `VITE_API_URL=http://localhost:8000`
- `VITE_SUPABASE_URL=https://kxbavxzlrxxzvnhnxbku.supabase.co`
- `VITE_SUPABASE_ANON_KEY=` → from Supabase dashboard → Settings → API → **anon / public** key

- [ ] **Step 2: Confirm email confirmation is OFF for the demo**

In the Supabase dashboard → Authentication → Providers → Email → ensure **"Confirm email"** is disabled (so signup returns a live session). If it must stay on, the Signup page's "check your email" branch handles it, but the rest of this verification needs a confirmed user.

- [ ] **Step 3: Start the dev server**

Run: `cd frontend && npm run dev`
Open the printed URL (default `http://localhost:5173`).

- [ ] **Step 4: Logged-out redirect**

Visit `/` → expect redirect to `/login`.

- [ ] **Step 5: Sign up → first business**

Go to `/signup`, register a fresh email + password (≥6 chars). Expect to land on **"Create your first business"**. Create one (e.g. "Test Co"). Expect the app to load on the Dashboard with the sidebar showing the business name.

- [ ] **Step 6: Auth headers on every verb**

- Upload a receipt (`/upload`, drop a PNG/JPG) → redirects to the detail page, OCR fields + fraud score populate. (POST multipart with `Authorization` + `X-Tenant-ID`.)
- `/receipts` lists it. (GET.)
- Open it, click Approve → status flips to `approved`. (PATCH.)

If any of these returns `400 Missing X-Tenant-ID` or `401`, the header wiring in `api.ts` is wrong — fix `authHeaders()` / its call sites and re-verify.

- [ ] **Step 7: Second business + switching**

Click **"＋ New business"** in the sidebar → `/select-business` → create "Second Co" → app reloads into it. Use the sidebar `<select>` to switch back to "Test Co" → `/receipts` shows the first receipt; switch to "Second Co" → the list is empty. (Confirms `X-Tenant-ID` scoping end to end.)

- [ ] **Step 8: Persistence + sign out**

Sign out (sidebar) → back to `/login`. Log in with the same account → lands straight in the last-active business (localStorage), data loads.

- [ ] **Step 9: Missing active tenant**

In devtools console: `localStorage.removeItem('activeTenantId')`, reload → the "Select a business" screen appears listing both businesses.

- [ ] **Step 10: Fix anything that failed, then update docs**

Apply minimal fixes for any failing step (re-run `npm run build` after). Then add to the repo-root `README.md`, in the `### Frontend` setup block, before `npm run dev`:

```
# Configure environment
cp .env.example .env
# Edit .env: VITE_SUPABASE_URL, VITE_SUPABASE_ANON_KEY (anon key), VITE_API_URL
```

And to `TODO.md`, under `### Supabase Auth & Multi-Tenancy`:

```markdown
- [x] Frontend: Supabase signup/login, active-business selection + sidebar switcher, auth headers on all API calls
```

- [ ] **Step 11: Commit**

```bash
cd /Users/syonchau/ai_fraud_payroll && git add -A
git commit -m "docs: frontend auth setup notes; fixes from manual verification"
```
(If no fixes were needed and only docs changed, keep the message as just the docs note.)

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| Add `@supabase/supabase-js`, `.env` vars, `.env.example` | Task 1 |
| `src/lib/supabase.ts` singleton | Task 1 |
| `src/lib/activeTenant.ts` get/set | Task 1 |
| `api.ts` `authHeaders()` (Bearer + X-Tenant-ID) | Task 2 |
| `api.ts` 401 → signOut + redirect | Task 2 |
| `api.ts` `getMe` / `createTenant` + types | Task 2 |
| `api.ts` drop `employeeId` from `uploadReceipt` | Task 2 |
| `AuthProvider` + `useAuth` (session, signIn/signUp/signOut) | Task 3 |
| `FullScreenSpinner` | Task 3 |
| `TenantProvider` + `useTenant` (memberships, active id, localStorage) | Task 4 |
| Login / Signup pages (dark theme, inline error, toggle link, needsConfirmation copy) | Task 5 |
| SelectBusiness (cards + create form, empty-state copy, sign out) | Task 6 |
| `App.tsx` restructured routing incl. `/select-business` always mounted | Task 7 |
| `Layout.tsx` sidebar `<select>` + "＋ New business" link + email + Sign out | Task 8 |
| Manual verification (build passes + the 7 spec steps) | Task 9 |
| README / TODO rollout notes | Task 9 |
| Out of scope: test runner, members UI, role gating, email-confirm flow | not implemented (Signup keeps only the minimal `needsConfirmation` notice) |

No gaps.

**Placeholder scan:** No "TBD" / "similar to Task N" / vague steps. Every code step gives the full file or the exact replacement block; every gate is a concrete command with an expected outcome.

**Type / name consistency:**
- `AuthContext` / `AuthContextValue` / `useAuth` — defined `src/auth/authContext.ts` (Task 3), imported by `AuthProvider.tsx` (Task 3), `App.tsx` (Task 7), `Login.tsx` / `Signup.tsx` (Task 5), `Layout.tsx` (Task 8), `SelectBusiness.tsx` (Task 6).
- `TenantContext` / `TenantContextValue` / `useTenant` — defined `src/auth/tenantContext.ts` (Task 4), imported by `TenantProvider.tsx` (Task 4), `App.tsx` (Task 7), `Layout.tsx` (Task 8), `SelectBusiness.tsx` (Task 6).
- `signUp` returns `{ error, needsConfirmation }` — shape defined in `AuthContextValue` (Task 3) and consumed in `Signup.tsx` (Task 5).
- `Membership` / `MeResponse` / `TenantResponse` — defined in `api.ts` (Task 2), imported by `tenantContext.ts` / `TenantProvider.tsx` (Task 4).
- `getActiveTenantId` / `setActiveTenantId` — defined `src/lib/activeTenant.ts` (Task 1), used in `api.ts` (Task 2), `AuthProvider.tsx` (Task 3, via `setActiveTenantId(null)`), `TenantProvider.tsx` (Task 4).
- `api.getMe` / `api.createTenant` — defined Task 2, called in `TenantProvider.tsx` (Task 4) and `SelectBusiness.tsx` (Task 6).
- `FullScreenSpinner` — defined Task 3, used in `App.tsx` (Task 7).
- Route path `/select-business` — mounted in `App.tsx` (Task 7), linked from `Layout.tsx` (Task 8), matches spec §6/§9.
