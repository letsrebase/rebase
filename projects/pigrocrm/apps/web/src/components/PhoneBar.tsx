import { Link, useRouterState } from '@tanstack/react-router'
import { Ellipsis, LayoutDashboard, ListChecks, Plus, Search } from 'lucide-react'
import { useState } from 'react'
import { cn } from '@rebase/ui/cn'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@rebase/ui/sheet'
import { useWaitingCount } from '@/features/inbox/queries'
import { useUnreadCount } from '@/features/reminders/queries'
import { useOverdueCount } from '@/features/tasks/queries'
import { useCanWrite } from '@/lib/auth'
import { useModules } from '@/lib/modules'
import { LegalEntitySelect } from './LegalEntitySelect'
import {
  Agents,
  INBOX_PATH,
  REGISTRY_PATH,
  SETTINGS,
  SETTINGS_PATHS,
  isNavItemActive,
  isUnder,
  navKey,
  phoneMoreItems,
  phoneThirdPlace,
  type LinkTo,
  type NavItem,
} from './nav'
import { ProfileMenu } from './ProfileMenu'
import { QuickAdd } from './QuickAdd'
import { FOCUS } from './sidebarStyles'

/** The bar's height without the home indicator's inset: the page keeps this much clear. */
export const PHONE_BAR_HEIGHT = '4rem'

/**
 * The phone's navigation (spec 2026-11-09 B6, the `pigrocrm-phone-shell` brief Lorenzo
 * approved on 2026-11-09, REB-959): five places under the thumb, with the square Watermelon
 * «+» in the centre. «Home» and «Attività» (its late count on the corner), then, around the
 * «+», the first switched-on of «Deal» and «Giornate» and «Altro», which opens everything else
 * the space has on. With neither module on there are four places. Drawn instead of the
 * sidebar below 640px, never beside it.
 *
 * What is on comes from the same tables as the sidebar (`nav.ts`, `ROUTE_MODULES`), so a
 * module that is off is gone from here, from «Altro» and from «+» at once.
 */
export function PhoneBar({
  onSearch,
  go,
}: {
  onSearch: () => void
  go: (url: string) => void
}) {
  const modules = useModules()
  const canWrite = useCanWrite()
  const overdue = useOverdueCount()
  const third = phoneThirdPlace(modules)
  const { location } = useRouterState()
  const [quickOpen, setQuickOpen] = useState(false)
  // «Altro» is stored as *where* it was opened, as the sidebar's overlay is: a move made from
  // inside it (the profile menu's links) closes it with no handler to remember, because the
  // pathname it was opened at is no longer the current one.
  const [moreAt, setMoreAt] = useState<string | null>(null)
  const moreOpen = moreAt === location.pathname
  const setMoreOpen = (open: boolean) => setMoreAt(open ? location.pathname : null)

  const places = [
    { to: '/app', label: 'Home', icon: LayoutDashboard, exact: true, badge: undefined },
    { to: '/app/activities', label: 'Attività', icon: ListChecks, exact: false, badge: overdue },
    ...(third === null ? [] : [{ ...third, exact: false, badge: undefined }]),
  ] as const
  // The «+» sits in the middle of what is drawn: before the third place when there is one
  // (Home, Tasks, «+», Deal, Altro), after the two fixed ones when there is none.
  const before = places.slice(0, 2)
  const after = places.slice(2)

  return (
    <>
      <nav
        aria-label="Navigazione principale"
        className="fixed inset-x-0 bottom-0 z-30 border-t border-sidebar-border bg-sidebar pb-[env(safe-area-inset-bottom)] text-sidebar-foreground"
      >
        <ul className="flex items-stretch" style={{ height: PHONE_BAR_HEIGHT }}>
          {before.map((place) => (
            <Place key={place.to} {...place} />
          ))}
          {canWrite ? (
            <li className="flex flex-1 items-center justify-center">
              <button
                type="button"
                aria-label="Aggiungi"
                onClick={() => setQuickOpen(true)}
                className={cn(
                  'flex size-12 items-center justify-center bg-sidebar-primary text-sidebar-primary-foreground transition-colors hover:brightness-110',
                  FOCUS,
                )}
              >
                <Plus className="size-6" aria-hidden="true" />
              </button>
            </li>
          ) : null}
          {after.map((place) => (
            <Place key={place.to} {...place} />
          ))}
          <li className="flex flex-1">
            <button
              type="button"
              onClick={() => setMoreOpen(true)}
              aria-haspopup="dialog"
              className={cn(PLACE, QUIET_PLACE, FOCUS)}
            >
              <Ellipsis className="size-5" aria-hidden="true" />
              <span>Altro</span>
            </button>
          </li>
        </ul>
      </nav>

      {canWrite ? <QuickAdd open={quickOpen} onOpenChange={setQuickOpen} /> : null}
      <More
        open={moreOpen}
        onOpenChange={setMoreOpen}
        onSearch={() => {
          setMoreOpen(false)
          onSearch()
        }}
        go={go}
      />
    </>
  )
}

const PLACE =
  'relative flex flex-1 flex-col items-center justify-center gap-1 px-1 text-xs transition-colors'
const QUIET_PLACE = 'text-sidebar-foreground/70 hover:text-sidebar-foreground'

