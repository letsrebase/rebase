import type { Attivita } from '@/features/calendar/queries'

/**
 * The four records an attività may hang from, as the API spells them
 * (`attivita/schemas.py`, `RIFERIMENTI`): at most one at a time, which is why the dialog
 * holds one `LinkValue` and never four ids.
 */
export type LinkKind = 'customer' | 'person' | 'deal' | 'invoice'

export interface LinkValue {
  kind: LinkKind
  id: string
}

/** What the screens call each kind, in the singular. */
export const LINK_KIND_LABELS: Record<LinkKind, string> = {
  customer: 'Cliente',
  person: 'Persona',
  deal: 'Deal',
  invoice: 'Fattura',
}

type LinkColumns = Pick<Attivita, 'customer_id' | 'person_id' | 'deal_id' | 'invoice_id'>

/** The record an attività hangs from, or `null` for one that hangs from none. */
export function linkOf(attivita: LinkColumns): LinkValue | null {
  if (attivita.customer_id) return { kind: 'customer', id: attivita.customer_id }
  if (attivita.person_id) return { kind: 'person', id: attivita.person_id }
  if (attivita.deal_id) return { kind: 'deal', id: attivita.deal_id }
  if (attivita.invoice_id) return { kind: 'invoice', id: attivita.invoice_id }
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
    customer_id: link?.kind === 'customer' ? link.id : null,
    person_id: link?.kind === 'person' ? link.id : null,
    deal_id: link?.kind === 'deal' ? link.id : null,
    invoice_id: link?.kind === 'invoice' ? link.id : null,
  }
}

export function sameLink(a: LinkValue | null, b: LinkValue | null): boolean {
  return a?.kind === b?.kind && a?.id === b?.id
}
