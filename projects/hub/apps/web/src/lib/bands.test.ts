import { describe, expect, it } from 'vitest'
import { bandLabel } from './bands'
import { formatEuro } from './format'

// Written as escapes on purpose: both characters are invisible or look-alikes in an editor.
const NBSP = '\u00a0'
const DASH = '\u2013'

/** The same cases as core's `test_bands.py`: the page writes a band byte for byte as
 *  `Band.label()` does, so an admin and a visitor read the same words. */
describe('bandLabel', () => {
  it('writes a band per day with its two bounds and an en dash', () => {
    expect(bandLabel({ min: 400, max: 500 })).toBe(`400${DASH}500${NBSP}€ al giorno`)
    expect(bandLabel({ min: 650, max: 800 }, 'giorno')).toBe(`650${DASH}800${NBSP}€ al giorno`)
  })

  it('says «fino a» for a band from nothing and «oltre» for one with no top', () => {
    expect(bandLabel({ min: 0, max: 300 })).toBe(`fino a 300${NBSP}€ al giorno`)
    expect(bandLabel({ min: 800, max: null })).toBe(`oltre 800${NBSP}€ al giorno`)
  })

  it('writes a month with a dot between the thousands', () => {
    expect(bandLabel({ min: 8800, max: 11000 }, 'mese')).toBe(`8.800${DASH}11.000${NBSP}€ al mese`)
    expect(bandLabel({ min: 17600, max: 23100 }, 'mese')).toBe(`17.600${DASH}23.100${NBSP}€ al mese`)
    expect(bandLabel({ min: 0, max: 6600 }, 'mese')).toBe(`fino a 6.600${NBSP}€ al mese`)
    expect(bandLabel({ min: 1_100_000, max: null }, 'mese')).toBe(`oltre 1.100.000${NBSP}€ al mese`)
  })

  it('never breaks a line before «€», with the same space formatEuro writes', () => {
    expect(bandLabel({ min: 400, max: 500 })).not.toContain(' €')
    expect(formatEuro('450')).toContain(`${NBSP}€`)
  })
})
