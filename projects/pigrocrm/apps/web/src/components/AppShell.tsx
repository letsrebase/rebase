import { Link, useRouterState } from '@tanstack/react-router'
import type { LucideIcon } from 'lucide-react'
import {
  ArrowLeftRight,
  Bell,
  Bot,
  Building2,
  CalendarCheck,
  CalendarDays,
  ChevronDown,
  ChevronsUpDown,
  Clock,
  Handshake,
  HardDrive,
  Inbox,
  LayoutDashboard,
  ListChecks,
  LogOut,
  MailPlus,
  PanelLeftIcon,
  Plus,
  Receipt,
  Rocket,
  Search,
  Settings,
  UserRound,
  Users,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { BrandMark } from '@/components/BrandMark'
import { useOverdueCount } from '@/features/attivita/queries'
import { useSpaceEmpty } from '@/features/get-started/firstSteps'
import { useUnreadCount } from '@/features/reminders/queries'
import { CommandPalette } from '@/features/search/CommandPalette'
import { SETTINGS_GROUPS, type SettingsTabValue } from '@/features/settings/tabs'
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@rebase/ui/select'
import { toast } from '@rebase/ui/sonner'
import { api, toProblem, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { useAuth, useIsAdmin } from '@/lib/auth'
import { useLegalEntity } from '@/lib/legalEntity'
import { canSeeSettingsTab, type Role } from '@/lib/permissions'
import { writeRegisterHandoffEmail } from '@/lib/registerHandoff'
import { roleLabel } from '@/lib/roles'
import { tenantPrefix } from '@/lib/tenant'
import { cn } from '@rebase/ui/cn'

type IdentitySpace = components['schemas']['IdentitySpace']

/**
 * The shell of the app: a dark sidebar on the left, everything else the page, full
 * width from the sidebar to the edge of the window (REB-328).
 *
 * The global search is a field at the top of the sidebar rather than a control in a top
 * bar -- there is no top bar -- so a page's own `PageHeader` is the first thing on the page
 * and owns the title and the primary action. The page scrolls on its own, so the sidebar
 * never scrolls away with it.
 *
 * The navigation reads in the order of the brief Lorenzo approved on 2026-11-02 (sidebar A,
 * REB-873): Home; then three sections named by what they are for, drawn as captions that
 * are always open and are never controls, «Da fare» (Attività, Calendario, Avvisi,
 * Proposte), «Vendite» and «Amministrazione»; then, at the foot, «Agenti» and
 * «Impostazioni», the only entry that still opens onto sub-items. «Oggi» is no entry of
 * its own: it is the first view of the Attività page, whose overdue count it carries.
 * «Primi passi» is in the profile menu once the space has data.
 *
 * Home is the dashboard, `/app`, and it is where the economic reading of the books lives
 * (the «Analisi» section left on 2026-09-09; `GET /api/analytics/*` and the MCP tools are
 * untouched).
 *
 * Below `lg` the sidebar is the icon rail by default and its expanded form is an overlay
 * over the page, dismissed by a backdrop or by navigating: 272px of the 390px a phone has
 * would leave the content about 118px, which is not a layout. Above `lg` it is the column
 * beside the page, collapsible to the same rail as before; there the captions become thin
 * separators and every entry an icon.
 */

const HOME = { to: '/app', label: 'Home', icon: LayoutDashboard, exact: true } as const

/**
 * The three sections, each a caption over its entries. The entries carry an icon even
 * though the expanded rendering shows it too: the icon is what the collapsed rail draws,
 * where the caption becomes a separator.
 *
 * «Da fare» is what asks for a person's attention, in the order they reach for it: the
 * attività (late ones counted beside it), the calendar that draws the dated ones by day,
 * the reminders still unread (REB-734) and the proposals an agent left for a decision
 * (REB-750). «Amministrazione» reads in the order the money is supposed to move: the
 * register, the hours the next invoice is built from, the contract days (REB-745). None of
 * it is admin-gated: every write behind these pages is gated at the service, where the
 * refusal belongs. The Solleciti page left the menu on 2026-09-09 at Ivan's request; its
 * API and MCP tools remain.
 */
const SECTIONS = [
  {
    id: 'da-fare',
    caption: 'Da fare',
    items: [
      { to: '/app/activities', label: 'Attività', icon: ListChecks },
      { to: '/app/calendar', label: 'Calendario', icon: CalendarDays },
      { to: '/app/avvisi', label: 'Avvisi', icon: Bell },
      { to: '/app/proposals', label: 'Proposte', icon: Inbox },
    ],
  },
  {
    id: 'vendite',
    caption: 'Vendite',
    items: [
      { to: '/app/customers', label: 'Clienti', icon: Building2 },
      { to: '/app/people', label: 'Persone', icon: Users },
      { to: '/app/deal', label: 'Deal', icon: Handshake },
    ],
  },
  {
    id: 'amministrazione',
    caption: 'Amministrazione',
    items: [
      { to: '/app/invoices', label: 'Fatture', icon: Receipt },
      { to: '/app/hours', label: 'Ore', icon: Clock },
      { to: '/app/days', label: 'Giornate', icon: CalendarCheck },
    ],
  },
] as const

/**
 * «Agenti», at the foot with «Impostazioni» (REB-873): the personal access tokens and the
 * «Collega un agente» action that mints one. Not one of the settings sub-items: a token
 * belongs to whoever creates it, any role (`PatService` scopes by `actor.id`, not role), so
 * the page is every role's own and its route, `/app/token`, carries no admin guard.
 */
const AGENTI = { to: '/app/token', label: 'Agenti', icon: Bot } as const

/**
 * One literal path per settings tab. `satisfies Record<SettingsTabValue, ...>` is what
 * makes this exhaustive: adding a tab to `SETTINGS_TABS` without a route here is a
 * compile error, which is the whole point of keeping the labels in one place and the
 * paths in the place that can typecheck them.
 */
const SETTINGS_PATHS = {
  profile: '/app/settings/profile',
  notifications: '/app/settings/notifications',
  space: '/app/settings/space',
  fields: '/app/settings/fields',
  pipeline: '/app/settings/pipeline',
  template: '/app/settings/template',
  aziende: '/app/settings/aziende',
  users: '/app/settings/users',
  'cost-categories': '/app/settings/cost-categories',
  rates: '/app/settings/rates',
  periods: '/app/settings/periods',
  gmail: '/app/settings/gmail',
  drive: '/app/settings/drive',
  slack: '/app/settings/slack',
  reminders: '/app/settings/reminders',
  automations: '/app/settings/automations',
  jobs: '/app/settings/jobs',
} as const satisfies Record<SettingsTabValue, string>
/**
 * «Impostazioni», whose sub-items are the six settings groups of the REB-770 brief (option
 * A), built from the shared `SETTINGS_GROUPS` so the sidebar cannot drift from the page.
 *
 * Its own constant, apart from the three sections, because it is the one entry that opens
 * onto sub-items, and because in the collapsed rail it becomes a single icon link: one icon
 * per section of one page would be a rail of settings and nothing else.
 *
 * Shown to every role (R2): each role sees only the groups holding a section it may open
 * (`canSeeSettingsTab`), so a member has «Personale» alone and a collaboratore adds
 * «Collegamenti» (their own Drive, REB-457). The weekly digest's opt-out link lands on
 * Profilo for whoever got the mail (spec 2026-09-16 §3.6).
 */
const SETTINGS = {
  label: 'Impostazioni',
  icon: Settings,
  base: '/app/settings',
  // One sub-item per group of the brief (REB-770, option A), not per section: the page's
  // tab strip lists the sections of the group it is on. Each group links to its first
  // section this reader may open, read from the same `canSeeSettingsTab` the page
  // filters its tabs with (REB-294), so sidebar and page read one table.
  groups: SETTINGS_GROUPS,
} as const

/** The sub-items «Impostazioni» shows `role`: a group with a section it may open, linked to
 *  that section, and `active` when the current path is any section of the group. */
function settingsSubItems(role: Role, pathname: string) {
  return SETTINGS.groups.flatMap((group) => {
    const first = group.tabs.find((tab) => canSeeSettingsTab(role, tab.value))
    if (first === undefined) return []
    return [
      {
        to: SETTINGS_PATHS[first.value],
        label: group.label,
        active: group.tabs.some((tab) => isUnder(pathname, SETTINGS_PATHS[tab.value])),
      },
    ]
  })
}

/**
 * Every path the sidebar links to, as a union of literals: that is what makes
 * `<Link to={item.to}>` check against the generated route tree at all, and what would
 * fail to compile the day one of these routes is renamed.
 */
type LinkTo =
  | typeof HOME.to
  | (typeof SECTIONS)[number]['items'][number]['to']
  | typeof AGENTI.to
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

/** The selector's value for «Tutte le aziende»: Radix refuses an empty string, and no
 *  azienda id is this word. */
const EVERY_AZIENDA = 'tutte'

/** Below this the sidebar is a rail whose expanded form is an overlay, not a column. */
const DESKTOP = '(min-width: 1024px)'

/** Is `pathname` this entry, or a page below it (a detail route, a tab)? */
function isUnder(pathname: string, to: string, exact = false) {
  return exact ? pathname === to : pathname === to || pathname.startsWith(`${to}/`)
}

export function AppShell({
  children,
  go = (url) => window.location.assign(url),
}: {
  children: ReactNode
  go?: (url: string) => void
}) {
  const { user, logout } = useAuth()
  const isAdmin = useIsAdmin()
  // The azienda selector (REB-625): drawn from the second azienda on, between the search
  // and the navigation, so the lists below it read as «of this azienda».
  const azienda = useLegalEntity()
  const { location } = useRouterState()
  // The sidebar's unread count (REB-734): the reminders still open for this person. The
  // Avvisi page reads the same query, so a snooze there moves this number at once.
  const unread = useUnreadCount()
  // Below `lg` a 272px sidebar leaves ~118px of page on a 390px phone, so there the
  // sidebar is the rail by default and its expanded form is an overlay over the content.
  // The breakpoint has to be read in JS and not only in CSS because the two are different
  // *structures*, not two widths of one.
  const isDesktop = useMediaQuery(DESKTOP)
  // Local, unpersisted UI state -- collapsing to an icon rail is a per-visit
  // convenience, not a setting worth a round trip or a storage key. Nothing about the
  // shape of the navigation is stored any more: the sections are always open
  // (REB-873), and «Impostazioni» below follows the route until it is toggled.
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
  // The other spaces this identity may reach, and the one being entered right now
  // (disables every row while that request is in flight). Fetched on mount and again
  // every time the menu opens (Greptile, PR #419): AppShell stays mounted for a whole
  // session, so a space this identity gained after mount -- an invitation accepted in
  // another tab -- would otherwise never show without a full reload. The same
  // silent-on-401-or-failure shape the login chooser uses (design §3, REB-345): a
  // stranger, or an identity with no live cookie, simply sees no extra rows here.
  const [spaces, setSpaces] = useState<IdentitySpace[]>([])
  const [enteringSlug, setEnteringSlug] = useState<string | null>(null)
  // A request in flight is tagged with its own id: if a second `refreshSpaces` call
  // (the menu re-opening quickly) starts before the first resolves, an out-of-order
  // response from the first must not overwrite the second's -- CodeRabbit, PR #419.
  const spacesRequestId = useRef(0)
  const refreshSpaces = useCallback(() => {
    const requestId = (spacesRequestId.current += 1)
    void api
      .GET('/api/identity/spaces')
      .then(({ data }) => {
        if (requestId !== spacesRequestId.current) return
        if (Array.isArray(data)) setSpaces(data)
      })
      .catch(() => {})
  }, [])
  useEffect(() => {
    refreshSpaces()
  }, [refreshSpaces])
  // The grandfathered root installation (design §1, 2026-09-08 spec §6) is no registry
  // row: the API lists it under its own `PIGROCRM_ROOT_SLUG` name (REB-583), and it is
  // reachable both unprefixed and under that alias -- `tenantPrefix` alone only ever
  // names the alias, so browsing it unprefixed would otherwise leave the space already
  // open unmatched and listed as "another one".
  // Retried on menu open, same as `refreshSpaces`: a transient failure must not leave
  // `rootSlug` stuck at `null`, which hides the whole switcher, for the rest of the
  // session (Greptile, PR #419).
  const [rootSlug, setRootSlug] = useState<string | null>(null)
  const refreshRootSlug = useCallback(() => {
    void api
      .GET('/api/tenants/root')
      // A response with no `data` (a malformed body, an error the client swallowed)
      // must not overwrite an already-known slug with "no root at all" -- Greptile,
      // PR #419, the same overwrite-on-retry shape as the space list's own guard.
      .then(({ data }) => {
        if (data) setRootSlug(data.slug ?? '')
      })
      .catch(() => {})
  }, [])
  useEffect(() => {
    refreshRootSlug()
  }, [refreshRootSlug])

  // The «Impostazioni» sub-items this reader may see (REB-294): the same
  // `canSeeSettingsTab` the settings page filters its own tabs with, so sidebar and
  // page read one table and cannot drift. The group itself is shown to every role.
  const settingsItems = user === null ? [] : settingsSubItems(user.ruolo, location.pathname)
  // «Impostazioni» is the one entry that opens onto sub-items. It follows the route: the
  // page holding the current route is open by itself, since the alternative is a page whose
  // own entry is not on screen, and the alternative to *that* -- nailing it open -- is a
  // chevron that does nothing. A press settles it only while the route stays on the same
  // side of the settings: it is remembered with the side it was made on, so leaving or
  // entering the settings hands the choice back to the route. What one route did on your
  // behalf is not a preference worth storing, and neither is this.
  const onSettings = isUnder(location.pathname, SETTINGS.base)
  const [settingsToggled, setSettingsToggled] = useState<{ open: boolean; onSettings: boolean } | null>(
    null,
  )
  const settingsOpen =
    settingsToggled !== null && settingsToggled.onSettings === onSettings
      ? settingsToggled.open
      : onSettings

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

  // `tenantPrefix` is `/<slug>` or `''`; at `''` the space already open is the root's
  // own slug when this installation has one (see the effect above), never "no slug".
  // While that answer has not come back yet, `currentSlug` is `null` rather than a
  // guess: the switcher shows nothing sooner than it shows the wrong thing (CodeRabbit,
  // PR #419), and settles once the root slug resolves, even to "no root at all" (`''`).
  const currentSlug = tenantPrefix === '' ? rootSlug : tenantPrefix.replace(/^\//, '')
  const otherSpaces = currentSlug === null ? [] : spaces.filter((space) => space.slug !== currentSlug)

  /** Opens another space with no second proof (design §3, REB-345): the identity
   *  cookie already is one. A different basepath is a different application
   *  instance, so landing there is a full navigation, the same shape the login
   *  chooser already uses. An invitation (REB-678) goes to its acceptance page
   *  instead: the API mints the token for the address this browser proved. */
  async function onEnterSpace(space: IdentitySpace) {
    const slug = space.slug
    setEnteringSlug(slug)
    try {
      if (space.invito) {
        const door = await unwrap(
          api.POST('/api/identity/invite/{slug}', { params: { path: { slug } } }),
        )
        go(door.url)
        return
      }
      await unwrap(api.POST('/api/identity/enter/{slug}', { params: { path: { slug } } }))
      go(`/${slug}/app/`)
    } catch (error) {
      toast.error(toProblem(error).detail)
      setEnteringSlug(null)
    }
  }

  // The one number of the product that asks for an action (slice 7 §5): how many of my
  // attività are past their deadline, drawn beside «Attività» (REB-873: it sat beside «Oggi»
  // while that was an entry) and only when it is above zero.
  const overdue = useOverdueCount()
  // Whether the space has data yet: «Primi passi» is in the profile menu from then on. The
  // same predicate the Home asks to choose between the start page and the dashboard, so the
  // two cannot disagree about when a space stops being new. `false` only for a space that
  // answered «has work»: while it is unknown the entry is not offered.
  const { spaceEmpty } = useSpaceEmpty()
  // Held once it is known, as the Home holds its own choice: `spaceEmpty` goes back to
  // `null` while any of the four reads refetches (a window focus, a write), and the entry
  // must not blink out of the menu for that. A space does not lose its work.
  const [hasData, setHasData] = useState(false)
  if (spaceEmpty === false && !hasData) setHasData(true)

  /** A leaf entry: an icon and a label expanded, an icon alone in the rail. A `count`
   *  above zero is drawn as a badge after the label, and on the icon's corner in the rail,
   *  where it is also part of the link's accessible name. */
  const leaf = ({
    to,
    label,
    icon: Icon,
    exact = false,
    count = 0,
  }: {
    to: LinkTo
    label: string
    icon: LucideIcon
    exact?: boolean
    count?: number
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
      {to === '/app/activities' && overdue !== undefined && (
        <span
          className={cn(
            'bg-sidebar-primary px-1.5 text-xs font-medium text-sidebar-primary-foreground',
            rail ? 'absolute top-1 right-1' : 'ml-auto',
          )}
        >
          <span aria-hidden="true">{overdue}</span>
          <span className="sr-only">{overdue} in ritardo</span>
        </span>
      )}
      {count > 0 && (
        <>
          {/* Sidebar Watermelon Strong under white, the pair the active tile already uses
              and `tokens.test.ts` measures at AA. In the rail it sits on the icon's corner. */}
          <span
            aria-hidden="true"
            className={cn(
              'bg-sidebar-primary px-1.5 text-xs font-medium tabular-nums text-sidebar-primary-foreground',
              rail ? 'absolute right-1.5 top-0.5 min-w-4 px-1 text-center' : 'ml-auto',
            )}
          >
            {count > 99 ? '99+' : count}
          </span>
          <span className="sr-only">{`, ${count} da leggere`}</span>
        </>
      )}
    </Link>
  )

  /** An entry of a section, the one with a count carrying it. */
  const entry = (item: { to: LinkTo; label: string; icon: LucideIcon }) =>
    leaf(item.to === '/app/avvisi' ? { ...item, count: unread } : item)

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
            {/* The wordmark's accent is the sidebar's own tint of the brand red, not the raw
                --color-watermelon: that one is 4.17:1 on this ground, under AA at this size. */}
            Pigro<span className="text-sidebar-wordmark-accent">CRM</span>
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

        {azienda.pinned ? (
          // A scoped person with one azienda (REB-635): its name where the selector
          // would be, so the lists below still read as «of this azienda», and no
          // control, since there is nothing to choose between.
          <div className="px-3 pb-3">
            <p
              role="note"
              aria-label="Azienda"
              className={cn(
                'flex h-9 items-center gap-2 border border-sidebar-border bg-sidebar-accent/50 px-3 text-sm text-sidebar-foreground/70',
                rail && 'justify-center px-0',
              )}
            >
              <Building2 className="size-4 shrink-0" aria-hidden="true" />
              <span className={cn('flex-1 truncate text-left', rail && 'sr-only')}>
                {azienda.aziende[0]?.nome}
              </span>
            </p>
          </div>
        ) : azienda.several ? (
          <div className="px-3 pb-3">
            <Select
              value={azienda.selected ?? EVERY_AZIENDA}
              onValueChange={(value) => azienda.select(value === EVERY_AZIENDA ? null : value)}
            >
              <SelectTrigger
                aria-label="Azienda"
                className={cn(
                  'w-full rounded-none border-sidebar-border bg-sidebar-accent/50 text-sidebar-foreground/70 shadow-none hover:bg-sidebar-accent hover:text-sidebar-foreground [&_svg:not([class*=text-])]:text-sidebar-foreground/70',
                  FOCUS,
                  rail && 'justify-center px-0 [&>svg:last-child]:hidden',
                )}
              >
                <Building2 className="size-4 shrink-0" aria-hidden="true" />
                <span className={cn('flex-1 truncate text-left', rail && 'sr-only')}>
                  <SelectValue />
                </span>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={EVERY_AZIENDA}>Tutte le aziende</SelectItem>
                {azienda.aziende.map((item) => (
                  <SelectItem key={item.id} value={item.id}>
                    {item.nome}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        ) : null}

        {/* Labelled because a page's own header may render a second <nav> landmark (a
            breadcrumb, a set of tabs), and two unlabelled ones are indistinguishable to a
            screen reader -- and to `getByRole('navigation')`. */}
        <nav
          aria-label="Navigazione principale"
          className="flex min-h-0 flex-1 flex-col overflow-y-auto px-3 pb-3"
        >
          <div className="space-y-1">
            {leaf(HOME)}
            {SECTIONS.map((section) => (
              // A group named by its caption, not a control: nothing opens or closes, so the
              // caption is a plain line of text above its entries. In the rail it is a thin
              // separator, and the group keeps its name for a screen reader either way.
              <div key={section.id} role="group" aria-label={section.caption} className="space-y-1">
                {rail ? (
                  <div aria-hidden="true" className="mx-2 mt-3 mb-2 border-t border-sidebar-border" />
                ) : (
                  <p
                    aria-hidden="true"
                    className="px-3 pt-4 pb-1 text-xs font-medium uppercase tracking-wide text-sidebar-foreground/70"
                  >
                    {section.caption}
                  </p>
                )}
                {section.items.map((item) => entry(item))}
              </div>
            ))}
          </div>

          {/* The foot (REB-873): «Agenti» and «Impostazioni», the two entries that are about
              the account and the space rather than about the work. `mt-auto` keeps them at the
              bottom of the column while the lists above are short; when the window is not tall
              enough the whole navigation scrolls and they scroll with it, never under the
              profile. «Impostazioni» is the one entry that still opens onto sub-items, and in
              the rail it is one link to the first of them. */}
          <div className="mt-auto space-y-1 border-t border-sidebar-border pt-3">
            {leaf(AGENTI)}
            {rail ? (
              // The first section, under the group's own name: in the rail the label is the
              // accessible name, and «Profilo» would say nothing about where it goes.
              // Profilo is open to every role, so every role has this link.
              leaf({ to: SETTINGS_PATHS.profile, label: SETTINGS.label, icon: Settings })
            ) : (
              <NavGroup
                label={SETTINGS.label}
                icon={SETTINGS.icon}
                open={settingsOpen}
                onToggle={() => setSettingsToggled({ open: !settingsOpen, onSettings })}
              >
                {settingsItems.map((item) => subItem(item))}
              </NavGroup>
            )}
          </div>
        </nav>

        {/* The profile, anchored at the bottom, opens a menu: the account's own things --
            who is signed in, the space's settings for an admin, the way out. */}
        <div className="mt-auto border-t border-sidebar-border p-3">
          <DropdownMenu
            onOpenChange={(open) => {
              if (!open) return
              refreshSpaces()
              refreshRootSlug()
            }}
          >
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
              {/* «Profilo», for every role and with no gate on it: a shortcut to the same
                  page as the sidebar's «Personale». One menu item, above the admin's own
                  entry, because it is the one thing here that belongs to the person
                  rather than to the space. */}
              <DropdownMenuItem asChild>
                <Link to="/app/settings/profile">
                  <UserRound className="size-4" />
                  Profilo
                </Link>
              </DropdownMenuItem>
              {/* «Primi passi» (ORB-180, REB-222) used to be a sidebar entry, right under
                  Home. It is the start page of a new space, and the Home already is that
                  page while the space is empty, so the entry is only wanted from the first
                  piece of work on: this is where it goes then, for every role, one click
                  from the profile. `/app/get-started` itself answers as before. */}
              {hasData && (
                <DropdownMenuItem asChild>
                  <Link to="/app/get-started">
                    <Rocket className="size-4" />
                    Primi passi
                  </Link>
                </DropdownMenuItem>
              )}
              {/* «Google Drive», for a collaboratore (REB-457): their Drive is their own
                  credential, as the profile is, and the Drive tab is where every consent
                  outcome lands. Read from the tab table, so a readonly person, whose
                  consent the service refuses, is not offered it. An admin reaches it through
                  the sidebar's «Collegamenti». */}
              {!isAdmin && user !== null && canSeeSettingsTab(user.ruolo, 'drive') && (
                <DropdownMenuItem asChild>
                  <Link to={SETTINGS_PATHS.drive}>
                    <HardDrive className="size-4" />
                    Google Drive
                  </Link>
                </DropdownMenuItem>
              )}
              {isAdmin && (
                <DropdownMenuItem asChild>
                  <Link to="/app/settings/space">
                    <Settings className="size-4" />
                    Impostazioni dello spazio
                  </Link>
                </DropdownMenuItem>
              )}
              <DropdownMenuSeparator />
              {/* Every other space this identity may enter (design §3, REB-345): the
                  same live chooser the login page uses, reachable now without first
                  logging out. */}
              {otherSpaces.length > 0 &&
                otherSpaces.map((space) => (
                  <DropdownMenuItem
                    key={space.slug}
                    disabled={enteringSlug !== null}
                    onSelect={() => void onEnterSpace(space)}
                  >
                    {space.invito ? (
                      <MailPlus className="size-4" />
                    ) : (
                      <ArrowLeftRight className="size-4" />
                    )}
                    <span className="flex-1 truncate">{space.slug}</span>
                    <span className="text-xs text-muted-foreground">
                      {space.invito ? 'Invito' : roleLabel(space.ruolo)}
                    </span>
                  </DropdownMenuItem>
                ))}
              <DropdownMenuItem
                onSelect={() => {
                  if (user) writeRegisterHandoffEmail(user.email)
                  go('/app/register')
                }}
              >
                <Plus className="size-4" />
                Crea un nuovo spazio
              </DropdownMenuItem>
              <DropdownMenuSeparator />
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
          pagina e senza margini»). `<main>` is the one scroller of the application: the
          root's `overflow-hidden` clips anything in normal flow to it, and the pages
          carry their own padding (`PageHeader`'s `px-8`). Below `lg` nothing changes:
          the inset was already zero there.

          `relative` gives `<main>` its own containing block (REB-418): without one, a
          plain `position: absolute` descendant with no explicit `top` -- the `sr-only`
          "Fatto: "/"Da fare: " prefix `GetStartedPage` puts on every step of «Primi
          passi», and the next one nobody notices until a page is long enough to expose
          it -- is *not* in that normal flow, so the root's `overflow-hidden` does not
          reach it: it falls back to its static position against the *initial*
          containing block instead, escaping this `overflow-y-auto` box entirely and
          inflating `<html>`'s real height, which drags the sidebar into the document's
          own scroll. Confirmed live on the get-started page, whose four steps are long
          enough to expose it. */}
      <main className="relative min-w-0 flex-1 overflow-y-auto bg-card">{children}</main>

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
function subItem({ to, label, active }: { to: LinkTo; label: string; active?: boolean }) {
  return (
    <li key={to}>
      <Link
        to={to}
        // As on the leaves above: a list that puts its filter in the URL keeps its own
        // entry marked.
        activeOptions={{ includeSearch: false }}
        activeProps={{ 'aria-current': 'page' }}
        // A sub-item that stands for several routes (a settings group) says itself when
        // it is current: the link's own match covers only its first section.
        {...(active ? { 'aria-current': 'page' as const, 'data-status': 'active' } : {})}
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
