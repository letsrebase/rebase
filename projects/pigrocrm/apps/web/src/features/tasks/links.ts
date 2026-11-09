import type { Task } from '@/features/calendar/queries'

/**
 * The four records a task may hang from, as the API spells them
 * (`tasks/schemas.py`, `REFERENCES`): at most one at a time, which is why the dialog
 * holds one `LinkValue` and never four ids.
 */
export type LinkKind = 'organization' | 'person' | 'deal' | 'invoice'

export interface LinkValue {
  kind: LinkKind
  id: string
}

/** What the screens call each kind, in the singular. */
export const LINK_KIND_LABELS: Record<LinkKind, string> = {
  organization: 'Cliente',
  person: 'Persona',
  deal: 'Deal',
  invoice: 'Fattura',
}

/** The query parameter and the body column that name each kind, as the API spells them. */
export const LINK_COLUMN = {
  organization: 'customer_id',
  person: 'person_id',
  deal: 'deal_id',
  invoice: 'invoice_id',
} as const satisfies Record<LinkKind, string>

type LinkColumns = Pick<Task, 'customer_id' | 'person_id' | 'deal_id' | 'invoice_id'>

/** The record a task hangs from, or `null` for one that hangs from none. */
export function linkOf(task: LinkColumns): LinkValue | null {
  if (task.customer_id) return { kind: 'organization', id: task.customer_id }
  if (task.person_id) return { kind: 'person', id: task.person_id }
  if (task.deal_id) return { kind: 'deal', id: task.deal_id }
  if (task.invoice_id) return { kind: 'invoice', id: task.invoice_id }
  return null
}

/** The four columns of a write body for `link`: the one that is set, the rest `null`. */
export function linkColumns(link: LinkValue | null): {
  customer_id: string | null
  person_id: string | null
  deal_id: string | null
  invoice_id: string | null
} {
  return {
    customer_id: link?.kind === 'organization' ? link.id : null,
    person_id: link?.kind === 'person' ? link.id : null,
    deal_id: link?.kind === 'deal' ? link.id : null,
    invoice_id: link?.kind === 'invoice' ? link.id : null,
  }
}

export function sameLink(a: LinkValue | null, b: LinkValue | null): boolean {
  return a?.kind === b?.kind && a?.id === b?.id
}
