import {
  Bell,
  Bot,
  Building2,
  CalendarCheck,
  CalendarDays,
  Clock,
  Handshake,
  Inbox,
  LayoutDashboard,
  ListChecks,
  Receipt,
  Settings,
  Users,
} from 'lucide-react'
import { SETTINGS_GROUPS, type SettingsTabValue } from '@/features/settings/tabs'
import { canSeeSettingsTab, type Role } from '@/lib/permissions'

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
 * the reminders still unread (REB-734) and the proposals an agent left for a decision
 * (REB-750). «Amministrazione» reads in the order the money is supposed to move: the
 * register, the hours the next invoice is built from, the contract days (REB-745). None of
 * it is admin-gated: every write behind these pages is gated at the service, where the
 * refusal belongs. The Payment reminders page left the menu on 2026-09-09 at Ivan's request; its
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
