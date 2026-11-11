import { type PurchaseKind } from './queries'

/**
 * The search parameters of «Acquisti» (`/app/purchases`): the filters travel in the URL, so a
 * bookmark or a link from another page lands on the same view. Every value that is empty or not
 * one of ours is dropped rather than carried, so the URL never claims a filter that is not applied.
 */
export interface PurchasesSearch {
  kind?: PurchaseKind
  /** A supplier's organization id. */
  supplier?: string
  /** A cost category's id. */
  category?: string
  /** First day of the document date, `YYYY-MM-DD`. */
  from?: string
  /** Last day of the document date, `YYYY-MM-DD`. */
  to?: string
  /** The «Annullati» chip: shows the cancelled purchases instead of the registered ones. */
  cancelled?: boolean
}

const KINDS: readonly PurchaseKind[] = ['invoice', 'credit_note', 'receipt', 'expense', 'expense_note']

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

function uuid(value: unknown): string | undefined {
  return typeof value === 'string' && UUID.test(value) ? value : undefined
}

/** A `YYYY-MM-DD` that names a day that exists: `2026-02-30` rolls over in `Date`, so the shape
 *  alone is not enough. */
function day(value: unknown): string | undefined {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return undefined
  const parsed = new Date(`${value}T00:00:00Z`)
  return !isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value ? value : undefined
}

export function parsePurchasesSearch(search: Record<string, unknown>): PurchasesSearch {
  return {
    kind: KINDS.find((kind) => kind === search.kind),
    supplier: uuid(search.supplier),
    category: uuid(search.category),
    from: day(search.from),
    to: day(search.to),
    // The router parses `true` to a boolean; a hand-typed `?cancelled=true` may still be text.
    cancelled: search.cancelled === true || search.cancelled === 'true' ? true : undefined,
  }
}
