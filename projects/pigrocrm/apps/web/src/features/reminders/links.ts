import { linkOptions, type LinkProps } from '@tanstack/react-router'
import type { Reminder } from './fields'

/**
 * Where each kind of record a reminder names is opened: the one table. The API says what
 * a reminder is about in `entity` and `entity_id`, and every card that adds a type adds
 * its row here, nowhere else.
 *
 * `linkOptions` is what keeps a row honest: its `to` and `params` are checked against the
 * generated route tree, so a renamed route fails to compile here and not as a dead link.
 *
 * Left out on purpose, because there is no page to open today: `contract` and
 * `work_unit` (no contract page exists yet) and the space-wide kinds that name no record
 * (`connection`, `job`, `fiscal`). A reminder whose `entity` is not in the table, or is
 * one of those, is shown with no link rather than with a guessed one.
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
  proposal: (id: string) =>
    linkOptions({ to: '/app/proposals/$proposalId', params: { proposalId: id } }),
  // The timer is the caller's own and lives in the hours page, which has no id to open.
  timer: () => linkOptions({ to: '/app/hours' }),
}

export type RecordLink = LinkProps

/** The link that opens what the reminder is about, or `null` for a kind with no page. */
export function recordLink(reminder: Pick<Reminder, 'entity' | 'entity_id'>): RecordLink | null {
  if (!Object.hasOwn(RECORD_LINKS, reminder.entity)) return null
  return RECORD_LINKS[reminder.entity]!(reminder.entity_id)
}
