import { Link, useRouterState } from '@tanstack/react-router'
import type { LucideIcon } from 'lucide-react'
import {
  Briefcase,
  Building2,
  CalendarDays,
  ChevronDown,
  ChevronsUpDown,
  Clock,
  Handshake,
  KeyRound,
  LayoutDashboard,
  LogOut,
  PanelLeftIcon,
  Plug,
  Receipt,
  Rocket,
  Search,
  Settings,
  UserRound,
  Users,
  Wallet,
} from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { BrandMark } from '@/components/BrandMark'
import { readSidebarGroups, writeSidebarGroups } from '@/components/sidebarGroups'
import { CommandPalette } from '@/features/search/CommandPalette'
import { SETTINGS_TABS, type SettingsTabValue } from '@/features/settings/tabs'
import { ConnectAgentDialog } from '@/features/tokens/ConnectAgentDialog'
import { useMediaQuery } from '@/hooks/use-media-query'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Button } from '@rebase/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@rebase/ui/dropdown-menu'
import { useAuth, useIsAdmin } from '@/lib/auth'
import { canSeeSettingsTab } from '@/lib/permissions'
import { roleLabel } from '@/lib/roles'
import { cn } from '@rebase/ui/cn'

/**
 * The shell of the app: a dark sidebar on the left, everything else the page, full
 * width from the sidebar to the edge of the window (REB-328).
 *
 * Three things moved in this revision. The global search is a field at the top of the
 * sidebar rather than a control in a top bar -- there is no top bar any more, so a page's
 * own `PageHeader` is the first thing on the page and owns the title and the primary
 * action. The navigation is grouped instead of flat: nine entries in one column had no
 * reading order left to give, while «Vendite» / «Amministrazione» say what the sections
 * are for. And the page scrolls on its own, so the sidebar never scrolls away with
 * it.
 *
 * Home is the dashboard, `/app`, and it is where the economic reading of the books now
 * lives: the «Analisi» entry that stood between it and «Token» is gone with the section
 * it opened (2026-09-09), whose estimate is a card in Home and whose two reports left the
 * interface. `GET /api/analytics/*` and the MCP tools are untouched.
 *
 * Below `lg` the sidebar is the icon rail by default and its expanded form is an overlay
 * over the page, dismissed by a backdrop or by navigating: 272px of the 390px a phone has
 * would leave the content about 118px, which is not a layout. Above `lg` it is the column
 * beside the page, collapsible to the same rail as before.
 */

// Token is a top-level entry, not one of the settings sub-items below: a personal access
// token belongs to whoever creates it, any role (`PatService` scopes by `actor.id`, not
// role), while the whole «Impostazioni» group is admin-only. Putting it inside that group
// would mean a collaborator could never connect an agent to their own account.
const TOP_LEVEL = [
  { to: '/app', label: 'Home', icon: LayoutDashboard, exact: true },
  // The start page (ORB-180, REB-222): the Gmail door, the assistant and the first steps.
  // The Home is this same page while the space is empty; this entry, right under Home and
  // for every role, keeps it one click away once the Home has become the dashboard.
  { to: '/app/get-started', label: 'Primi passi', icon: Rocket, exact: false },
  // Under Home, at Ivan's request (2026-09-09), and top-level rather than inside a
  // group for the same reason Home is: a month of one's own days and deadlines is a
  // cross-cutting view, not a step of «Vendite» or of «Amministrazione».
  { to: '/app/calendar', label: 'Calendario', icon: CalendarDays, exact: false },
  { to: '/app/token', label: 'Token', icon: KeyRound, exact: false },
] as const

/**
 * The two collapsible groups. The sub-items carry an icon even though the expanded
 * rendering shows only their label: the icon is what the collapsed rail draws, where
 * these become plain icon links and the group header disappears.
 *
 * «Amministrazione» reads in the order the money is supposed to move: the register, then
 * the hours the next invoice is built from. Neither group is admin-gated: every write
 * behind these pages is gated at the service, where the refusal belongs. The Solleciti
 * page left the menu on 2026-09-09 at Ivan's request; its API and MCP tools remain.
 */
