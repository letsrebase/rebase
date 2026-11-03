import type { Invoice } from './queries'

/**
 * The customer's purchase order on a document that can still change (REB-693): its
 * number, and optionally its date, CIG and CUP, as the four inputs hold them.
 */
export interface PurchaseOrderDraft {
  purchase_order_number: string
  purchase_order_date: string
  purchase_order_tender_code: string
  purchase_order_project_code: string
}

type PurchaseOrderFields = Pick<Invoice, 'purchase_order_number' | 'purchase_order_date' | 'purchase_order_tender_code' | 'purchase_order_project_code'>

/** `?? ''`: a `null` from the API, and an absent key from an API that predates the
 * columns, both read as "not set". */
export function purchaseOrderDraft(invoice: PurchaseOrderFields): PurchaseOrderDraft {
  return {
    purchase_order_number: invoice.purchase_order_number ?? '',
    purchase_order_date: invoice.purchase_order_date ?? '',
    purchase_order_tender_code: invoice.purchase_order_tender_code ?? '',
    purchase_order_project_code: invoice.purchase_order_project_code ?? '',
  }
}

/** The one mistake worth catching before the request: a date, a CIG or a CUP with no
 * order to belong to. The CIG's and CUP's own shape is the server's to judge, and its
 * sentence is shown as it comes. */
export function validatePurchaseOrder(draft: PurchaseOrderDraft): string | undefined {
  const hasRest =
    draft.purchase_order_date !== '' || draft.purchase_order_tender_code.trim() !== '' || draft.purchase_order_project_code.trim() !== ''
  if (draft.purchase_order_number.trim() === '' && hasRest) return 'Indica il numero dell’ordine.'
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
