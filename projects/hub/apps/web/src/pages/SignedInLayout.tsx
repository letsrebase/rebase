import { Link, Outlet, useNavigate } from '@tanstack/react-router'
import { BookOpen, Boxes, Briefcase, Home, LogIn, LogOut, Plug, ShieldCheck, UserRound } from 'lucide-react'
import { useEffect } from 'react'
import { BrandMark } from '@/components/BrandMark'
import { Button } from '@rebase/ui/button'
import { useIdentify } from '@/lib/analytics'
import { useLogout, useMe } from '@/lib/me'

/** The admin pages (ORB-123 onward): «Developer e CTO» and «Iscrizioni», two
 *  overlapping views of the same people, are one entry, «Talenti», over the read
 *  model that merges them (REB-283); the rest keep their label, icon and path. */
const ADMIN_NAV = [
  { to: '/admin/talent', label: 'Talenti', icon: UserRound },
  { to: '/admin/companies', label: 'Aziende', icon: Briefcase },
  { to: '/admin/pigro', label: 'Istanze Pigro', icon: Boxes },
  { to: '/admin/guide', label: 'La guida', icon: BookOpen },
  { to: '/admin/access', label: 'Accessi', icon: LogIn },
  { to: '/admin/admins', label: 'Amministratori', icon: ShieldCheck },
  { to: '/admin/agents', label: 'Agenti', icon: Plug },
] as const

const NAV_LINK =
  'flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm text-[var(--color-paper)]/80 hover:bg-white/10 [&.active]:bg-white/10 [&.active]:text-[var(--color-paper)]'

/**
 * The one guard and the one frame every signed-in route passes through (REB-279):
 * `AdminLayout.tsx`'s dark sidebar and `pages/member/Guard.tsx`'s `MemberGuard`
 * collapse into this one component, reading `useMe()`. Nothing renders while the
 * session is unknown, a signed-out visitor goes to `/accedi`, and the admin group of
 * the sidebar shows only for `role === 'admin'`. `/admin/*`'s own access rule lives
 * one level further in, `AdminGuard`: a signed-in non-admin still belongs in this same
 * frame, just not in that section of it, which is what closes the old empty-frame bug
 * (REB-106) as a side effect of there being one guard here instead of two.
 */
export function SignedInLayout() {
  const me = useMe()
  const logout = useLogout()
  const navigate = useNavigate()
  useIdentify(me.data, me.data?.role)

  useEffect(() => {
    if (!me.isPending && me.data === null) void navigate({ to: '/login', replace: true })
  }, [me.isPending, me.data, navigate])

  if (me.isPending) return <p className="p-8 text-sm text-muted-foreground">Caricamento…</p>
  if (!me.data) return null
  const isAdmin = me.data.role === 'admin'

  return (
    <div className="flex h-full">
      <aside className="flex h-full w-56 shrink-0 flex-col gap-6 overflow-y-auto bg-[var(--color-prussian-blue)] p-4 text-[var(--color-paper)]">
        <Link to="/me" className="inline-flex items-center gap-2.5 px-2 font-semibold">
          <BrandMark className="size-3.5 [&>span:nth-child(1)]:bg-[var(--color-paper)] [&>span:nth-child(4)]:bg-[var(--color-paper)]" />
          rebase
        </Link>
        <nav className="flex flex-col gap-1">
          <Link to="/me" className={NAV_LINK}>
            <Home className="size-4" aria-hidden="true" />
            La tua area
          </Link>
          {isAdmin && (
            <>
              <p className="mt-2 px-2 text-xs font-medium tracking-wide text-[var(--color-paper)]/70 uppercase">
                Amministrazione
              </p>
              <div role="separator" className="my-2 border-t border-white/10" />
              {ADMIN_NAV.map(({ to, label, icon: Icon }) => (
                <Link key={to} to={to} className={NAV_LINK}>
                  <Icon className="size-4" aria-hidden="true" />
                  {label}
                </Link>
              ))}
            </>
          )}
        </nav>
        <div className="mt-auto space-y-2 px-2 text-xs text-[var(--color-paper)]/70">
          <p className="truncate">{me.data.email}</p>
          <Button
            variant="ghost"
            size="sm"
            className="w-full justify-start text-[var(--color-paper)] hover:bg-white/10 hover:text-[var(--color-paper)]"
            onClick={() => logout.mutate()}
          >
            <LogOut className="mr-2 size-4" />
            Esci
          </Button>
        </div>
      </aside>
      <main className="flex-1 overflow-y-auto bg-card p-6">
        <Outlet />
      </main>
    </div>
  )
}
