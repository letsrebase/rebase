import { toIsoMonth } from '@/lib/dates'

export interface DaysSearch {
  /** One contract's days; omitted, every contract's. */
  contract?: string
  /** `AAAA-MM`; omitted with a contract chosen, every month of that contract. */
  mese?: string
}

export interface NewDaySearch {
  contract?: string
  data?: string
}

const MESE = /^\d{4}-(?:0[1-9]|1[0-2])$/
const DATA = /^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])$/
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

function text(value: unknown, pattern: RegExp): string | undefined {
  return typeof value === 'string' && pattern.test(value) ? value : undefined
}

/**
 * `?contract=&mese=`: both in the URL, so a link to a contract's days or to a month is a
 * link. A malformed value is dropped rather than refused: the filter is a view, and a
 * pasted URL with a bad month opens the page's default instead of an error.
 */
export function validateDaysSearch(search: Record<string, unknown>): DaysSearch {
  return { contract: text(search.contract, UUID), mese: text(search.mese, MESE) }
}

export function validateNewDaySearch(search: Record<string, unknown>): NewDaySearch {
  return { contract: text(search.contract, UUID), data: text(search.data, DATA) }
}

/** The first and last day of `AAAA-MM`, as the API's `da` and `a`. */
export function monthWindow(mese: string): { da: string; a: string } {
  const [year = 0, month = 1] = mese.split('-').map(Number)
  const last = new Date(year, month, 0).getDate()
  return { da: `${mese}-01`, a: `${mese}-${String(last).padStart(2, '0')}` }
}

/** The month to open on when the URL names none and no contract narrows the list. */
export function defaultMese(today: Date = new Date()): string {
  return toIsoMonth(today)
}
