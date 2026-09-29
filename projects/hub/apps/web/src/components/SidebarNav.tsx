import { Link, useLocation } from '@tanstack/react-router'
import { Fragment } from 'react'
import {
  BookOpen,
  Boxes,
  Briefcase,
  Cloud,
  Handshake,
  Home,
  LogIn,
  LogOut,
  Plug,
  Send,
  Share2,
  ShieldCheck,
  UserRound,
  Users,
} from 'lucide-react'
import { BrandMark } from '@/components/BrandMark'
import { Button } from '@rebase/ui/button'
import { cn } from '@rebase/ui/cn'
import type { Me } from '@/lib/api'
import { useLogout } from '@/lib/me'

/** The admin pages, in the order an admin reaches for them (REB-609), in four groups a
 *  hairline apart: what is worked every day, in the order a piece of work travels
 *  (a person, the company that asks, the team it asks for, the match that joins them, the
 *  referral that match may earn); the mails sent to the community; what is set up or
 *  watched now and then (the Pigro instances, the guide); and who may get in (accesses,
 *  administrators, agents). «Talenti» is the merge of the old «Developer e CTO» and
 *  «Iscrizioni» (REB-283); a page renamed or moved keeps its route through a redirect in
 *  `router.tsx`, so this list is the only place the order lives. */
const ADMIN_NAV = [
  [
    { to: '/admin/talent', label: 'Talenti', icon: UserRound },
    { to: '/admin/companies', label: 'Aziende', icon: Briefcase },
    { to: '/admin/team', label: 'Richieste team', icon: Users },
    { to: '/admin/matches', label: 'Match', icon: Handshake },
    { to: '/admin/referrals', label: 'Referral', icon: Share2 },
  ],
  [{ to: '/admin/campaigns', label: 'Campagne', icon: Send }],
  [
    { to: '/admin/pigro', label: 'Istanze Pigro', icon: Boxes },
    { to: '/admin/guide', label: 'La guida', icon: BookOpen },
  ],
  [
    { to: '/admin/access', label: 'Accessi', icon: LogIn },
    { to: '/admin/admins', label: 'Amministratori', icon: ShieldCheck },
    { to: '/admin/agents', label: 'Agenti', icon: Plug },
  ],
] as const

/* The record of 2026-09-18, as #228 drew it in the CRM's AppShell: the active item is
   marked by a Watermelon Strong tile at the row's left edge (the `before:` square, painted
   only when active so the label never shifts), over the lighter translucent fill the dark
   sidebar already had. TanStack's default `activeClass` is `active`, which is what the
   `[&.active]` selectors read. */
const NAV_LINK =
  'relative flex items-center gap-2 px-2 py-1.5 text-sm transition-colors before:absolute before:left-0 before:top-1/2 before:size-1.5 before:-translate-y-1/2 before:bg-transparent before:content-[""] text-sidebar-foreground/70 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground [&.active]:bg-sidebar-accent [&.active]:font-medium [&.active]:text-sidebar-foreground [&.active]:before:bg-sidebar-primary'

/** `/me/cloud` (REB-518) is a child route of `/me`, same as `/me/edit` and its
 *  siblings, so TanStack's own prefix matching would keep "La tua area" lit up there
 *  too -- both entries active at once. It should stay lit for `/me` itself and its
 *  other children, the way the admin entries stay lit for theirs (e.g. `Talenti` on
 *  `/admin/talent/$id`), just not for the one child that carries its own nav entry. */
function isMeActive(pathname: string): boolean {
  return pathname === '/me' || (pathname.startsWith('/me/') && !pathname.startsWith('/me/cloud'))
}

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
  const pathname = useLocation({ select: (location) => location.pathname })
  const meActive = isMeActive(pathname)

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
        <Link
          to="/me"
          onClick={onNavigate}
          activeOptions={{ exact: true }}
          aria-current={meActive ? 'page' : undefined}
          className={cn(NAV_LINK, meActive && 'active', drawer && 'min-h-11')}
        >
          <Home className="size-4" aria-hidden="true" />
          La tua area
        </Link>
        {/* REB-518: while a grant of theirs is live, the cloud rebase opened for their
            company (spec § 4.1); an admin with none does not see it either. */}
        {me.talent_cloud && (
          <Link to="/me/cloud" onClick={onNavigate} className={cn(NAV_LINK, drawer && 'min-h-11')}>
            <Cloud className="size-4" aria-hidden="true" />
            Talent cloud
          </Link>
        )}
        {isAdmin && (
          <>
            <p className="mt-2 px-2 text-xs font-medium tracking-wide text-sidebar-foreground/70 uppercase">
              Amministrazione
            </p>
            <div role="separator" className="my-2 border-t border-sidebar-border" />
            {ADMIN_NAV.map((group, index) => (
              <Fragment key={group[0].to}>
                {index > 0 && <div role="separator" className="my-1 border-t border-sidebar-border" />}
                {group.map(({ to, label, icon: Icon }) => (
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
              </Fragment>
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
