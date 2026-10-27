import { linkOptions, type LinkProps } from '@tanstack/react-router'
import { defaultDashboardSearch } from '@/features/dashboard/search'
import type { Reminder } from './fields'

/**
 * Where each kind of record a reminder names is opened: the one table. The API says what
 * a reminder is about in `entity` and `entity_id`, and every card that adds a type adds
 * its row here, nowhere else.
 *
 * `linkOptions` is what keeps a row honest: its `to` and `params` are checked against the
 * generated route tree, so a renamed route fails to compile here and not as a dead link.
 *
 * A day worked has no page of its own, so its reminder names the contract it belongs to.
 * Left out on purpose, because there is no record to open: the space-wide kinds
 * (`google_connection`, `period`). A late job opens Esecuzioni (REB-764), the one place
 * that says which job it is and what it last did. `fiscal` opens the home, where the year's
 * ceilings are. A reminder whose `entity` is not in the table is shown with no link
 * rather than with a guessed one.
 */
const RECORD_LINKS: Record<string, (id: string) => LinkProps> = {
  // An attività has no page of its own: the list opens it in a dialog (`?apri=`).
  attivita: (id: string) => linkOptions({ to: '/app/activities', search: { apri: id } }),
  deal: (id: string) => linkOptions({ to: '/app/deal/$dealId', params: { dealId: id } }),
  customer: (id: string) =>
    linkOptions({ to: '/app/customers/$customerId', params: { customerId: id } }),
  // A proforma is an invoice row of type `proforma`, opened by the same page.
  invoice: (id: string) =>
    linkOptions({ to: '/app/invoices/$invoiceId', params: { invoiceId: id } }),
  proforma: (id: string) =>
    linkOptions({ to: '/app/invoices/$invoiceId', params: { invoiceId: id } }),
  contract: (id: string) =>
    linkOptions({ to: '/app/contracts/$contractId', params: { contractId: id } }),
  fiscal: () =>
    linkOptions({ to: '/app', search: { ...defaultDashboardSearch(), tab: 'anno' } }),
  proposal: (id: string) =>
    linkOptions({ to: '/app/proposals/$proposalId', params: { proposalId: id } }),
  // The timer is the caller's own and lives in the hours page, which has no id to open.
  timer: () => linkOptions({ to: '/app/hours' }),
  // A job has no id the page could open: the section lists them all.
  job: () => linkOptions({ to: '/app/settings/jobs' }),
}

export type RecordLink = LinkProps

/** The link that opens what the reminder is about, or `null` for a kind with no page. */
export function recordLink(reminder: Pick<Reminder, 'entity' | 'entity_id'>): RecordLink | null {
  if (!Object.hasOwn(RECORD_LINKS, reminder.entity)) return null
  return RECORD_LINKS[reminder.entity]!(reminder.entity_id)
}
