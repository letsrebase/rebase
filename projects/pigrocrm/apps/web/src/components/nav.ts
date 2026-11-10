import {
  Bell,
  Bot,
  Building2,
  CalendarCheck,
  CalendarDays,
  Clock,
  FileInput,
  Handshake,
  IdCard,
  Inbox,
  Landmark,
  LayoutDashboard,
  ListChecks,
  PackageOpen,
  Receipt,
  Settings,
  ShoppingCart,
  Users,
  type LucideIcon,
} from 'lucide-react'
import { SETTINGS_GROUPS, type SettingsTabValue } from '@/features/settings/tabs'
import { modulesOfPath, type ModuleAccess, type ModuleKey } from '@/lib/modules'
import { can, canSeeSettingsTab, type Action, type Role } from '@/lib/permissions'

/**
 * The sidebar's navigation tables: what it links to, in the order of the brief Lorenzo
 * approved on 2026-11-02 (sidebar A, REB-873). `Sidebar` draws them; nothing here renders.
 */

export const HOME = { to: '/app', label: 'Home', icon: LayoutDashboard, exact: true } as const

/**
 * The three sections, each a caption over its entries. The entries carry an icon even
 * though the expanded rendering shows it too: the icon is what the collapsed rail draws,
 * where the caption becomes a separator.
 *
 * «Da fare» is what asks for a person's attention, in the order they reach for it: the
 * tasks (late ones counted beside it), the calendar that draws the dated ones by day,
 * the reminders still unread (REB-734), the files that arrived and wait for a person
 * («In arrivo», REB-984) and the proposals an agent left for a decision (REB-750). «Amministrazione» reads in the order the money is supposed to move: the
 * register, the hours the next invoice is built from, the contract days (REB-745), then the taxes (REB-1026), which every role sees and only an administrator reads, and last
 * «Esporta per il commercialista» (REB-1028), which is the one entry only an administrator sees: it
 * names the action that decides (`action`), pinned against the core by `lib/permissions.ts`, and
 * the filter below reads that table. Every other entry is open to every role, and every write
 * behind those pages is gated at the service, where the refusal belongs. The Payment reminders page left the menu on 2026-09-09 at Ivan's request; its
 * API and MCP tools remain.
 */
export const SECTIONS = [
  {
    id: 'da-fare',
    caption: 'Da fare',
    items: [
      { to: '/app/activities', label: 'Attività', icon: ListChecks },
      { to: '/app/calendar', label: 'Calendario', icon: CalendarDays },
      { to: '/app/reminders', label: 'Avvisi', icon: Bell },
      { to: '/app/inbox', label: 'In arrivo', icon: FileInput },
      { to: '/app/proposals', label: 'Proposte', icon: Inbox },
    ],
  },
  {
    id: 'vendite',
    caption: 'Vendite',
    items: [
      { id: 'customers', to: '/app/organizations', label: 'Clienti', icon: Building2 },
      { to: '/app/people', label: 'Persone', icon: Users },
      { to: '/app/deal', label: 'Deal', icon: Handshake },
    ],
  },
  {
    id: 'amministrazione',
    caption: 'Amministrazione',
    items: [
      { to: '/app/invoices', label: 'Fatture', icon: Receipt },
      // The registry's supplier view (the approved brief, 2026-11-10): one page with two doors,
      // «Clienti» in «Vendite» and «Fornitori» here, beside the purchases they are paid for.
      // The page is the core's, so the module is named on the entry and not read from the path.
      {
        id: 'suppliers',
        to: '/app/organizations',
        search: { role: 'supplier' },
        modules: ['purchases'],
        label: 'Fornitori',
        icon: Building2,
      },
      { to: '/app/purchases', label: 'Acquisti', icon: ShoppingCart },
      { to: '/app/hours', label: 'Ore', icon: Clock },
      { to: '/app/days', label: 'Giornate', icon: CalendarCheck },
      { to: '/app/taxes', label: 'Tasse', icon: Landmark },
      // The people the company pays (the approved brief of 2026-11-10, REB-1027): pay is an
      // administrator's, so the entry is theirs alone, as the service is.
      { to: '/app/staff', action: 'list_staff', label: "Persone dell'azienda", icon: IdCard },
      // The archive of a period for the accountant (REB-1028): an administrator's, in the sidebar
      // and in «Altro». It belongs to no module, since the registers it reads are the core's.
      {
        to: '/app/export',
        action: 'build_accountant_export',
        label: 'Esporta per il commercialista',
        icon: PackageOpen,
      },
    ],
  },
] as const

/** One entry of a section: `to` and `search` are where it links, `modules` overrides the
 *  modules the path alone would need (an entry that opens a core page on a module's view),
 *  `action` hides it from a role that may not do it, and `id` tells apart two entries that open the same page. */
export interface NavItem {
  readonly id?: string
  readonly to: LinkTo
  readonly search?: { readonly role: 'supplier' }
  readonly modules?: readonly ModuleKey[]
  /** The action that decides who sees the entry, a row of `lib/permissions.ts`'s table. An entry
   *  with none is open to every role. */
  readonly action?: Action
  readonly label: string
  readonly icon: LucideIcon
}

/** The registry's page, which two entries open (`?role=` tells them apart). */
export const REGISTRY_PATH = '/app/organizations'

/** What tells one entry from another: its `id`, else its path. */
export const navKey = (item: NavItem) => item.id ?? item.to

