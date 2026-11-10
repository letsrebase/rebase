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
 * `collaborator`, a `require_write` string not as `admin`). Actions the SPA gates by
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
export type Role = SessionUser['role']

/**
 * The role lattice, ordered the way the services order it: a `collaborator` may do
 * everything a `readonly` may and more, an `admin` everything a `collaborator` may and
 * more. Every check below is `RANK[role] >= RANK[minimum]`, which is what keeps the
 * table one column instead of three.
 */
const RANK: Record<Role, 0 | 1 | 2> = { readonly: 0, collaborator: 1, admin: 2 }

/**
 * The minimum role for each action, read off the services in `packages/core` (every
 * string below is what those services pass to `require_write`/`require_admin`).
 * `readonly` never appears as a minimum: a readonly actor may press no control in here,
 * which is the card's own rule, not an omission.
 *
 * Every key is the English action key the service passes; the Italian sentence a person
 * reads is a label in core's `ACTION_LABELS`. The table mirrors the source exactly.
 */
export const MIN_ROLE = {
  // Vendite
  create_organization: 'collaborator',
  update_organization: 'collaborator',
  delete_organization: 'collaborator',
  // Who answers for a customer, and so who the reminders about it go to (REB-773).
  change_customer_owner: 'admin',
  create_person: 'collaborator',
  update_person: 'collaborator',
  delete_person: 'collaborator',
  create_deal: 'collaborator',
  update_deal: 'collaborator',
  delete_deal: 'collaborator',
  move_deal: 'collaborator',
  // The notes thread of a deal, a customer or a person (REB-879): `require_write` in
  // `NoteService`. Whose note may be changed is the row's own `editable`, not a role.
  add_note: 'collaborator',
  update_note: 'collaborator',
  delete_note: 'collaborator',
  set_offer_state: 'collaborator',
  // Amministrazione: documents
  create_document: 'collaborator',
  update_document: 'collaborator',
  delete_document: 'collaborator',
  add_document_version: 'collaborator',
  import_document: 'collaborator',
  regenerate_document: 'collaborator',
  create_document_from_template: 'collaborator',
  // Amministrazione: hours, purchases, calendar
  log_time: 'collaborator',
  update_time_entry: 'collaborator',
  delete_time_entry: 'collaborator',
  start_timer: 'collaborator',
  stop_timer: 'collaborator',
  discard_timer: 'collaborator',
  create_purchase: 'collaborator',
  update_purchase: 'collaborator',
  cancel_purchase: 'collaborator',
  delete_purchase: 'collaborator',
  restore_purchase: 'collaborator',
  create_recurring_expense: 'collaborator',
  stop_recurring_expense: 'collaborator',
  create_work_unit: 'collaborator',
  transition_work_unit: 'collaborator',
  create_approval: 'collaborator',
  request_approval: 'collaborator',
  create_task: 'collaborator',
  update_task: 'collaborator',
  complete_task: 'collaborator',
  cancel_task: 'collaborator',
  archive_task: 'collaborator',
  // Amministrazione: invoices (the document cycle, in its order)
  create_invoice: 'collaborator',
  update_invoice: 'collaborator',
  replace_invoice_lines: 'collaborator',
  confirm_proforma: 'collaborator',
  delete_invoice: 'collaborator',
  set_payment_state: 'collaborator',
  // Due items (REB-967): a client's payment recorded or undone is a collaborator's; money
  // going out, and every item whose source is a tax, IVA, withholding or payroll, an
  // administrator's. The service picks the row from the item, so a control on an outgoing
  // item consults the second pair.
  settle_due_item: 'collaborator',
  reverse_settlement: 'collaborator',
  settle_outgoing_due_item: 'admin',
  reverse_outgoing_settlement: 'admin',
  // Staff and their pay (REB-977, REB-1008, REB-1009): every read and write is an
  // administrator's, and the service refuses anyone else before it looks anything up.
  list_staff: 'admin',
  get_staff_member: 'admin',
  create_staff_member: 'admin',
  update_staff_member: 'admin',
  set_payroll_pay_day: 'admin',
  get_payroll_month: 'admin',
  record_staff_payslip: 'admin',
  correct_staff_payslip: 'admin',
  propose_staff_hourly_cost: 'admin',
  adopt_staff_hourly_cost: 'admin',
  // Engagements with freelancers (REB-976): every read and write is an administrator's, as the
  // hub's matches were. «Incarichi», the engagement page and the registry's block consult them
  // (REB-1035); `read_rebase_engagement` is the freelancer's own card, read in their own space
  // by the administrator of it.
  read_engagements: 'admin',
  create_engagement: 'admin',
  update_engagement: 'admin',
  cancel_engagement: 'admin',
  close_engagement: 'admin',
  send_engagement_for_signature: 'admin',
  resend_signing_mail: 'admin',
  refresh_document_signature: 'admin',
  cancel_framework_agreement: 'admin',
  record_framework_notice: 'admin',
  upload_signed_document: 'admin',
  activate_engagement: 'admin',
  read_engagement_report: 'admin',
  record_monthly_report: 'admin',
  approve_monthly_report: 'admin',
  read_rebase_engagement: 'admin',
  get_freelancer_availability: 'admin',
  set_freelancer_tax_data: 'admin',
  set_organization_hub_freelancer: 'admin',
  produce_invoice_artifacts: 'collaborator',
  export_invoice_xml: 'collaborator',
  // The irreversible steps and the money parameters, admin-only in the service
  // (`issue_invoice`, `annul_invoice`, `mark_transmitted_externally`,
  // `bind_time_to_invoice`) or admin-only across the whole feature (rates, locks).
  issue_invoice: 'admin',
  annul_invoice: 'admin',
  mark_transmitted_externally: 'admin',
  bind_time_to_invoice: 'admin',
  bill_contract_days: 'admin',
  // The draft of a contract's recurring fee (REB-1032), the same rung as the days'.
  bill_contract_fee: 'admin',
  // Registering an invoice issued elsewhere: the start page's invoice door (REB-224) is
  // the admin's, because the import its handoff asks the assistant for is.
  import_issued_invoice: 'admin',
  // «Importa» on the Fatture page (REB-758): the review reads and the confirmation writes
  // the register, both admin in the service, so the button asks for the write.
  review_invoice_import: 'admin',
  confirm_invoice_import: 'admin',
  update_user_rates: 'admin',
  update_deal_rate: 'admin',
  close_period: 'admin',
  reopen_period: 'admin',
  // Contratti: the page's own writes (REB-741..744). `require_write` in the services.
  create_contract: 'collaborator',
  render_contract_register: 'collaborator',
  create_contract_email_template: 'collaborator',
  update_contract_email_template: 'collaborator',
  prepare_contract_email: 'collaborator',
  update_contract: 'collaborator',
  set_contract_state: 'collaborator',
  create_rate_card: 'collaborator',
  update_rate_card: 'collaborator',
  create_contract_expense: 'collaborator',
  update_contract_expense: 'collaborator',
  create_clause_note: 'collaborator',
  update_clause_note: 'collaborator',
  set_renewal_assumption: 'collaborator',
  renew_contract: 'collaborator',
  // The Email tab: the three presses on a draft the assistant prepared (REB-415).
  send_email: 'collaborator',
  reconcile_email: 'collaborator',
  delete_email_draft: 'collaborator',
  // «Cerca chi ha scritto» on a customer's Email tab (REB-766): the read spends the
  // owner's Gmail quota, so the service guards it like a write.
  discover_gmail_correspondents: 'collaborator',
  // Impostazioni: the write behind each tab, guarded with `require_write`. What a
  // readonly person never reaches there is decided by the tab's visibility, not by
  // these rows -- see `SETTINGS_TAB_MIN_ROLE` below.
  update_gmail_settings: 'collaborator',
  set_drive_roots: 'collaborator',
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
  upsert_legal_entities: 'admin',
  update_fiscal_profile: 'admin',
  get_fiscal_estimate: 'admin',
  // The tax calendar (REB-1004) is the same fiscal position as the estimate: an administrator's.
  get_tax_calendar: 'admin',
  // The accountant's period export (REB-1028): every fiscal figure of a company, so the page, its
  // entry in the menu and the download are an administrator's, as the core guards them.
  build_accountant_export: 'admin',
  get_export_download_url: 'admin',
  // The IVA periods and the registers behind them (REB-1024), the same position again.
  list_iva_periods: 'admin',
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
} as const satisfies Record<string, 'collaborator' | 'admin'>

