import { describe, expect, it } from 'vitest'
import { parsePurchasesSearch } from './search'

const UUID = '0199aaaa-0000-7000-8000-000000000001'

describe('parsePurchasesSearch', () => {
  it('keeps every filter that is valid', () => {
    expect(
      parsePurchasesSearch({
        kind: 'credit_note',
        supplier: UUID,
        category: UUID,
        from: '2026-10-01',
        to: '2026-10-31',
        cancelled: true,
      }),
    ).toEqual({
      kind: 'credit_note',
      supplier: UUID,
      category: UUID,
      from: '2026-10-01',
      to: '2026-10-31',
      cancelled: true,
    })
  })

  it('drops a value that is not ours instead of carrying it', () => {
    const parsed = parsePurchasesSearch({
      kind: 'bill',
      supplier: 'acme',
      category: 42,
      from: '2026-02-30',
      to: 'domani',
      cancelled: false,
    })
    expect(Object.values(parsed).every((value) => value === undefined)).toBe(true)
  })

  it('reads a hand-typed cancelled=true', () => {
    expect(parsePurchasesSearch({ cancelled: 'true' }).cancelled).toBe(true)
  })
})
