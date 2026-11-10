import { describe, expect, it } from 'vitest'
import { documentLabel, documentLine, foreignAmount, specialChips, withholdingLabel } from './labels'

describe('the words of a purchase', () => {
  it('writes the document from its kind and number', () => {
    expect(documentLabel({ kind: 'invoice', number: '2026/0418' })).toBe('Fattura 2026/0418')
    expect(documentLabel({ kind: 'receipt', number: null })).toBe('Ricevuta')
    expect(documentLine({ kind: 'invoice', number: '14/E', date: '2026-10-28' })).toBe(
      'Fattura 14/E · 28/10/2026',
    )
  })

  it('names what is special, credit note first', () => {
    expect(
      specialChips({ kind: 'credit_note', reverse_charge: true, split_payment: true }),
    ).toEqual(['Nota di credito', 'Inversione contabile', 'Scissione dei pagamenti'])
    expect(specialChips({ kind: 'invoice', reverse_charge: false, split_payment: false })).toEqual([])
  })

  it('shows the original of a foreign amount and nothing for euros', () => {
    expect(foreignAmount({ currency: 'USD', currency_amount: '540.00' })).toBe('USD 540,00')
    expect(foreignAmount({ currency: 'EUR', currency_amount: null })).toBeNull()
  })

  it('shows the withholding as stored', () => {
    expect(withholdingLabel({ withholding_amount: '270.00' })).toMatch(/^ritenuta 270,00\s€$/)
    expect(withholdingLabel({ withholding_amount: null })).toBeNull()
  })
})
