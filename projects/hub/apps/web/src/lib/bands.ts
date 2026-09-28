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
