/** The member area's own arithmetic for «Come si calcola?» (REB-610): a rate as the
 *  member reads it, and one worked example built from the live rates. Integers all the
 *  way, so a rate like `0.1250` never meets binary floating point. */

/** The API stores a rate as a fraction with four decimals (`RATE_PLACES` in the core's
 *  models): `0.1250` is 1250 units of a ten-thousandth. */
const RATE_SCALE = 10_000

/** The invented numbers of the popover's example, in whole euro and days. They are not
 *  any engagement's: the member area never shows a real one. */
export const EXAMPLE = { budget: 500, compenso: 400, giorni: 20 } as const

const percent = new Intl.NumberFormat('it-IT', { maximumFractionDigits: 2 })

/** A rate as the API answers it (`"0.1000"`) in ten-thousandths, `null` for anything
 *  that is not a fraction between 0 and 1 (the API bounds it the same way). */
function rateUnits(rate: string): number | null {
  if (!/^\d+(\.\d+)?$/.test(rate)) return null
  const units = Math.round(Number(rate) * RATE_SCALE)
  return units >= 0 && units <= RATE_SCALE ? units : null
}

/** `"0.1000"` as `10%`, `"0.1250"` as `12,5%`: Italian decimals, no trailing zeros, two
 *  decimals at most (a four-place fraction has no more). `null` when there is no valid
 *  rate to say, so the caller says nothing rather than a wrong number. */
export function formatRate(rate: string | null | undefined): string | null {
  const units = rate == null ? null : rateUnits(rate)
  return units === null ? null : `${percent.format(units / 100)}%`
}

/** The rate with the article its Italian reading takes, for a sentence: `il 10%`, and
 *  `l'8%`, `l'11%`, `l'80%`, `l'1%` (otto, undici, ottanta, uno), `lo 0,5%` (zero). `null`
 *  when there is no valid rate. The rates are an admin's to edit, so no number is safe to
 *  hard-code before. */
export function rateWithArticle(rate: string | null | undefined): string | null {
  const units = rate == null ? null : rateUnits(rate)
  if (units === null) return null
  const whole = Math.floor(units / 100)
  const article = whole === 0 ? 'lo ' : whole === 1 || whole === 8 || whole === 11 || (whole >= 80 && whole <= 89) ? "l'" : 'il '
  return `${article}${percent.format(units / 100)}%`
}

/** `amount * units / RATE_SCALE`, in cents, rounded half to even: the mode Python's
 *  `Decimal.quantize(Decimal('0.01'))` uses in the backend's own `base * rate`. */
export function roundedShare(cents: number, units: number): number {
  const product = cents * units
  const whole = Math.floor(product / RATE_SCALE)
  const rest = product - whole * RATE_SCALE
  if (rest * 2 < RATE_SCALE) return whole
  if (rest * 2 > RATE_SCALE) return whole + 1
  return whole % 2 === 0 ? whole : whole + 1
}

export interface ReferralExample {
  /** What rebase keeps: `(budget - compenso) * giorni`, in euro, as `"2000.00"`. */
  margin: string
  /** The reward on that margin at the freelancer rate, to the cent, or `null` with no rate. */
  freelancer: string | null
  company: string | null
}

/** Cents as the decimal string the API speaks (`"2000.00"`), for `formatEuro`. */
function money(cents: number): string {
  return `${Math.floor(cents / 100)}.${String(cents % 100).padStart(2, '0')}`
}

/** The worked example of the popover: the invented margin and what each rate makes of it. */
export function referralExample(rates: {
  rate_freelancer: string | null | undefined
  rate_company: string | null | undefined
}): ReferralExample {
  const marginCents = (EXAMPLE.budget - EXAMPLE.compenso) * EXAMPLE.giorni * 100
  const share = (rate: string | null | undefined) => {
    const units = rate == null ? null : rateUnits(rate)
    return units === null ? null : money(roundedShare(marginCents, units))
  }
  return { margin: money(marginCents), freelancer: share(rates.rate_freelancer), company: share(rates.rate_company) }
}