function Place({
  to,
  label,
  icon: Icon,
  exact,
  badge,
}: {
  to: LinkTo
  label: string
  icon: typeof Search
  exact: boolean
  badge: number | undefined
}) {
  return (
    <li className="flex flex-1">
      <Link
        to={to}
        activeOptions={{ exact, includeSearch: false }}
        activeProps={{ 'aria-current': 'page' }}
        className={cn(
          PLACE,
          QUIET_PLACE,
          FOCUS,
          // The active place carries the same Watermelon Strong tile as the sidebar's row,
          // along the top edge here, and the sidebar's active text.
          'before:absolute before:top-0 before:h-0.5 before:w-8 before:bg-transparent before:content-[""] data-[status=active]:font-medium data-[status=active]:text-sidebar-accent-foreground data-[status=active]:before:bg-sidebar-primary',
        )}
      >
        <Icon className="size-5" aria-hidden="true" />
        <span>{label}</span>
        {badge !== undefined && (
          <span className="absolute top-1.5 left-1/2 ml-2 min-w-4 bg-sidebar-primary px-1 text-center text-[0.6875rem] font-medium tabular-nums text-sidebar-primary-foreground">
            <span aria-hidden="true">{badge > 99 ? '99+' : badge}</span>
            <span className="sr-only">{badge} in ritardo</span>
          </span>
        )}
      </Link>
    </li>
  )
}

/**
 * «Altro»: every place the bar does not draw and the space has on, then the account's own
 * entries the sidebar keeps at its foot: search, «Agenti», «Impostazioni», the azienda
 * selector and the profile menu with the space switcher.
 */
function More({
  open,
  onOpenChange,
  onSearch,
  go,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSearch: () => void
  go: (url: string) => void
}) {
  const modules = useModules()
  const unread = useUnreadCount()
  const waiting = useWaitingCount()
  const { location } = useRouterState()
  const registryRole = (location.search as { role?: string }).role
  const items: readonly NavItem[] = [
    ...phoneMoreItems(modules),
    { to: Agents.to, label: Agents.label, icon: Agents.icon },
    { to: SETTINGS_PATHS.profile, label: SETTINGS.label, icon: SETTINGS.icon },
  ]

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="bottom"
        closeLabel="Chiudi il menu"
        className="max-h-[85dvh] gap-0 overflow-y-auto border-sidebar-border bg-sidebar pb-[env(safe-area-inset-bottom)] text-sidebar-foreground"
      >
        <SheetHeader>
          <SheetTitle className="text-sidebar-foreground">Altro</SheetTitle>
          <SheetDescription className="text-sidebar-foreground/70">
            Tutto quello che questo spazio ha acceso.
          </SheetDescription>
        </SheetHeader>
        <div role="search" aria-label="Ricerca globale" className="px-3 pb-3">
          <button
            type="button"
            onClick={onSearch}
            aria-label="Cerca in tutto il CRM"
            className={cn(
              'flex w-full items-center gap-2 border border-sidebar-border bg-sidebar-accent/50 px-3 py-3 text-sm text-sidebar-foreground/70',
              FOCUS,
            )}
          >
            <Search className="size-4" aria-hidden="true" />
            Cerca
          </button>
        </div>
        <LegalEntitySelect rail={false} />
        <ul className="px-3 pb-3">
          {items.map((item) => {
            const { to, label, icon: Icon } = item
            return (
            <li key={navKey(item)}>
              <Link
                to={to}
                search={item.search}
                onClick={() => onOpenChange(false)}
                aria-current={
                  (to === REGISTRY_PATH
                    ? isNavItemActive(item, location.pathname, registryRole)
                    : isUnder(location.pathname, to === SETTINGS_PATHS.profile ? SETTINGS.base : to))
                    ? 'page'
                    : undefined
                }
                className={cn(
                  'flex min-h-12 items-center gap-3 px-3 text-sm text-sidebar-foreground/80 hover:bg-sidebar-accent/60 aria-[current=page]:bg-sidebar-accent aria-[current=page]:font-medium aria-[current=page]:text-sidebar-accent-foreground',
                  FOCUS,
                )}
              >
                <Icon className="size-5 shrink-0" aria-hidden="true" />
                <span className="flex-1">{label}</span>
                {to === '/app/reminders' && unread !== undefined && unread > 0 ? (
                  <span className="bg-sidebar-primary px-1.5 text-xs font-medium tabular-nums text-sidebar-primary-foreground">
                    <span aria-hidden="true">{unread > 99 ? '99+' : unread}</span>
                    <span className="sr-only">{`, ${unread} da leggere`}</span>
                  </span>
                ) : null}
                {to === INBOX_PATH && waiting > 0 ? (
                  <span className="bg-sidebar-primary px-1.5 text-xs font-medium tabular-nums text-sidebar-primary-foreground">
                    <span aria-hidden="true">{waiting > 99 ? '99+' : waiting}</span>
                    <span className="sr-only">{`, ${waiting} da gestire`}</span>
                  </span>
                ) : null}
              </Link>
            </li>
            )
          })}
        </ul>
        <ProfileMenu rail={false} go={go} />
      </SheetContent>
    </Sheet>
  )
}