/**
 * Whether an entry is the one the reader is on. The registry page serves two entries, so the
 * path is not enough there: «Fornitori» is the page on `?role=supplier` and «Clienti» the same
 * page on any other view (and a customer's own page), the way the sidebar always read it.
 */
export function isNavItemActive(item: NavItem, pathname: string, role: string | undefined) {
  if (!isUnder(pathname, item.to)) return false
  if (item.to !== REGISTRY_PATH) return true
  const onSuppliers = pathname === item.to && role === 'supplier'
  return item.search?.role === 'supplier' ? onSuppliers : !onSuppliers
}

/**
 * The sections as one space sees them (REB-953, spec 2026-11-09 B4): an entry whose module
 * is off is gone, and a section left with no entry goes with it. Which module owns which
 * entry is not written here: it is `ROUTE_MODULES`, the table the route guard reads too,
 * so a link and the page it leads to cannot disagree. An entry that names an `action` is gone
 * for a role that may not do it (REB-1028); while the role is not known, it is gone too.
 */
export function visibleSections(access: ModuleAccess, role: Role | undefined) {
  return SECTIONS.map((section) => ({
    ...section,
    items: (section.items as readonly NavItem[]).filter(
      (item) =>
        access.on(...(item.modules ?? modulesOfPath(item.to))) &&
        (item.action === undefined || (role !== undefined && can(role, item.action))),
    ),
  })).filter((section) => section.items.length > 0)
}

/**
 * The phone's bar (spec 2026-11-09 B6, the approved `pigrocrm-phone-shell` brief, REB-959):
 * «Home», «Attività», a third place, «+» in the centre and «Altro». The third place is the
 * first switched-on of «Deal» and «Giornate», the profile deciding the order: a `solo` space
 * works by the day, a `company` by the deal. With both off the bar has four places.
 */
const PHONE_DEALS = { to: '/app/deal', label: 'Deal', icon: Handshake } as const
const PHONE_DAYS = { to: '/app/days', label: 'Giornate', icon: CalendarCheck } as const

export function phoneThirdPlace(access: ModuleAccess): typeof PHONE_DEALS | typeof PHONE_DAYS | null {
  const order = access.profile === 'solo' ? [PHONE_DAYS, PHONE_DEALS] : [PHONE_DEALS, PHONE_DAYS]
  return order.find((place) => access.on(...modulesOfPath(place.to))) ?? null
}

/**
 * What «Altro» lists: every place the space has on that the bar itself does not draw. The
 * same `visibleSections` the sidebar reads, so a module that is off is gone from both.
 */
export function phoneMoreItems(access: ModuleAccess, role: Role | undefined) {
  const drawn = new Set<string>([HOME.to, '/app/activities', phoneThirdPlace(access)?.to ?? ''])
  const items = visibleSections(access, role).flatMap((section) =>
    section.items.filter((item) => !drawn.has(item.to)),
  )
  // «In arrivo» leads «Altro» (the inbox brief, REB-973): the phone has no sidebar to carry its
  // count, so it is the first thing in the one place the count is drawn.
  return [...items.filter((item) => item.to === INBOX_PATH), ...items.filter((item) => item.to !== INBOX_PATH)]
}

/** The inbox's page, whose entry carries the count of items waiting. */
export const INBOX_PATH = '/app/inbox'


/**
 * «Agenti», at the foot with «Impostazioni» (REB-873): the personal access tokens and the
 * «Collega un agente» action that mints one. Not one of the settings sub-items: a token
 * belongs to whoever creates it, any role (`PatService` scopes by `actor.id`, not role), so
 * the page is every role's own and its route, `/app/token`, carries no admin guard.
 */
export const Agents = { to: '/app/token', label: 'Agenti', icon: Bot } as const

/**
 * One literal path per settings tab. `satisfies Record<SettingsTabValue, ...>` is what
 * makes this exhaustive: adding a tab to `SETTINGS_TABS` without a route here is a
 * compile error, which is the whole point of keeping the labels in one place and the
 * paths in the place that can typecheck them.
 */
export const SETTINGS_PATHS = {
  profile: '/app/settings/profile',
  notifications: '/app/settings/notifications',
  space: '/app/settings/space',
  modules: '/app/settings/modules',
  fields: '/app/settings/fields',
  pipeline: '/app/settings/pipeline',
  templates: '/app/settings/templates',
  'legal-entities': '/app/settings/legal-entities',
  users: '/app/settings/users',
  'cost-categories': '/app/settings/cost-categories',
  rates: '/app/settings/rates',
  periods: '/app/settings/periods',
  gmail: '/app/settings/gmail',
  drive: '/app/settings/drive',
  slack: '/app/settings/slack',
  'ai-model': '/app/settings/ai-model',
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
 * (`canSeeSettingsTab`), so a member has «Personale» alone and a collaborator adds
 * «Collegamenti» (their own Drive, REB-457). The weekly digest's opt-out link lands on
 * Profile for whoever got the mail (spec 2026-09-16 §3.6).
 */
export const SETTINGS = {
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
export function settingsSubItems(role: Role, pathname: string) {
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
export type LinkTo =
  | typeof HOME.to
  | (typeof SECTIONS)[number]['items'][number]['to']
  | typeof Agents.to
  | (typeof SETTINGS_PATHS)[SettingsTabValue]

/** Is `pathname` this entry, or a page below it (a detail route, a tab)? */
export function isUnder(pathname: string, to: string, exact = false) {
  return exact ? pathname === to : pathname === to || pathname.startsWith(`${to}/`)
}
