import type { SessionUser } from './auth'
import type { SettingsTabValue } from '@/features/settings/tabs'

/**
 * What each role may *do* in the interface, in one table.
 *
 * REB-294: the SPA used to show every action to every role and let the API answer 403,
 * and the interface turned that refusal into a red stack nobody could read. This table
 * is the one answer to "may this role press this control"; the server remains the one
 * that decides. Every key below is a service's own `require_write`/`require_admin`
 * string verbatim, and `test/permissions.test.ts` pins the table against the core's
 * source: every key must name an action the core really guards, and the guard's kind
 * must match the table's minimum (a `require_admin` string cannot be listed as
 * `collaboratore`, a `require_write` string not as `admin`). Actions the SPA gates by
 * the coarse checks or does not offer a control for (restores, creating or editing an
 * email draft, the timer rename) are deliberately not rows: the table lists what a
 * named control consults.
 *
 * A control the server gates by identity rather than role (editing one's own profile,
 * minting one's own token, connecting one's own agent) has no row here at all: absence
 * of a row means "not role-gated", not "forbidden".
 *
 * Its own module and not `lib/auth.tsx`, for the reason documented there: component
 * tests mock `@/lib/auth` wholesale, and a constant living there would come back
 * undefined in all of them.
 */
export type Role = SessionUser['ruolo']

/**
 * The role lattice, ordered the way the services order it: a `collaboratore` may do
 * everything a `readonly` may and more, an `admin` everything a `collaboratore` may and
 * more. Every check below is `RANK[role] >= RANK[minimum]`, which is what keeps the
 * table one column instead of three.
 */
const RANK: Record<Role, 0 | 1 | 2> = { readonly: 0, collaboratore: 1, admin: 2 }

/**
 * The minimum role for each action, read off the services in `packages/core` (every
 * string below is what those services pass to `require_write`/`require_admin`).
 * `readonly` never appears as a minimum: a readonly actor may press no control in here,
 * which is the card's own rule, not an omission.
 *
 * The Gmail and Drive strings are the Italian ones because those services build the
 * sentence themselves and surface the action inside it; every other key is the English
 * action string. The table mirrors the source exactly, in either language.
 */
export const MIN_ROLE = {
  // Vendite
  create_customer: 'collaboratore',
  update_customer: 'collaboratore',
  delete_customer: 'collaboratore',
  create_person: 'collaboratore',
  update_person: 'collaboratore',
  delete_person: 'collaboratore',
  create_deal: 'collaboratore',
  update_deal: 'collaboratore',
  delete_deal: 'collaboratore',
  move_deal: 'collaboratore',
  set_offer_state: 'collaboratore',
  // Amministrazione: documents
  create_document: 'collaboratore',
  update_document: 'collaboratore',
  delete_document: 'collaboratore',
  add_document_version: 'collaboratore',
  import_document: 'collaboratore',
  regenerate_document: 'collaboratore',
  create_document_from_template: 'collaboratore',
  // Amministrazione: hours, costs, calendar
  log_time: 'collaboratore',
  update_time_entry: 'collaboratore',
  delete_time_entry: 'collaboratore',
  start_timer: 'collaboratore',
  stop_timer: 'collaboratore',
  discard_timer: 'collaboratore',
  create_cost: 'collaboratore',
  update_cost: 'collaboratore',
  delete_cost: 'collaboratore',
  create_attivita: 'collaboratore',
  update_attivita: 'collaboratore',
  complete_attivita: 'collaboratore',
  cancel_attivita: 'collaboratore',
  archive_attivita: 'collaboratore',
  // Amministrazione: invoices (the document cycle, in its order)
  create_invoice: 'collaboratore',
  update_invoice: 'collaboratore',
  replace_invoice_lines: 'collaboratore',
  confirm_proforma: 'collaboratore',
  delete_invoice: 'collaboratore',
  set_payment_state: 'collaboratore',
  produce_invoice_artifacts: 'collaboratore',
  export_invoice_xml: 'collaboratore',
  // The irreversible steps and the money parameters, admin-only in the service
  // (`issue_invoice`, `annul_invoice`, `mark_transmitted_externally`,
  // `bind_time_to_invoice`) or admin-only across the whole feature (rates, locks).
  issue_invoice: 'admin',
  annul_invoice: 'admin',
  mark_transmitted_externally: 'admin',
  bind_time_to_invoice: 'admin',
  // Registering an invoice issued elsewhere: the start page's invoice door (REB-224) is
  // the admin's, because the import its handoff asks the assistant for is.
  import_issued_invoice: 'admin',
  update_user_rates: 'admin',
  update_deal_rate: 'admin',
  close_period: 'admin',
  reopen_period: 'admin',
  // The Email tab: the three presses on a draft the assistant prepared (REB-415). The
  // send and its verification are Italian strings, like the Gmail and Drive settings
  // below, because `gmail/send.py` builds its sentence around them.
  "inviare un'email": 'collaboratore',
  "verificare l'esito di un'email": 'collaboratore',
  delete_email_draft: 'collaboratore',
  // Impostazioni: the write behind each tab. The Gmail and Drive strings are Italian
  // because those services build the sentence themselves and surface the action
  // inside it; they guard with `require_write`, and the table says so. What a
  // readonly person never reaches there is decided by the tab's visibility, not by
  // these rows -- see `SETTINGS_TAB_MIN_ROLE` below.
  'modificare le impostazioni Gmail': 'collaboratore',
  'impostare le cartelle Drive': 'collaboratore',
  update_automation_config: 'admin',
  create_field_definition: 'admin',
  update_field_definition: 'admin',
  archive_field_definition: 'admin',
  unarchive_field_definition: 'admin',
  create_pipeline_stage: 'admin',
  update_pipeline_stage: 'admin',
  delete_pipeline_stage: 'admin',
  seed_pipeline: 'admin',
  create_template: 'admin',
  update_template: 'admin',
  activate_template: 'admin',
  deactivate_template: 'admin',
  upsert_emitter_profile: 'admin',
  update_fiscal_profile: 'admin',
  get_fiscal_estimate: 'admin',
  create_cost_category: 'admin',
  update_cost_category: 'admin',
  archive_cost_category: 'admin',
  unarchive_cost_category: 'admin',
  read_space_settings: 'admin',
  update_space_settings: 'admin',
  create_user: 'admin',
  update_user: 'admin',
  invite_user: 'admin',
  list_users: 'admin',
  list_invites: 'admin',
  resend_invite: 'admin',
  revoke_invite: 'admin',
  reset_password: 'admin',
} as const satisfies Record<string, 'collaboratore' | 'admin'>

