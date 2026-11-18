import { describe, expect, it } from 'vitest'
import {
  BAND_FILLS,
  BAND_WORDS,
  USAGE_LEGEND,
  formatMonth,
  sortByUsage,
  usageCounts,
  usageHeadline,
} from './techUsage'
import { technology } from './tech-usage-test-kit'

describe('the usage bands in words and fills (REB-1130)', () => {
  it('names each band in feminine Italian, the word the legend uses', () => {
    expect(BAND_WORDS).toEqual({ continuous: 'continua', recurring: 'ricorrente', occasional: 'occasionale' })
    for (const word of Object.values(BAND_WORDS)) expect(USAGE_LEGEND).toContain(word)
  })

  it('fills a third, two thirds or all of the badge, and nothing in between', () => {
    expect(BAND_FILLS).toEqual({ occasional: 'third', recurring: 'two-thirds', continuous: 'full' })
  })

  it('says what the fill is and what it is not', () => {
    expect(USAGE_LEGEND).toContain('quanto spesso e con che continuità')
    expect(USAGE_LEGEND).toContain('non quanto la si conosce')
  })
})

describe('the order of the badges', () => {
  it('goes strongest band first, then the more months, then the name', () => {
    const rows = [
      technology({ name: 'Zod', band: 'recurring', months: 4 }),
      technology({ name: 'Go', band: 'occasional', months: 2 }),
      technology({ name: 'Vue', band: 'continuous', months: 7 }),
      technology({ name: 'Python', band: 'continuous', months: 12 }),
      technology({ name: 'Astro', band: 'continuous', months: 7 }),
      technology({ name: 'Rust', band: 'recurring', months: 5 }),
    ]

    expect(sortByUsage(rows).map((t) => t.name)).toEqual(['Python', 'Astro', 'Vue', 'Rust', 'Zod', 'Go'])
  })

  it('leaves the list it was given as it was', () => {
    const rows = [technology({ name: 'B', band: 'occasional' }), technology({ name: 'A' })]
    sortByUsage(rows)
    expect(rows.map((t) => t.name)).toEqual(['B', 'A'])
  })
})

describe('the counts behind a band', () => {
  it('writes a month as Italian does', () => {
    expect(formatMonth('2026-01')).toBe('gennaio 2026')
    expect(formatMonth('2025-12')).toBe('dicembre 2025')
  })

  it('says the counts with their plurals, as sessions in products', () => {
    expect(usageCounts(technology())).toBe('12 sessioni in 3 prodotti, 6 mesi, da gennaio 2026 a giugno 2026')
    expect(
      usageCounts(technology({ sessions: 1, products: 1, months: 1, first_month: '2026-03', last_month: '2026-03' })),
    ).toBe('1 sessione in 1 prodotto, 1 mese, marzo 2026')
  })

  it('heads it with the band', () => {
    expect(usageHeadline(technology())).toBe('Presenza continua')
    expect(usageHeadline(technology({ band: 'recurring' }))).toBe('Presenza ricorrente')
    expect(usageHeadline(technology({ band: 'occasional' }))).toBe('Presenza occasionale')
  })
})

describe('a month that is no date', () => {
  it('reads as it came instead of throwing', () => {
    expect(formatMonth('nope')).toBe('nope')
  })
})
