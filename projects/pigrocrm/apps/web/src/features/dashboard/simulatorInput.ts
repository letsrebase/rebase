/**
 * What the «ci sta?» simulator accepts, and what it sends.
 *
 * A keyboard in Italy produces `12.000,50` or `12000,5`; the API's `Decimal` parser wants
 * `12000.50`. The translation is a string rewrite and never a parse into a number, which
 * is what `no-browser-arithmetic.test.ts` guards in this folder: the amount that goes out
 * is the one that was typed.
 *
 * One rule for the comma and the point, stated here because it is the ambiguous one: with
 * a comma anywhere, the points are thousands separators and the comma is the decimal mark
 * (`1.234,5`); without one, a point is the decimal mark (`1234.5`), the shape `<input
 * inputmode="decimal">` also produces on a phone keyboard set to English.
 */

export type Amount = { ok: true; value: string } | { ok: false; error: string }

/** `"12.000,50"` -> `"12000.50"`, or `null` when it is not a decimal at all. */
function normalise(raw: string): string | null {
  const compact = raw.replace(/\s/g, '')
  const text = compact.includes(',') ? compact.replace(/\./g, '').replace(',', '.') : compact
  return /^\d+(\.\d+)?$/.test(text) ? text : null
}

/** A non-negative decimal of at most `whole` integer and `decimals` fractional digits, the
 *  widths the API's `Numeric` columns carry. Blank is its own answer: nothing was typed. */
function amount(raw: string, whole: number, decimals: number, what: string): Amount | null {
  if (raw.trim() === '') return null
  const text = normalise(raw)
  if (text === null) return { ok: false, error: `${what}: scrivi un numero, per esempio 12.000,50` }
  const [integer = '', fraction = ''] = text.split('.')
  if (integer.length > whole || fraction.length > decimals) {
    return {
      ok: false,
      error:
        fraction.length > decimals
          ? `${what}: al massimo ${decimals} decimali`
          : `${what}: troppo grande`,
    }
  }
  return { ok: true, value: text }
}

export const parseValue = (raw: string) => amount(raw, 10, 2, 'Importo')
export const parseHours = (raw: string) => amount(raw, 6, 2, 'Ore')
export const parseRate = (raw: string) => amount(raw, 6, 6, 'Tariffa oraria')

export type SimulatorFields = {
  /** Where the estimate comes from: a deal that carries one, or what is typed. */
  source: 'deal' | 'importo'
  valore: string
  ore: string
  tariffa: string
  /** The chosen deal's own three estimate columns, as the API sent them. */
  deal: {
    valore_preventivato: string | null
    ore_preventivate: string | null
    tariffa_oraria: string | null
  } | null
}

export type SimulatorRequest =
  | {
      ok: true
      input: { valore_preventivato?: string; ore_preventivate?: string; tariffa_oraria?: string }
    }
  | { ok: false; errors: Partial<Record<'valore' | 'ore' | 'tariffa' | 'deal' | 'form', string>> }

/**
 * The request the typed fields make, or what keeps them from making one. The estimate is
 * a value, or hours with a rate: the two shapes `CeilingSimulationQuery` takes, and the
 * value wins when both are given, as it does on the server.
 */
export function simulatorRequest(fields: SimulatorFields): SimulatorRequest {
  if (fields.source === 'deal') {
    const deal = fields.deal
    if (deal === null) return { ok: false, errors: { deal: 'Scegli un deal.' } }
    if (deal.valore_preventivato !== null) {
      return { ok: true, input: { valore_preventivato: deal.valore_preventivato } }
    }
    if (deal.ore_preventivate !== null && deal.tariffa_oraria !== null) {
      return {
        ok: true,
        input: { ore_preventivate: deal.ore_preventivate, tariffa_oraria: deal.tariffa_oraria },
      }
    }
    return {
      ok: false,
      errors: { deal: 'Questo deal non ha una stima: scegli «Un importo» e scrivila qui.' },
    }
  }
  const valore = parseValue(fields.valore)
  const ore = parseHours(fields.ore)
  const tariffa = parseRate(fields.tariffa)
  const errors: Partial<Record<'valore' | 'ore' | 'tariffa' | 'form', string>> = {}
  if (valore && !valore.ok) errors.valore = valore.error
  if (ore && !ore.ok) errors.ore = ore.error
  if (tariffa && !tariffa.ok) errors.tariffa = tariffa.error
  if (Object.keys(errors).length > 0) return { ok: false, errors }
  if (valore?.ok) return { ok: true, input: { valore_preventivato: valore.value } }
  if (ore?.ok && tariffa?.ok) {
    return { ok: true, input: { ore_preventivate: ore.value, tariffa_oraria: tariffa.value } }
  }
  return {
    ok: false,
    errors: { form: 'Scrivi un importo, oppure le ore e la tariffa oraria.' },
  }
}

/** The calendar year of an ISO `YYYY-MM-DD` the API can answer for (2000 to 2200, its own
 *  bounds), or `null`. A date, not an amount, so reading it through `Date` is not the
 *  coercion `no-browser-arithmetic.test.ts` bans; noon UTC keeps the day in its year in
 *  every time zone. */
export function yearOfIso(value: string): number | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return null
  const year = new Date(`${value.slice(0, 4)}-06-15T12:00:00Z`).getUTCFullYear()
  return year >= 2000 && year <= 2200 ? year : null
}
