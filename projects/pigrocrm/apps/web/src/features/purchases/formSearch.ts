/**
 * What «Nuovo acquisto» can be opened with: the deal it is for, the supplier it is from and
 * the «In arrivo» item whose file sits beside the form (the approved brief of 2026-11-10,
 * «Capture»). Each is an id, so each is kept only when it reads as one: a hand-typed or stale
 * link never reaches the form as a value the API would answer 422 to.
 */
export interface NewPurchaseSearch {
  deal_id?: string
  supplier_id?: string
  inbox_item_id?: string
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

function uuidOf(value: unknown): string | undefined {
  return typeof value === 'string' && UUID.test(value) ? value : undefined
}

/** The route's `validateSearch`: an id each, dropped (not carried empty) when it is not a UUID,
 *  so the URL never claims a prefill that is not applied. */
export function parseNewPurchaseSearch(search: Record<string, unknown>): NewPurchaseSearch {
  return {
    deal_id: uuidOf(search.deal_id),
    supplier_id: uuidOf(search.supplier_id),
    inbox_item_id: uuidOf(search.inbox_item_id),
  }
}
