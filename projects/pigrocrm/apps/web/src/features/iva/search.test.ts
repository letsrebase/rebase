import { describe, expect, it } from 'vitest'
import { validateIvaSearch } from './search'

const ID = '0199aaaa-0000-7000-8000-000000000001'

describe('the search of «IVA»', () => {
  it('keeps what is ours and drops what is not', () => {
    expect(
      validateIvaSearch({
        year: 2026,
        period: 4,
        frequency: 'quarterly',
        register: 'purchases',
        entity: ID,
      }),
    ).toEqual({ year: 2026, period: 4, frequency: 'quarterly', register: 'purchases', entity: ID })
    expect(
      validateIvaSearch({
        year: '2026',
        period: 13,
        frequency: 'weekly',
        register: 'vendite',
        entity: 'not-an-id',
      }),
    ).toEqual({
      year: undefined,
      period: undefined,
      frequency: undefined,
      register: undefined,
      entity: undefined,
    })
    expect(validateIvaSearch({ year: 1999 }).year).toBeUndefined()
    expect(validateIvaSearch({ year: 2026.5 }).year).toBeUndefined()
    expect(validateIvaSearch({})).toEqual({
      year: undefined,
      period: undefined,
      frequency: undefined,
      register: undefined,
      entity: undefined,
    })
  })
})
