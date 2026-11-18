import type { components } from './api-types'

/** One technology of a work profile with how often and how steadily it shows up in the
 *  person's work (REB-1130). The band is the hub's own derivation from the months; it is a
 *  label for those counts, never a score and never how well the person knows the technology. */
export type Technology = components['schemas']['WorkProfileTechnology']
export type UsageBand = Technology['band']

/** The band in the person's language: «presenza continua», «ricorrente», «occasionale».
 *  Feminine, since what they qualify is a presence, the same word the legend uses. */
export const BAND_WORDS: Record<UsageBand, string> = {
  continuous: 'continua',
  recurring: 'ricorrente',
  occasional: 'occasionale',
}

/** How much of the badge is filled: a third, two thirds, all of it, and no finer level than
 *  the band itself. */
export const BAND_FILLS = { occasional: 'third', recurring: 'two-thirds', continuous: 'full' } as const

const BAND_RANK: Record<UsageBand, number> = { occasional: 0, recurring: 1, continuous: 2 }

/** The one sentence under every group of badges: what the fill means and what it does not. */
export const USAGE_LEGEND =
  'Il riempimento indica quanto spesso e con che continuità la tecnologia compare nel lavoro, non quanto la si conosce (un terzo: occasionale, due terzi: ricorrente, pieno: continua).'

/** Strongest band first, then the more months, then the name: the order every screen lists
 *  technologies in. A copy, so the caller's list is left as it came. */
export function sortByUsage(technologies: readonly Technology[]): Technology[] {
  return [...technologies].sort(
    (a, b) => BAND_RANK[b.band] - BAND_RANK[a.band] || b.months - a.months || a.name.localeCompare(b.name),
  )
}

// A month the API sends as `YYYY-MM` is no instant: read on the first of it at midnight UTC
// and written in UTC, it is the same month in every browser's time zone.
const monthName = new Intl.DateTimeFormat('it-IT', { month: 'long', year: 'numeric', timeZone: 'UTC' })

/** `"2026-01"` as `gennaio 2026`. */
export function formatMonth(value: string): string {
  const date = new Date(`${value}-01T00:00:00Z`)
  // A month the API sent that is no date reads as it came, never as a crash of the page.
  return Number.isNaN(date.getTime()) ? value : monthName.format(date)
}

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`
}

/** The counts behind a band, in words: «12 sessioni in 3 prodotti, 6 mesi, da gennaio 2026 a
 *  giugno 2026». The sessions are those of the products where the technology was detected, so
 *  they are said as «in N prodotti», never as sessions that used it. */
export function usageCounts(technology: Technology): string {
  const { sessions, products, months, first_month, last_month } = technology
  const span =
    first_month === last_month
      ? formatMonth(first_month)
      : `da ${formatMonth(first_month)} a ${formatMonth(last_month)}`
  return `${plural(sessions, 'sessione', 'sessioni')} in ${plural(products, 'prodotto', 'prodotti')}, ${plural(months, 'mese', 'mesi')}, ${span}`
}

/** «Presenza continua»: the band's first line, on hover and in the accessible label. */
export function usageHeadline(technology: Technology): string {
  return `Presenza ${BAND_WORDS[technology.band]}`
}
