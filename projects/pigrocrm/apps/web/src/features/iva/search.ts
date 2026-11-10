export type IvaRegisterTab = 'sales' | 'purchases'

export type IvaFrequency = 'monthly' | 'quarterly'

/**
 * `?year=2026&period=4&frequency=quarterly&register=sales&entity=<id>`: the year, the period, the
 * register and, under «Tutte le aziende» with several legal entities that settle IVA, the one being
 * read, all in the URL so a link reopens the same reading. Every value that is not ours is dropped,
 * so the URL never claims a choice that was not made; with no period the page opens on the one
 * running now.
 *
 * A period number means nothing without its frequency: a quarterly `3` is September, a monthly `3`
 * is March, and the sidebar's choice of legal entity can swap one for the other under the same URL.
 * So the page honours `period` only for a period of that `frequency` (REB-1036), and writes both
 * together.
 */
export interface IvaSearch {
  year?: number
  period?: number
  frequency?: IvaFrequency
  register?: IvaRegisterTab
  entity?: string
}

const REGISTERS: readonly IvaRegisterTab[] = ['sales', 'purchases']
const FREQUENCIES: readonly IvaFrequency[] = ['monthly', 'quarterly']

const FIRST_YEAR = 2000
const LAST_YEAR = 2200
const MAX_PERIOD = 12
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

function whole(value: unknown, min: number, max: number): number | undefined {
  return typeof value === 'number' && Number.isInteger(value) && value >= min && value <= max
    ? value
    : undefined
}

export function validateIvaSearch(search: Record<string, unknown>): IvaSearch {
  return {
    year: whole(search.year, FIRST_YEAR, LAST_YEAR),
    period: whole(search.period, 1, MAX_PERIOD),
    frequency: FREQUENCIES.find((candidate) => candidate === search.frequency),
    register: REGISTERS.find((candidate) => candidate === search.register),
    entity: typeof search.entity === 'string' && UUID.test(search.entity) ? search.entity : undefined,
  }
}
