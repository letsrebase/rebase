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
  source: 'deal' | 'amount'
  value: string
  hours: string
  rate: string
  /** The chosen deal's own three estimate columns, as the API sent them. */
  deal: {
    budgeted_value: string | null
    budgeted_hours: string | null
    hourly_rate: string | null
  } | null
  /** The day the money arrives, `YYYY-MM-DD`. Sent whenever it is given: the server reads
   *  the year of a contract's cap from it, and the ceilings' year from `year`. */
  date?: string
  /** A contract with a cap on the customer's share, to read the addition against too. */
  contractId?: string | null
}

export type SimulatorRequest =
  | {
      ok: true
      input: {
        budgeted_value?: string
        budgeted_hours?: string
        hourly_rate?: string
        expected_date?: string
        contract_id?: string
      }
    }
  | { ok: false; errors: Partial<Record<'value' | 'hours' | 'rate' | 'deal' | 'form', string>> }

/**
 * The request the typed fields make, or what keeps them from making one. The estimate is
 * a value, or hours with a rate: the two shapes `CeilingSimulationQuery` takes, and the
 * value wins when both are given, as it does on the server.
 */
function estimateRequest(fields: SimulatorFields): SimulatorRequest {
  if (fields.source === 'deal') {
    const deal = fields.deal
    if (deal === null) return { ok: false, errors: { deal: 'Scegli un deal.' } }
    if (deal.budgeted_value !== null) {
      return { ok: true, input: { budgeted_value: deal.budgeted_value } }
    }
    if (deal.budgeted_hours !== null && deal.hourly_rate !== null) {
      return {
        ok: true,
        input: { budgeted_hours: deal.budgeted_hours, hourly_rate: deal.hourly_rate },
      }
    }
    return {
      ok: false,
      errors: { deal: 'Questo deal non ha una stima: scegli «Un importo» e scrivila qui.' },
    }
  }
  const value = parseValue(fields.value)
  const hours = parseHours(fields.hours)
  const rate = parseRate(fields.rate)
  const errors: Partial<Record<'value' | 'hours' | 'rate' | 'form', string>> = {}
  if (value && !value.ok) errors.value = value.error
  if (hours && !hours.ok) errors.hours = hours.error
  if (rate && !rate.ok) errors.rate = rate.error
  if (Object.keys(errors).length > 0) return { ok: false, errors }
  if (value?.ok) return { ok: true, input: { budgeted_value: value.value } }
  if (hours?.ok && rate?.ok) {
    return { ok: true, input: { budgeted_hours: hours.value, hourly_rate: rate.value } }
  }
  return {
    ok: false,
    errors: { form: 'Scrivi un importo, oppure le ore e la tariffa oraria.' },
  }
}

/**
 * The estimate's request with the day and the contract the person chose put on it, so one
 * press of «Simula» asks the ceilings and the contract's cap about the same addition.
 */
export function simulatorRequest(fields: SimulatorFields): SimulatorRequest {
  const request = estimateRequest(fields)
  if (!request.ok) return request
  return {
    ok: true,
    input: {
      ...request.input,
      ...(fields.date ? { expected_date: fields.date } : {}),
      ...(fields.contractId ? { contract_id: fields.contractId } : {}),
    },
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
