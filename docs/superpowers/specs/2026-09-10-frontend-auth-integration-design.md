# Frontend Auth Integration — Design

**Date:** 2026-09-10
**Branch:** `feature/supabase-multitenant-auth` (continues on the same branch)
**Status:** Approved for implementation planning

## Problem

The backend now requires a Supabase JWT (`Authorization: Bearer`) on every request
and an `X-Tenant-ID` header on every `/api/v1/receipts/*` route. The React app
(`frontend/`) sends neither and has no login flow, so it cannot talk to the
backend at all. This adds authentication (signup + login) and active-business
selection to the frontend.

## Decisions (from brainstorming)

- **In-app signup + login.** A signup form (`supabase.auth.signUp`) and a login
  form (`supabase.auth.signInWithPassword`). Email confirmation is assumed OFF in
  the Supabase project (Auth → Providers → Email → "Confirm email" disabled), so
  a new account is usable immediately.
- **Active business:** after login, if none is chosen the user lands on a
  "Select a business" screen (which doubles as "create your first business" when
  the user belongs to none). Once chosen, a `<select>` in the sidebar switches
  it. The choice is persisted in `localStorage`.
- **Tokens:** `supabase-js` auto-refreshes the session. `api.ts` reads the
  current access token per request via `supabase.auth.getSession()`. A `401` from
  the backend signs the user out and redirects to `/login`.
- **Design:** match the existing dark fintech aesthetic (`Layout.tsx`,
  `index.css`). No separate design pass.
- **Anon key** (not service-role) in the frontend — it only performs auth.

## Non-goals

Test framework, members-management UI (`add_member`), role-based UI gating, email
confirmation flow, URL-based tenant routing.

## Components

### 1. Dependencies & config

- Add `@supabase/supabase-js` (latest 2.x) to `frontend/package.json` dependencies.
- `frontend/.env` (gitignored — add `.env` to `frontend/.gitignore` if not there)
  and `frontend/.env.example`:
  ```
  VITE_API_URL=http://localhost:8000
  VITE_SUPABASE_URL=https://kxbavxzlrxxzvnhnxbku.supabase.co
  VITE_SUPABASE_ANON_KEY=<anon key from Supabase dashboard → Settings → API>
  ```
- `frontend/src/lib/supabase.ts`:
  ```ts
  import { createClient } from '@supabase/supabase-js'
  export const supabase = createClient(
    import.meta.env.VITE_SUPABASE_URL,
    import.meta.env.VITE_SUPABASE_ANON_KEY,
  )
  ```

### 2. Active-tenant module (framework-agnostic)

`frontend/src/lib/activeTenant.ts` — so `api.ts` needs no React context:
```ts
const KEY = 'activeTenantId'
export function getActiveTenantId(): number | null {
  const v = localStorage.getItem(KEY)
  return v ? Number(v) : null
}
export function setActiveTenantId(id: number | null): void {
  if (id == null) localStorage.removeItem(KEY)
  else localStorage.setItem(KEY, String(id))
}
```

### 3. `src/lib/api.ts` changes

- New `async function authHeaders(): Promise<Record<string,string>>`:
  - `const { data } = await supabase.auth.getSession()`
  - returns `{}` if no session; otherwise
    `{ Authorization: 'Bearer ' + data.session.access_token }` and, when
    `getActiveTenantId()` is non-null, `'X-Tenant-ID': String(getActiveTenantId())`.
- `request<T>()`: `headers: { 'Content-Type': 'application/json', ...(await authHeaders()), ...init?.headers }`.
- `uploadReceipt`: spread `await authHeaders()` into the fetch `headers` (no
  `Content-Type` — FormData sets it). Remove the `employeeId` parameter and the
  `form.append('employee_id', …)` line (backend dropped it).
- Shared 401 handling: in `request()` and in `uploadReceipt`, when
  `res.status === 401`, `await supabase.auth.signOut()` then
  `window.location.assign('/login')` before throwing. `403` is thrown as-is.
- New types + methods:
  ```ts
  export interface Membership { tenant_id: number; name: string; role: string }
  export interface MeResponse { user_id: string; email: string | null; memberships: Membership[] }
  export interface TenantResponse { id: number; name: string; created_at: string }

  getMe: (): Promise<MeResponse> => request('/api/v1/auth/me'),
  createTenant: (name: string): Promise<TenantResponse> =>
    request('/api/v1/tenants', { method: 'POST', body: JSON.stringify({ name }) }),
  ```

### 4. `src/auth/AuthProvider.tsx` + `useAuth`

- Context value:
  `{ session: Session | null; user: User | null; loading: boolean;
     signIn(email,password): Promise<{error: string | null}>;
     signUp(email,password): Promise<{error: string | null}>;
     signOut(): Promise<void> }`
- On mount: `supabase.auth.getSession()` sets initial `session` and clears
  `loading`; then `supabase.auth.onAuthStateChange((_e, s) => setSession(s))`;
  unsubscribe on cleanup.
- `signIn` / `signUp` call the matching `supabase.auth` method and return
  `{ error: error?.message ?? null }` (the component shows it; the provider does
  not throw).
- `signOut`: `await supabase.auth.signOut()` and `setActiveTenantId(null)`.
- `src/auth/useAuth.ts`: `const ctx = useContext(AuthContext); if (!ctx) throw …; return ctx`.

### 5. `src/auth/TenantProvider.tsx` + `useTenant`

