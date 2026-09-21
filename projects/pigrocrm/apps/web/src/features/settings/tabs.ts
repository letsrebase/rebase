/**
 * The tabs of the settings page, in the order they are shown.
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
 * `profilo` first, and the one exception to "every tab here is admin-only": it is a
 * person's own preferences (`ProfilePanel`), not something `require_admin` gates
 * anywhere, and `SettingsLayout` exempts exactly this one value from the page's
 * otherwise-blanket admin gate so the weekly digest's opt-out link works for whoever
 * receives the mail (spec 2026-09-16 §3.6).
 */
export const SETTINGS_TABS = [
  { value: 'profile', label: 'Profilo' },
  { value: 'space', label: 'Spazio' },
  { value: 'fields', label: 'Campi' },
  { value: 'pipeline', label: 'Pipeline' },
  { value: 'template', label: 'Template' },
  { value: 'issuer', label: 'Emittente' },
  { value: 'fiscal', label: 'Fiscale' },
  { value: 'users', label: 'Utenti' },
  { value: 'cost-categories', label: 'Categorie costo' },
  { value: 'rates', label: 'Tariffe' },
  { value: 'periods', label: 'Periodi' },
  { value: 'gmail', label: 'Gmail' },
  { value: 'drive', label: 'Google Drive' },
  { value: 'automations', label: 'Automazioni' },
] as const

export type SettingsTabValue = (typeof SETTINGS_TABS)[number]['value']
