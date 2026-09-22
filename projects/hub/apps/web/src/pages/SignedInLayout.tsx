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

/* The record of 2026-09-18, as #228 drew it in the CRM's AppShell: the active item is
   marked by a Watermelon Strong tile at the row's left edge (the `before:` square, painted
   only when active so the label never shifts), over the lighter translucent fill the dark
   sidebar already had. TanStack's default `activeClass` is `active`, which is what the
   `[&.active]` selectors read. */
const NAV_LINK =
  'relative flex items-center gap-2 px-2 py-1.5 text-sm transition-colors before:absolute before:left-0 before:top-1/2 before:size-1.5 before:-translate-y-1/2 before:bg-transparent before:content-[""] text-sidebar-foreground/70 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground [&.active]:bg-sidebar-accent [&.active]:font-medium [&.active]:text-sidebar-accent-foreground [&.active]:before:bg-sidebar-primary'

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
    <div className="flex h-full overflow-hidden">
      {/* The sidebar reads the sidebar slots, not the raw palette: `--sidebar` is the
         same Prussian Blue and `--sidebar-foreground` the same Paper, and the record's
         Sidebar paragraph is what points them. The CRM's shell (#228) draws the same
         pair from the same tokens. */}
      <aside className="flex h-full w-56 shrink-0 flex-col gap-6 overflow-y-auto bg-sidebar p-4 text-sidebar-foreground">
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
              <p className="mt-2 px-2 text-xs font-medium tracking-wide text-sidebar-foreground/70 uppercase">
                Amministrazione
              </p>
              <div role="separator" className="my-2 border-t border-sidebar-border" />
              {ADMIN_NAV.map(({ to, label, icon: Icon }) => (
                <Link key={to} to={to} className={NAV_LINK}>
                  <Icon className="size-4" aria-hidden="true" />
                  {label}
                </Link>
              ))}
            </>
          )}
        </nav>
        <div className="mt-auto space-y-2 px-2 text-xs text-sidebar-foreground/70">
          <p className="truncate">{me.data.email}</p>
          <Button
            variant="ghost"
            size="sm"
            className="w-full justify-start text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground"
            onClick={() => logout.mutate()}
          >
            <LogOut className="mr-2 size-4" />
            Esci
          </Button>
        </div>
      </aside>
      {/* The content panel of the record, as #228 rebuilt the CRM's: the shell stops
         painting `bg-background` over the body's own 16px grid, and the white panel sits
         inset inside a 1px ink line from `lg` up. Below `lg` the inset and the line drop
         to nothing and the panel simply is the page. */}
      <div className="flex min-w-0 flex-1 flex-col p-0 lg:p-3">
        {/* No padding on `main`, as in the CRM's shell: each page insets itself, the
           admin pages with their own `px-6` rows and the member pages with a `p-6` on
           their root. The panel's border is then the page's edge, not a frame around a
           frame. */}
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden border-0 border-border bg-card lg:border">
          <main className="min-h-0 flex-1 overflow-y-auto">
            <Outlet />
          </main>
        </div>
      </div>
    </div>
  )
}