Mounted only inside the authenticated branch.

- Context value:
  `{ memberships: Membership[]; loading: boolean; activeTenantId: number | null;
     setActiveTenant(id: number): void; refreshMemberships(): Promise<void> }`
- On mount and on `refreshMemberships()`: `api.getMe()` → `setMemberships(res.memberships)`.
- After memberships load: if `getActiveTenantId()` is set **and** still present in
  `memberships`, keep it; otherwise leave `activeTenantId` `null` (do NOT
  auto-pick — the Select screen handles that).
- `setActiveTenant(id)`: `setActiveTenantId(id)` (localStorage) + state update.

### 6. `src/App.tsx` — restructured

```tsx
<AuthProvider>
  <BrowserRouter>
    <AppRoutes />
  </BrowserRouter>
</AuthProvider>
```

`AppRoutes` (new small component using `useAuth`):
- `loading` → `<FullScreenSpinner/>`
- no `session` → `<Routes>`: `/login` → `<Login/>`, `/signup` → `<Signup/>`,
  `*` → `<Navigate to="/login" replace/>`
- `session` → `<TenantProvider><AuthedRoutes/></TenantProvider>`

`AuthedRoutes` (new, uses `useTenant`):
- `loading` → `<FullScreenSpinner/>`
- `activeTenantId == null` → `<Routes>`: `*` → `<SelectBusiness/>`
- else → the routes below. Note `/select-business` is mounted here too so a user
  with an active business can still reach the screen to create or switch to
  another one:
  ```tsx
  <Routes>
    <Route path="/select-business" element={<SelectBusiness/>} />
    <Route element={<Layout/>}>
      <Route path="/" element={<Dashboard/>} />
      <Route path="/receipts" element={<ReceiptsList/>} />
      <Route path="/receipts/:id" element={<ReceiptDetail/>} />
      <Route path="/upload" element={<Upload/>} />
    </Route>
    <Route path="*" element={<Navigate to="/" replace/>} />
  </Routes>
  ```

`src/components/FullScreenSpinner.tsx` — centered spinner on `bg-[#0a0a0a]`.

### 7. `src/pages/Login.tsx` / `src/pages/Signup.tsx`

- Centered card on the dark background; app logo; email + password inputs;
  submit button with a loading state; one inline error line; a link toggling to
  the other page (`react-router` `<Link>`).
- Submit: `const { error } = await signIn(email, password)` (or `signUp`); on
  `error` show it; on success do nothing — `onAuthStateChange` re-renders
  `AppRoutes` into the authed branch.
- Signup success with confirmation OFF returns a session immediately. If it
  returns no session (confirmation unexpectedly ON), show:
  "Account created — check your email to confirm, then log in."

### 8. `src/pages/SelectBusiness.tsx`

- Uses `useTenant()`.
- If `memberships.length > 0`: a heading "Select a business" and a list of
  clickable cards (name + role); clicking → `setActiveTenant(m.tenant_id)`
  (router re-renders into the app).
- Always (and as the whole screen when `memberships` is empty, with copy
  "Create your first business"): a form with a name input + "Create" button →
  `await api.createTenant(name)` → `await refreshMemberships()` →
  `setActiveTenant(created.id)`. Inline error on failure.
- A "Sign out" link in a corner.

### 9. `src/components/Layout.tsx` changes

- Under the logo block: a styled `<select>` — value `activeTenantId`, options
  from `useTenant().memberships` (`value={m.tenant_id}`, label `m.name`).
  `onChange`: `setActiveTenant(Number(e.target.value))` then
  `window.location.reload()` (pages refetch on mount; simplest correct refresh).
- Directly under the `<select>`: a small `<Link to="/select-business">＋ New business</Link>`
  so a user with an active business can create or switch to another one.
- Footer: replace the static `CPSC 490 · v1.0` line with the user's email
  (`useAuth().user?.email`) and a "Sign out" button (`signOut()`), keeping the
  version string above or below.

## Testing / verification

No automated tests (frontend has no runner; out of scope). Verify manually with
`npm run dev` against the running backend + real Supabase:

1. `npm run build` passes (TypeScript strict, no unused-var / no-explicit-any
   violations under the existing eslint config).
2. Visit `/` while logged out → redirected to `/login`.
3. Sign up with a fresh email → lands on "Create your first business" → create
   one → app loads on the Dashboard.
4. Upload a receipt → appears in Receipts list; open it → detail loads; approve
   it → status updates. (Confirms `Authorization` + `X-Tenant-ID` are attached on
   GET, POST multipart, and PATCH.)
5. Click "＋ New business" in the sidebar → `/select-business` → create a second
   business → switch between the two in the sidebar `<select>` → Receipts list
   shows only the active business's receipts.
6. Sign out → back to `/login`. Log in again → same business still selected
   (localStorage), data loads.
7. Manually delete the `activeTenantId` localStorage key and reload → "Select a
   business" screen appears.

## Rollout notes

- Requires `frontend/.env` populated with `VITE_SUPABASE_ANON_KEY` before
  `npm run dev`.
- Supabase project: Auth → Providers → Email → "Confirm email" should be OFF for
  the demo signup flow. If it must stay ON, the Signup page's
  "check your email" branch covers it but the happy path needs a confirmed user.
- No backend changes. No new backend endpoints (`/auth/me` and
  `POST /tenants` already exist).
