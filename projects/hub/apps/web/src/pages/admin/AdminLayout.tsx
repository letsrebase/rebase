import { Link, Outlet, useNavigate } from '@tanstack/react-router'
import { BookOpen, Boxes, Briefcase, LogIn, LogOut, Mail, Plug, ShieldCheck, UserRound } from 'lucide-react'
import { useEffect } from 'react'
import { BrandMark } from '@/components/BrandMark'
import { Button } from '@rebase/ui/button'
import { useIdentifyAdmin } from '@/lib/analytics'
import { useAdmin, useLogout } from '@/lib/auth'

const NAV = [
  { to: '/admin/freelance', label: 'Developer e CTO', icon: UserRound },
  { to: '/admin/aziende', label: 'Aziende', icon: Briefcase },
  { to: '/admin/iscrizioni', label: 'Iscrizioni', icon: Mail },
  { to: '/admin/pigro', label: 'Istanze Pigro', icon: Boxes },
  { to: '/admin/guida', label: 'La guida', icon: BookOpen },
  { to: '/admin/accessi', label: 'Accessi', icon: LogIn },
  { to: '/admin/amministratori', label: 'Amministratori', icon: ShieldCheck },
  { to: '/admin/agenti', label: 'Agenti', icon: Plug },
] as const

/** The admin area's frame: the CRM's dark sidebar, in miniature. Nothing renders until
 *  the session is known, and without one the visitor goes to the login. */
export function AdminLayout() {
  const me = useAdmin()
  const logout = useLogout()
  const navigate = useNavigate()
  useIdentifyAdmin(me.data)

  useEffect(() => {
    if (!me.isPending && me.data === null) void navigate({ to: '/admin/login' })
  }, [me.isPending, me.data, navigate])

  if (me.isPending) return <p className="p-8 text-sm text-muted-foreground">Caricamento…</p>
  if (!me.data) return null

  return (
    <div className="flex min-h-full">
      <aside className="flex w-56 shrink-0 flex-col gap-6 bg-[var(--color-prussian-blue)] p-4 text-[var(--color-paper)]">
        <Link to="/admin/freelance" className="inline-flex items-center gap-2.5 px-2 font-semibold">
          <BrandMark className="size-3.5 [&>span:nth-child(1)]:bg-[var(--color-paper)] [&>span:nth-child(4)]:bg-[var(--color-paper)]" />
          rebase
        </Link>
        <nav className="flex flex-col gap-1">
          {NAV.map(({ to, label, icon: Icon }) => (
            <Link
              key={to}
              to={to}
              className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm text-[var(--color-paper)]/80 hover:bg-white/10 [&.active]:bg-white/10 [&.active]:text-[var(--color-paper)]"
            >
              <Icon className="size-4" aria-hidden="true" />
              {label}
            </Link>
          ))}
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
      <main className="flex-1 p-6">
        <div className="min-h-full rounded-2xl border bg-card">
          <Outlet />
        </div>
      </main>
    </div>
  )
}
