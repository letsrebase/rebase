import { describe, expect, it } from 'vitest'
import { formatExperience, formatLevel } from './format'

describe('a card’s level in words (REB-799)', () => {
  it('writes the years, and says when the CV gives none', () => {
    expect(formatExperience(0)).toBe('meno di un anno di esperienza')
    expect(formatExperience(1)).toBe('1 anno di esperienza')
    expect(formatExperience(9)).toBe('9 anni di esperienza')
    expect(formatExperience(null)).toBe('Anni non indicati nel CV')
  })

  it('marks a seniority the CV’s dates could not back', () => {
    expect(formatLevel({ seniority: 'senior', anni: 9 })).toBe('Senior, 9 anni di esperienza')
    expect(formatLevel({ seniority: 'mid', anni: null, seniority_incerta: true })).toBe(
      'Mid (da verificare), Anni non indicati nel CV',
    )
    expect(formatLevel({ seniority: 'lead', anni: 14, seniority_incerta: false })).toBe('Lead, 14 anni di esperienza')
  })
})
