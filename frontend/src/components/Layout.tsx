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