const GROUPS = [
  {
    id: 'vendite',
    label: 'Vendite',
    icon: Briefcase,
    items: [
      { to: '/app/customers', label: 'Clienti', icon: Building2 },
      { to: '/app/people', label: 'Persone', icon: Users },
      { to: '/app/deal', label: 'Deal', icon: Handshake },
    ],
  },
  {
    id: 'amministrazione',
    label: 'Amministrazione',
    icon: Wallet,
    items: [
      { to: '/app/invoices', label: 'Fatture', icon: Receipt },
      { to: '/app/hours', label: 'Ore', icon: Clock },
    ],
  },
] as const

/**
 * «Impostazioni», whose sub-items are the settings tabs -- one per entry of the shared
 * `SETTINGS_TABS`, so the sidebar cannot drift from the page that renders them.
 *
 * Its own constant, separate from `GROUPS`, because it behaves differently in two ways:
 * this whole group is shown in the sidebar to an admin only (below, `groups = isAdmin ?
 * ...`), and in the collapsed rail it becomes a single icon link -- fourteen icons for
 * the tabs of one page would be a rail of settings and nothing else.
 *
 * Every service behind these tabs calls `actor.require_admin` on every write except
 * one: `profilo` (`ProfilePanel`) is a person's own preferences, and `SettingsLayout`
 * exempts that one tab from the page's own admin gate so a non-admin who opens it
 * directly -- from the weekly digest's opt-out link, spec 2026-09-16 §3.6 -- can reach
 * it even though this sidebar link stays admin-only, same as the rest of the group.
 */
/**
 * One literal path per settings tab. `satisfies Record<SettingsTabValue, ...>` is what
 * makes this exhaustive: adding a tab to `SETTINGS_TABS` without a route here is a
 * compile error, which is the whole point of keeping the labels in one place and the
 * paths in the place that can typecheck them.
 */
const SETTINGS_PATHS = {
  profile: '/app/settings/profile',
  space: '/app/settings/space',
  fields: '/app/settings/fields',
  pipeline: '/app/settings/pipeline',
  template: '/app/settings/template',
  issuer: '/app/settings/issuer',
  fiscal: '/app/settings/fiscal',
  users: '/app/settings/users',
  'cost-categories': '/app/settings/cost-categories',
  rates: '/app/settings/rates',
  periods: '/app/settings/periods',
  gmail: '/app/settings/gmail',
  drive: '/app/settings/drive',
  automations: '/app/settings/automations',
} as const satisfies Record<SettingsTabValue, string>
const SETTINGS = {
  id: 'impostazioni',
  label: 'Impostazioni',
  icon: Settings,
  base: '/app/settings',
  // The tab value travels with the item so the sidebar can filter through the same
  // `canSeeSettingsTab` the settings page filters its tabs with (REB-294): one table,
  // two readers, no private list on either side. The group itself stays admin-only
  // (`isAdmin ? ...` below), so today every tab passes for the group's only reader;
  // what the filter buys is that a tab whose visibility changes at the page changes
  // here too, without a second decision.
  items: SETTINGS_TABS.map((tab) => ({
    to: SETTINGS_PATHS[tab.value],
    label: tab.label,
    tab: tab.value,
  })),
} as const

/**
 * Every path the sidebar links to, as a union of literals: that is what makes
 * `<Link to={item.to}>` check against the generated route tree at all, and what would
 * fail to compile the day one of these routes is renamed.
 */
type LinkTo =
  | (typeof TOP_LEVEL)[number]['to']
  | (typeof GROUPS)[number]['items'][number]['to']
  | (typeof SETTINGS_PATHS)[SettingsTabValue]

// `metaKey` on Apple platforms, `ctrlKey` elsewhere. Read once at module scope from the
// platform hint rather than sniffing the user agent string: this only decides which glyph
// is drawn, and the palette's listener accepts either modifier regardless.
const IS_APPLE =
  typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform ?? '')

/**
 * The focus ring of every control in here, on every one of them.
 *
 * `--sidebar-ring` is Paper (see `tokens.css`), not the app-wide `--ring`: Watermelon at
 * 50% over Prussian Blue is 1.73:1, which is a ring nobody can see -- and shadcn's
 * `Button` sets `outline-none`, so an invisible ring is *no* visible focus at all. The
 * `cn()` here is `twMerge`, so this overrides the ring colour the button variant sets.
 */
const FOCUS =
  'outline-none focus-visible:ring-[3px] focus-visible:ring-sidebar-ring focus-visible:ring-offset-0'

