import type { Invoice } from './queries'

/**
 * The customer's purchase order on a document that can still change (REB-693): its
 * number, and optionally its date, CIG and CUP, as the four inputs hold them.
 */
export interface PurchaseOrderDraft {
  ordine_numero: string
  ordine_data: string
  ordine_cig: string
  ordine_cup: string
}

type PurchaseOrderFields = Pick<Invoice, 'ordine_numero' | 'ordine_data' | 'ordine_cig' | 'ordine_cup'>

/** `?? ''`: a `null` from the API, and an absent key from an API that predates the
 * columns, both read as "not set". */
export function purchaseOrderDraft(invoice: PurchaseOrderFields): PurchaseOrderDraft {
  return {
    ordine_numero: invoice.ordine_numero ?? '',
    ordine_data: invoice.ordine_data ?? '',
    ordine_cig: invoice.ordine_cig ?? '',
    ordine_cup: invoice.ordine_cup ?? '',
  }
}

/** The one mistake worth catching before the request: a date, a CIG or a CUP with no
 * order to belong to. The CIG's and CUP's own shape is the server's to judge, and its
 * sentence is shown as it comes. */
export function validatePurchaseOrder(draft: PurchaseOrderDraft): string | undefined {
  const hasRest =
    draft.ordine_data !== '' || draft.ordine_cig.trim() !== '' || draft.ordine_cup.trim() !== ''
  if (draft.ordine_numero.trim() === '' && hasRest) return 'Indica il numero dell’ordine.'
  return undefined
}

/** Only the fields the person edited, trimmed, `null` for an empty one (how the server
 * clears a field). A field left alone is not sent, so a value changed elsewhere since the
 * page loaded is never overwritten by a save of another field. */
export function purchaseOrderChanges(
  draft: PurchaseOrderDraft,
  touched: ReadonlySet<keyof PurchaseOrderDraft>,
): Record<string, string | null> {
  const changes: Record<string, string | null> = {}
  for (const field of touched) {
    const value = draft[field].trim()
    changes[field] = value === '' ? null : value
  }
  return changes
}
