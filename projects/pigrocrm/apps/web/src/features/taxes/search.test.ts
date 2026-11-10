import { describe, expect, it } from 'vitest'
import { validateTaxesSearch } from './search'

describe('validateTaxesSearch', () => {
  it('opens «Imposte» on the current year by default', () => {
    expect(validateTaxesSearch({})).toEqual({ tab: 'taxes' })
    expect(validateTaxesSearch({ tab: 'nope', year: 'x' })).toEqual({ tab: 'taxes' })
  })

  it('keeps a known tab and a whole year the API accepts', () => {
    expect(validateTaxesSearch({ tab: 'dates', year: 2025 })).toEqual({ tab: 'dates', year: 2025 })
    expect(validateTaxesSearch({ year: 1999 })).toEqual({ tab: 'taxes' })
    expect(validateTaxesSearch({ year: 2025.5 })).toEqual({ tab: 'taxes' })
  })
})