/** Every action the table names. A typo in a `useCan('…')` call is a type error. */
export type Action = keyof typeof MIN_ROLE

/**
 * Whether `role` may perform `action`. An action with no row is not role-gated, so it
 * answers true for every role: the table only contains the things the server gates.
 */
export function can(role: Role, action: Action | (string & {})): boolean {
  const minimum: 'collaborator' | 'admin' | undefined = MIN_ROLE[action as Action]
  return minimum === undefined || RANK[role] >= RANK[minimum]
}

/**
 * The coarse checks, same lattice as `can`: what a whole page gates on when it offers
 * several controls at once ("chi può scrivere nello spazio", "amministratore"). They
 * read the same rank the table's minimums are drawn from, so the table stays the one
 * source for the named checks and these alike.
 */
export function canWrite(role: Role): boolean {
  return RANK[role] >= RANK.collaborator
}

export function isAdmin(role: Role): boolean {
  return RANK[role] >= RANK.admin
}

/**
 * The settings tabs, each with the minimum role that may *see* it in the tab strip and
 * in the sidebar; `null` where the tab is nobody's row because it is the person's own
 * preferences (spec 2026-09-16 §3.6), open to every role. `profile` is therefore the
 * one tab a readonly keeps.
 *
 * `drive` is the one tab at `collaborator` (REB-457). The Drive credential belongs to
 * one CRM user (`google_drive_accounts.user_id` is unique), `GoogleDriveOAuthService`
 * lets any writer connect, disconnect and configure their own, and every consent
 * outcome lands on this tab. So a collaboratore sees it and manages their own Drive
 * there; a readonly person, whose consent the service refuses, does not. The one
 * installation-wide field on it, the space's write folder, stays an admin's inside the
 * panel (`DrivePanel`'s own docstring says why).
 *
 * Every other tab's services gate their writes on `require_admin`, which is why every
 * other minimum here is `admin`.
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
  'collaborator' | 'admin' | null
> = {
  profile: null,
  notifications: null,
  space: 'admin',
  modules: 'admin',
  fields: 'admin',
  pipeline: 'admin',
  templates: 'admin',
  'legal-entities': 'admin',
  users: 'admin',
  'cost-categories': 'admin',
  rates: 'admin',
  periods: 'admin',
  gmail: 'admin',
  drive: 'collaborator',
  slack: 'admin',
  'ai-model': 'admin',
  reminders: 'admin',
  automations: 'admin',
  jobs: 'admin',
}

/** Whether `role` may see the settings tab `tab`: the same reading as `can`, against
 *  the tab table. This is the shell's and the settings page's one source; neither
 *  keeps a private list any more. */
export function canSeeSettingsTab(role: Role, tab: SettingsTabValue): boolean {
  const minimum = SETTINGS_TAB_MIN_ROLE[tab]
  return minimum === null || RANK[role] >= RANK[minimum]
}
