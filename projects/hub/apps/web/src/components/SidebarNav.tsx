import { Link } from '@tanstack/react-router'
import {
  BookOpen,
  Boxes,
  Briefcase,
  Handshake,
  Home,
  LogIn,
  LogOut,
  Plug,
  Send,
  ShieldCheck,
  UserRound,
  Users,
} from 'lucide-react'
import { BrandMark } from '@/components/BrandMark'
import { Button } from '@rebase/ui/button'
import { cn } from '@rebase/ui/cn'
import type { Me } from '@/lib/api'
import { useLogout } from '@/lib/me'

/** The admin pages (ORB-123 onward): «Developer e CTO» and «Iscrizioni», two
 *  overlapping views of the same people, are one entry, «Talenti», over the read
 *  model that merges them (REB-283); the rest keep their label, icon and path. «Match»
 *  (REB-413) lists every match the milestone's other four admin pages create, between
 *  the two lists it draws its rows from. «Campagne» (P-REB-41) lists the mails an
 *  admin sends the community, one state and one outcome at a time. «Richieste team»
 *  (REB-514) sits right after «Match», before «Aziende» as the spec places it: the
 *  requests the team builder files, the step before a match. */
const ADMIN_NAV = [
  { to: '/admin/talent', label: 'Talenti', icon: UserRound },
  { to: '/admin/matches', label: 'Match', icon: Handshake },
  { to: '/admin/team', label: 'Richieste team', icon: Users },
  { to: '/admin/campaigns', label: 'Campagne', icon: Send },
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
  'relative flex items-center gap-2 px-2 py-1.5 text-sm transition-colors before:absolute before:left-0 before:top-1/2 before:size-1.5 before:-translate-y-1/2 before:bg-transparent before:content-[""] text-sidebar-foreground/70 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground [&.active]:bg-sidebar-accent [&.active]:font-medium [&.active]:text-sidebar-foreground [&.active]:before:bg-sidebar-primary'

/**
 * The whole sidebar, in one render: brand, the member's own entry, the admin group
 * gated on role, and the footer with the address and «Esci». `SignedInLayout` mounts
 * it twice a visit at most and never both at once -- directly in the `w-56` aside on
 * desktop, and inside the `SheetContent` drawer below `lg` (REB-316) -- so the nav
 * list, the gating and the active-item treatment are said once.
 *
 * `drawer` is what the mobile shape knows that the desktop one does not: every
 * control gets its 44px target (the card's bar, not Tailwind's comfortable 32/36px
 * sizes), and `onNavigate` collapses the drawer after a link's own navigation, which
 * is what makes a tap on «Talenti» land on Talenti with the drawer gone.
 */
export function SidebarNav({
  me,
  isAdmin,
  drawer = false,
  onNavigate,
}: {
  me: Me
  isAdmin: boolean
  drawer?: boolean
  onNavigate?: () => void
}) {
  const logout = useLogout()

  return (
    <>
      <Link
        to="/me"
        onClick={onNavigate}
        className={cn(
          'inline-flex items-center gap-2.5 px-2 font-semibold',
          drawer && 'min-h-11',
        )}
      >
        <BrandMark className="size-3.5 [&>span:nth-child(1)]:bg-[var(--color-paper)] [&>span:nth-child(4)]:bg-[var(--color-paper)]" />
        rebase
      </Link>
      <nav className="flex flex-col gap-1">
        <Link to="/me" onClick={onNavigate} className={cn(NAV_LINK, drawer && 'min-h-11')}>
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
              <Link
                key={to}
                to={to}
                onClick={onNavigate}
                className={cn(NAV_LINK, drawer && 'min-h-11')}
              >
                <Icon className="size-4" aria-hidden="true" />
                {label}
              </Link>
            ))}
          </>
        )}
      </nav>
      <div className="mt-auto space-y-2 px-2 text-xs text-sidebar-foreground/70">
        <p className="truncate">{me.email}</p>
        <Button
          variant="ghost"
          size={drawer ? 'default' : 'sm'}
          className={cn(
            'w-full justify-start text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground',
            drawer && 'min-h-11',
          )}
          onClick={() => logout.mutate()}
        >
          <LogOut className="mr-2 size-4" />
          Esci
        </Button>
      </div>
    </>
  )
}