const ITEM =
  'relative flex items-center gap-3 px-3 py-2 text-sm transition-colors before:absolute before:left-0 before:top-1/2 before:size-1.5 before:-translate-y-1/2 before:bg-transparent before:content-[""]'
// The active mark of the record of 2026-09-18: the lighter translucent fill on the dark
// sidebar (the group and the item inside it are marked at once, and two solid fills
// would fight), plus the Watermelon Strong tile beside the item -- a small square at the
// row's left edge, drawn by `ITEM`'s `before:` and painted only when active, so the
// label never shifts when a route changes which row carries it.
const ACTIVE =
  'data-[status=active]:before:bg-sidebar-primary data-[status=active]:bg-sidebar-accent data-[status=active]:text-sidebar-accent-foreground data-[status=active]:font-medium'
const QUIET = 'text-sidebar-foreground/70 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground'

/** Below this the sidebar is a rail whose expanded form is an overlay, not a column. */
const DESKTOP = '(min-width: 1024px)'

/** Is `pathname` this entry, or a page below it (a detail route, a tab)? */
function matches(pathname: string, to: string, exact = false) {
  return exact ? pathname === to : pathname === to || pathname.startsWith(`${to}/`)
}

export function AppShell({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth()
  const isAdmin = useIsAdmin()
  const { location } = useRouterState()
  // Below `lg` a 272px sidebar leaves ~118px of page on a 390px phone, so there the
  // sidebar is the rail by default and its expanded form is an overlay over the content.
  // The breakpoint has to be read in JS and not only in CSS because the two are different
  // *structures*, not two widths of one.
  const isDesktop = useMediaQuery(DESKTOP)
  // Local, unpersisted UI state -- collapsing to an icon rail is a per-visit
  // convenience, not a setting worth a round trip or a storage key. Which groups are
  // open *is* stored (see `sidebarGroups.ts`): it is a standing preference about the
  // shape of your own navigation.
  const [collapsed, setCollapsed] = useState(false)
  // The overlay, stored as *where* it was opened rather than as a boolean: navigating
  // closes it, with no effect to synchronise, because the pathname it was opened at is no
  // longer the current one. A menu that stays open over the page you just asked for is
  // the classic mobile-drawer bug.
  const [openedAt, setOpenedAt] = useState<string | null>(null)
  // The palette is mounted here, once, rather than inside the search field: it owns the
  // Cmd/Ctrl+K listener, so it has to be alive even while the field has never been
  // clicked.
  const [searchOpen, setSearchOpen] = useState(false)
  // The connect-agent dialog, same idea as the search palette: mounted here once so its
  // trigger can sit in the sidebar as a plain button rather than a route.
  const [agentOpen, setAgentOpen] = useState(false)

  // The «Impostazioni» sub-items this reader may see (REB-294): the same
  // `canSeeSettingsTab` the settings page filters its own tabs with, so sidebar and
  // page read one table and cannot drift. The group's own visibility stays the
  // admin check below.
  const settingsItems = SETTINGS.items.filter((item) =>
    user === null ? false : canSeeSettingsTab(user.ruolo, item.tab),
  )
  const groups = isAdmin ? [...GROUPS, SETTINGS] : GROUPS
  const activeGroup = groups.find((group) =>
    'base' in group
      ? matches(location.pathname, group.base)
      : group.items.some((item) => matches(location.pathname, item.to)),
  )?.id

  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>(readSidebarGroups)
  // The group holding the current route opens by itself unless you dismissed it in this
  // session: the alternative is a page whose own entry in the sidebar is not on screen,
  // and the alternative to *that* -- nailing it open -- is a chevron that does nothing.
  // The dismissal is deliberately not stored: what gets written is the preference you
  // chose, not what one route did on your behalf, so a reload of the same page opens the
  // group again.
  const [dismissed, setDismissed] = useState<string | undefined>(undefined)
  const isOpen = (id: string) =>
    openGroups[id] === true || (id === activeGroup && dismissed !== id)

  const toggleGroup = (id: string) => {
    const next = { ...openGroups, [id]: !isOpen(id) }
    if (id === activeGroup) setDismissed(isOpen(id) ? id : undefined)
    setOpenGroups(writeSidebarGroups(next))
  }

  // What the sidebar is showing right now, and what the one toggle does to it.
  const overlay = !isDesktop && openedAt === location.pathname
  const expanded = isDesktop ? !collapsed : overlay
  const rail = !expanded
  const toggleSidebar = () =>
    isDesktop
      ? setCollapsed((value) => !value)
      : setOpenedAt(overlay ? null : location.pathname)

  const initials = (user?.nome ?? '?')
    .split(' ')
    .map((part) => part[0])
    .join('')
    .slice(0, 2)
    .toUpperCase()

  /** A leaf entry: an icon and a label expanded, an icon alone in the rail. */
  const leaf = ({
    to,
    label,
    icon: Icon,
    exact = false,
  }: {
    to: LinkTo
    label: string
    icon: LucideIcon
    exact?: boolean
  }) => (
    <Link
      key={to}
      to={to}
      // `includeSearch: false`, because TanStack defaults it to true: none of these links
      // carries a search of its own, so with the default an entry stopped being active
      // the moment its page put anything in the URL -- and Home, whose dashboard writes
      // its tab and period there on load, was never highlighted at all.
      activeOptions={{ exact, includeSearch: false }}
      activeProps={{ 'aria-current': 'page' }}
      className={cn(ITEM, QUIET, ACTIVE, FOCUS, rail && 'justify-center px-0')}
    >
      <Icon className="size-4 shrink-0" aria-hidden="true" />
      <span className={cn('truncate', rail && 'sr-only')}>{label}</span>
    </Link>
  )

  return (
    <div className="flex h-full overflow-hidden">
      {overlay && (
        <>
          {/* A real button, not an `aria-hidden` div: dismissing an overlay is something a
              keyboard has to be able to do, and this is the control that does it. */}
          <button
            type="button"
            aria-label="Chiudi il menu"
            onClick={() => setOpenedAt(null)}
            className="fixed inset-0 z-30 bg-[var(--color-prussian-blue)]/50"
          />
          {/* The rail's own width, kept in the flow while the sidebar itself is out of it,
              so the page underneath does not shift as the overlay opens and closes. */}
          <div aria-hidden="true" className="w-[4.5rem] shrink-0" />
        </>
      )}
      <aside
        className={cn(
          // As tall as the viewport, never as tall as the page: the profile at the bottom
          // is reachable without scrolling, and the navigation scrolls on its own if it
          // ever outgrows the window. `h-full` of the shell (itself `h-full` of #root),
          // not `100dvh`: see the html/body/#root rule in tokens.css.
          'flex h-full shrink-0 flex-col bg-sidebar text-sidebar-foreground transition-[width] duration-200 ease-linear',
          rail ? 'w-[4.5rem]' : 'w-[17rem]',
          // Below `lg` the expanded sidebar is an overlay over the content, not a column
          // beside it: 272px of the 390px a phone has is not a layout, it is a menu.
          overlay && 'fixed inset-y-0 left-0 z-40 shadow-2xl',
        )}
      >
        <div
          className={cn(
            'flex items-center gap-2 px-4 pt-5 pb-3',
            rail ? 'justify-center' : 'justify-between',
          )}
        >
          <span
            className={cn(
              'inline-flex items-center truncate text-lg font-medium tracking-tight',
              rail && 'sr-only',
            )}
          >
            <BrandMark className="mr-2.5" />
            {/* Brand accent, not body text: --color-watermelon (not the AA-adjusted
                -strong variant) is exactly what the design tokens reserve for this. */}
            Pigro<span className="text-[var(--color-watermelon)]">CRM</span>
          </span>
          <Button
            variant="ghost"
            size="icon-sm"
            className={cn(
              'shrink-0 text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-foreground',
              FOCUS,
            )}
            onClick={toggleSidebar}
            aria-label={rail ? 'Espandi il menu' : 'Comprimi il menu'}
          >
            <PanelLeftIcon className="size-4" />
          </Button>
        </div>

        {/* A button that looks like a field, not an <input>: the palette is a dialog, so a
            real text field here would take focus, accept typing, and then hand it over --
            two places to type the same query. One control, one place to type. */}
        {/* Named, for the same reason the nav below is: a landmark whose accessible name
            is only its role tells a screen-reader user nothing, and a page's own filter
            row is entitled to a search landmark of its own. */}
        <div role="search" aria-label="Ricerca globale" className="px-3 pb-3">
          <button
            type="button"
            onClick={() => setSearchOpen(true)}
            aria-label="Cerca in tutto il CRM"
            className={cn(
              'flex w-full items-center gap-2 border border-sidebar-border bg-sidebar-accent/50 px-3 py-2 text-sm text-sidebar-foreground/70 transition-colors hover:bg-sidebar-accent hover:text-sidebar-foreground',
              FOCUS,
              rail && 'justify-center px-0',
            )}
          >
            <Search className="size-4 shrink-0" aria-hidden="true" />
            <span className={cn('flex-1 text-left', rail && 'sr-only')}>Cerca</span>
            <kbd
              className={cn(
                'border border-sidebar-border px-1.5 py-0.5 text-xs font-medium',
                rail && 'sr-only',
              )}
            >
              {IS_APPLE ? '⌘' : 'Ctrl'} K
            </kbd>
          </button>
        </div>

        {/* Labelled because a page's own header may render a second <nav> landmark (a
            breadcrumb, a set of tabs), and two unlabelled ones are indistinguishable to a
            screen reader -- and to `getByRole('navigation')`. */}
        <nav
          aria-label="Navigazione principale"
          className="min-h-0 flex-1 space-y-1 overflow-y-auto px-3 pb-3"
        >
          {/* Home, then «Primi passi» right under it (ORB-180); the other top-level
              entries follow the groups. */}
          {leaf(TOP_LEVEL[0])}
          {leaf(TOP_LEVEL[1])}

          {rail
            ? // The rail: no headers, no indentation, every section one click away. The
              // settings tabs are the exception -- one link to the page that owns them.
              [
                ...GROUPS.flatMap((group) => group.items.map((item) => leaf(item))),
                ...TOP_LEVEL.slice(2).map((item) => leaf(item)),
                // The first tab, under the group's own name: in the rail the label is the
                // accessible name, and «Spazio» would say nothing about where it goes.
                isAdmin
                  ? leaf({ to: SETTINGS_PATHS.space, label: SETTINGS.label, icon: Settings })
                  : null,
              ]
            : [
                ...GROUPS.map((group) => (
                  <NavGroup
                    key={group.id}
                    label={group.label}
                    icon={group.icon}
                    open={isOpen(group.id)}
                    onToggle={() => toggleGroup(group.id)}
                  >
                    {group.items.map((item) => subItem(item))}
                  </NavGroup>
                )),
                ...TOP_LEVEL.slice(2).map((item) => leaf(item)),
                isAdmin ? (
                  <NavGroup
                    key={SETTINGS.id}
                    label={SETTINGS.label}
                    icon={SETTINGS.icon}
                    open={isOpen(SETTINGS.id)}
                    onToggle={() => toggleGroup(SETTINGS.id)}
                  >
                    {settingsItems.map((item) => subItem(item))}
                  </NavGroup>
                ) : null,
              ]}
        </nav>

        {/* «Collega un agente» (ORB-170): for every role, like Token, because a token
            belongs to whoever creates it. A button and not a route: the dialog is the
            whole surface. In the rail the label is for screen readers only. */}
        <div className="px-3 pb-1">
          <button
            type="button"
            onClick={() => setAgentOpen(true)}
            className={cn(ITEM, QUIET, FOCUS, 'w-full', rail && 'justify-center px-0')}
          >
            <Plug className="size-4 shrink-0" aria-hidden="true" />
            <span className={cn('truncate', rail && 'sr-only')}>Collega un agente</span>
          </button>
        </div>
        <ConnectAgentDialog open={agentOpen} onOpenChange={setAgentOpen} />

        {/* The profile, anchored at the bottom, opens a menu: the account's own things --
            who is signed in, the space's settings for an admin, the way out. */}
        <div className="mt-auto border-t border-sidebar-border p-3">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button
                type="button"
                className={cn(
                  'flex w-full items-center gap-3 px-2 py-2 text-left transition-colors hover:bg-sidebar-accent',
                  FOCUS,
                  rail && 'justify-center px-0',
                )}
                aria-label="Menu del profilo"
              >
                <Avatar className="size-8 shrink-0">
                  <AvatarFallback className="bg-sidebar-accent text-xs text-sidebar-foreground">
                    {initials}
                  </AvatarFallback>
                </Avatar>
                <div className={cn('min-w-0 flex-1', rail && 'sr-only')}>
                  <p className="truncate text-sm font-medium">{user?.nome}</p>
                  {/* The role in words: «admin» is what the API stores, not what the
                      product writes (spec §5.5). */}
                  <p className="truncate text-xs text-sidebar-foreground/70">
                    {user ? roleLabel(user.ruolo) : null}
                  </p>
                </div>
                <ChevronsUpDown
                  className={cn('size-4 shrink-0 text-sidebar-foreground/70', rail && 'hidden')}
                  aria-hidden="true"
                />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent side="top" align="start" className="w-56">
              <DropdownMenuLabel className="font-normal">
                <p className="truncate text-sm font-medium">{user?.nome}</p>
                <p className="truncate text-xs text-muted-foreground">{user?.email}</p>
              </DropdownMenuLabel>
              <DropdownMenuSeparator />
              {/* «Profilo», for every role and with no gate on it: the sidebar's whole
                  «Impostazioni» group is admin-only, so without this entry a
                  collaborator had no way of reaching their own profile tab from inside
                  the app at all -- the weekly report's opt-out link (spec 2026-09-16
                  §3.6) was the only door to it. One menu item, above the admin's own
                  entry, because it is the one thing here that belongs to the person
                  rather than to the space. */}
              <DropdownMenuItem asChild>
                <Link to="/app/settings/profile">
                  <UserRound className="size-4" />
                  Profilo
                </Link>
              </DropdownMenuItem>
              {isAdmin && (
                <DropdownMenuItem asChild>
                  <Link to="/app/settings/space">
                    <Settings className="size-4" />
                    Impostazioni dello spazio
                  </Link>
                </DropdownMenuItem>
              )}
              <DropdownMenuItem onSelect={() => void logout()}>
                <LogOut className="size-4" />
                Esci
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </aside>

      {/* The page: white from the sidebar's edge to the window's, no inset frame and no
          border of its own. The 12px grid-ground margin and the `lg:border` were copied
          from the hub's panel and read as a card scrolling on its own inside the page
          (REB-328, Lorenzo 2026-09-22: «full width dalla sidebar fino alla fine della
          pagina e senza margini»). `<main>` is the one scroller of the application,
          which the root's `overflow-hidden` guarantees; the pages carry their own
          padding (`PageHeader`'s `px-8`). Below `lg` nothing changes: the inset was
          already zero there. */}
      <main className="min-w-0 flex-1 overflow-y-auto bg-card">{children}</main>

      <CommandPalette open={searchOpen} onOpenChange={setSearchOpen} />
    </div>
  )
}

/**
 * A group header and, when it is open, its sub-items.
 *
 * `aria-expanded` on a real `<button>` rather than a styled `<div>`: the state of a
 * disclosure is something a screen reader has to be able to read and a keyboard has to be
 * able to change, and both come free this way.
 */
function NavGroup({
  label,
  icon: Icon,
  open,
  onToggle,
  children,
}: {
  label: string
  icon: LucideIcon
  open: boolean
  onToggle: () => void
  children: ReactNode
}) {
  return (
    <div>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className={cn(ITEM, QUIET, FOCUS, 'w-full')}
      >
        <Icon className="size-4 shrink-0" aria-hidden="true" />
        <span className="flex-1 truncate text-left">{label}</span>
        <ChevronDown
          className={cn('size-4 shrink-0 transition-transform', !open && '-rotate-90')}
          aria-hidden="true"
        />
      </button>
      {open && (
        // The thin vertical line of the reference: one border on the list, so it runs the
        // height of the sub-items whatever their number.
        <ul className="mt-1 ml-6 space-y-0.5 border-l border-sidebar-border pl-3">{children}</ul>
      )}
    </div>
  )
}

/** A sub-item: label only. The indentation and the line say where it belongs. */
function subItem({ to, label }: { to: LinkTo; label: string }) {
  return (
    <li key={to}>
      <Link
        to={to}
        // As on the leaves above: a list that puts its filter in the URL keeps its own
        // entry marked.
        activeOptions={{ includeSearch: false }}
        activeProps={{ 'aria-current': 'page' }}
        className={cn(
          'relative block truncate px-3 py-1.5 text-sm transition-colors before:absolute before:left-0 before:top-1/2 before:size-1.5 before:-translate-y-1/2 before:bg-transparent before:content-[""]',
          QUIET,
          ACTIVE,
          FOCUS,
        )}
      >
        {label}
      </Link>
    </li>
  )
}
