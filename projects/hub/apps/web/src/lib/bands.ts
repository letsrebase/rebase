import type { Band } from './api'

/** Between a figure and «€», as `formatEuro` (`Intl.NumberFormat('it-IT')`) writes it:
 *  a line never breaks between the two. */
const NBSP = '\u00a0'
/** Between a band's two bounds, as core's `Band.bounds()` writes them. */
const DASH = '\u2013'

/** Whole euro the Italian way, `17.600`, for any number of digits: the `it-IT` grouping
 *  of `Intl` leaves a four-digit figure alone (`8800`), and core's `_euro` does not. */
function euro(amount: number): string {
  return String(amount).replace(/\B(?=(\d{3})+(?!\d))/g, '.')
}

/** «400–500», «oltre 800», «fino a 300»: a band from nothing says only its top, since
 *  «0–300» reads as a price that could be nothing. */
function bounds(band: Band): string {
  if (band.max === null) return `oltre ${euro(band.min)}`
  if (band.min === 0) return `fino a ${euro(band.max)}`
  return `${euro(band.min)}${DASH}${euro(band.max)}`
}

/** A client's price band, byte for byte as core's `Band.label()` writes it (REB-511,
 *  spec § 3.3): «400–500 € al giorno», «oltre 800 € al giorno», «8.800–11.000 € al
 *  mese». The web cannot import Python, so `bands.test.ts` holds the same cases as core's
 *  `test_bands.py`. */
export function bandLabel(band: Band, per: 'giorno' | 'mese' = 'giorno'): string {
  return `${bounds(band)}${NBSP}€ al ${per}`
}

/** Core's `BANDS`: euro per day, each `[min, max)`, the last with no top. */
const BANDS: readonly Band[] = [
  { min: 0, max: 300 },
  { min: 300, max: 400 },
  { min: 400, max: 500 },
  { min: 500, max: 650 },
  { min: 650, max: 800 },
  { min: 800, max: null },
]

/** The client's band for a freelancer's own daily rate, as core's `band_for` places it
 *  (spec § 3.3): the rate plus 40%, in the band whose bottom it reaches. The API's
 *  `"450.00"` is counted in cents, and the price compared in thousandths of a euro
 *  (cents × 14 against the top × 1000), so the arithmetic stays in whole numbers and a
 *  float never moves a rate across a bound. `null` without a rate: «tariffa da
 *  definire». Only the admin's talent page computes one here, since the card read
 *  carries the rate and not the band; everything else reads the band core wrote. */
export function bandFor(tariffa: string | null): Band | null {
  if (tariffa === null) return null
  const cents = Math.round(Number(tariffa) * 100)
  if (!Number.isFinite(cents)) return null
  const price = cents * 14
  return BANDS.find((band) => band.max === null || price < band.max * 1000) ?? null
}
