import type { PreferenceType } from './preferences'

/**
 * The sections the settings list the types in, in this order: the one table. A type's group
 * is the first section that names its key; a type that no section names (a card adds one and
 * forgets this file) is filed by who receives it, with the space-wide ones under «Spazio»,
 * so a new type is never missing from the page.
 */
const GROUPS: readonly { id: string; label: string; keys: readonly string[] }[] = [
  {
    id: 'attivita',
    label: 'Attività e deal',
    keys: [
      'attivita_due',
      'attivita_overdue',
      'deal_past_close',
      'deal_stale',
      'timer_running_long',
    ],
  },
  {
    id: 'fatture',
    label: 'Fatture e fatturazione',
    keys: [
      'invoice_overdue',
      'invoice_not_transmitted',
      'invoice_rejected_sdi',
      'invoice_days_unlinked',
      'proforma_not_invoiced',
      'hours_unbilled',
      'billable_period_closed',
      'fixed_fee_period_unbilled',
      'on_completion_unbilled',
    ],
  },
  {
    id: 'contratti',
    label: 'Contratti, giornate e approvazioni',
    keys: [
      'contract_expiring',
      'renewal_window_open',
      'worked_without_approval',
      'approval_unanswered',
      'approval_coverage_expiring',
      'approval_requirement_unmet',
      'work_unit_outside_term',
      'register_publication_failed',
    ],
  },
  {
    id: 'spazio',
    label: 'Spazio',
    keys: [
      'ceiling_approaching',
      'year_end_overrun_projected',
      'tax_payment_due',
      'month_not_closed',
      'proposal_pending',
      'connection_failing',
      'job_late',
    ],
  },
]

const OTHER = { id: 'altri', label: 'Altri promemoria' }

function groupOf(kind: PreferenceType) {
  const named = GROUPS.find((group) => group.keys.includes(kind.key))
  if (named) return named
  return kind.audience === 'admins' ? GROUPS[GROUPS.length - 1]! : OTHER
}

/** The types split into their sections, in the table's order, each in the API's order. */
export function groupTypes(types: PreferenceType[]): { id: string; label: string; types: PreferenceType[] }[] {
  const order = [...GROUPS, OTHER]
  return order
    .map((group) => ({
      id: group.id,
      label: group.label,
      types: types.filter((kind) => groupOf(kind).id === group.id),
    }))
    .filter((group) => group.types.length > 0)
}