/** Every action the table names. A typo in a `useCan('…')` call is a type error. */
export type Action = keyof typeof MIN_ROLE

/**
 * Whether `role` may perform `action`. An action with no row is not role-gated, so it
 * answers true for every role: the table only contains the things the server gates.
 */
export function can(role: Role, action: Action | (string & {})): boolean {
  const minimum: 'collaboratore' | 'admin' | undefined = MIN_ROLE[action as Action]
  return minimum === undefined || RANK[role] >= RANK[minimum]
}

/**
 * The coarse checks, same lattice as `can`: what a whole page gates on when it offers
 * several controls at once ("chi può scrivere nello spazio", "amministratore"). They
 * read the same rank the table's minimums are drawn from, so the table stays the one
 * source for the named checks and these alike.
 */
export function canWrite(role: Role): boolean {
  return RANK[role] >= RANK.collaboratore
}

export function isAdmin(role: Role): boolean {
  return RANK[role] >= RANK.admin
}

/**
 * The settings tabs, each with the minimum role that may *see* it in the tab strip and
 * in the sidebar; `null` where the tab is nobody's row because it is the person's own
 * preferences (spec 2026-09-16 §3.6), open to every role. `profile` is therefore the
 * one tab a collaboratore or a readonly keeps; every other tab's services gate their
 * writes on `require_admin`, which is why every other minimum here is `admin`.
 *
 * Exhaustive over `SettingsTabValue` on purpose: adding a tab to `SETTINGS_TABS`
 * without deciding its visibility is a compile error.
 *
 * The Home fiscal card is deliberately *not* governed by any of this: the estimate it
 * reads is `get_fiscal_estimate`, an admin-only *read*, and the standing record for the
 * card (`features/dashboard/FiscalPanel`'s docstring) says it stays visible to every
 * role and explains itself. What REB-294 changes there is the explanation, not the
 * visibility: a 403 still arrives on Home for a non-admin, and it now reads as an
 * Italian sentence naming the role (`toProblem`, `lib/api.ts`), not as a stack of red.
 */
export const SETTINGS_TAB_MIN_ROLE: Record<
  SettingsTabValue,
  'collaboratore' | 'admin' | null
> = {
  profile: null,
  space: 'admin',
  fields: 'admin',
  pipeline: 'admin',
  template: 'admin',
  issuer: 'admin',
  fiscal: 'admin',
  users: 'admin',
  'cost-categories': 'admin',
  rates: 'admin',
  periods: 'admin',
  gmail: 'admin',
  drive: 'admin',
  automations: 'admin',
}

/** Whether `role` may see the settings tab `tab`: the same reading as `can`, against
 *  the tab table. This is the shell's and the settings page's one source; neither
 *  keeps a private list any more. */
export function canSeeSettingsTab(role: Role, tab: SettingsTabValue): boolean {
  const minimum = SETTINGS_TAB_MIN_ROLE[tab]
  return minimum === null || RANK[role] >= RANK[minimum]
}
