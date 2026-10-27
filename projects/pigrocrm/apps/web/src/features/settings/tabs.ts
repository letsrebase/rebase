/**
 * The sections of the settings page and the groups they belong to, in the order shown.
 *
 * Its own module because two places need the same list and neither can own it:
 * `SettingsLayout` renders them as tabs, and `AppShell` renders them as the sub-items of
 * the «Impostazioni» group in the sidebar. Duplicating the labels meant the sidebar could
 * drift from the page silently -- a new tab appearing in one place and not the other --
 * which is exactly the kind of divergence a shared constant makes impossible.
 *
 * A plain `.ts` module rather than a second export from `SettingsLayout.tsx`: a component
 * file that also exports values trips `react-refresh/only-export-components`.
 *
 * The paths are *not* here: `<Link to>` typechecks against the generated route tree, so
 * the sidebar maps each `value` to a literal path of its own (`AppShell`'s
 * `SETTINGS_PATHS`), and adding a tab there is a compile error until the route exists.
 *
 * The sections are grouped as the REB-770 brief says (option A, DECISIONS.md 2026-10-26):
 * the sidebar lists the groups, the page's tab strip lists the sections of the current
 * group, and the URL is always the section's (`/app/settings/<value>`), never the group.
 *
 * Three sections are the exception to "every section here is admin-only": `profile` and
 * `notifications` are a person's own (`ProfilePanel`, `NotificationsPanel`), not something
 * `require_admin` gates, and `SettingsLayout` lets any role open them so the weekly digest's
 * opt-out link, which lands on Notifiche, works for whoever receives the mail (spec
 * 2026-09-16 §3.6); `drive` is open to a collaboratore (REB-457). `SETTINGS_TAB_MIN_ROLE` in
 * `lib/permissions.ts` is the table that says which.
 */
export const SETTINGS_GROUPS = [
  {
    id: 'personale',
    label: 'Personale',
    tabs: [
      { value: 'profile', label: 'Profilo' },
      // A person's own reminder overrides and the weekly digest (REB-735); like Profilo it
      // is every role's, and the digest mail's opt-out link lands here.
      { value: 'notifications', label: 'Notifiche' },
    ],
  },
  {
    id: 'spazio',
    label: 'Spazio e persone',
    tabs: [
      { value: 'space', label: 'Spazio' },
      { value: 'users', label: 'Utenti' },
    ],
  },
  {
    id: 'clienti-deal',
    label: 'Clienti e deal',
    tabs: [
      { value: 'fields', label: 'Campi' },
      { value: 'pipeline', label: 'Pipeline' },
      { value: 'template', label: 'Template' },
    ],
  },
  {
    id: 'fatturazione',
    label: 'Fatturazione e costi',
    tabs: [
      { value: 'aziende', label: 'Aziende' },
      { value: 'cost-categories', label: 'Categorie costo' },
      { value: 'rates', label: 'Tariffe' },
      { value: 'periods', label: 'Periodi' },
    ],
  },
  {
    id: 'collegamenti',
    label: 'Collegamenti',
    tabs: [
      { value: 'gmail', label: 'Gmail' },
      { value: 'drive', label: 'Google Drive' },
    ],
  },
  {
    id: 'promemoria',
    label: 'Promemoria e automazioni',
    tabs: [
      // The space's reminder defaults (REB-735), admin only like every section but the two
      // personal ones.
      { value: 'reminders', label: 'Promemoria' },
      { value: 'automations', label: 'Automazioni' },
      // What the cron commands last did in this space (REB-764): admin only.
      { value: 'jobs', label: 'Esecuzioni' },
    ],
  },
] as const

export type SettingsGroup = (typeof SETTINGS_GROUPS)[number]

/** Every section, flattened in group order: the list the permissions table is keyed by. */
export const SETTINGS_TABS: ReadonlyArray<{ value: SettingsTabValue; label: string }> =
  SETTINGS_GROUPS.flatMap((group) => [...group.tabs])

export type SettingsTabValue = SettingsGroup['tabs'][number]['value']

/** The group a section belongs to, or `undefined` for a path that is no section. */
export function settingsGroupOf(value: string | undefined): SettingsGroup | undefined {
  return SETTINGS_GROUPS.find((group) => group.tabs.some((tab) => tab.value === value))
}
