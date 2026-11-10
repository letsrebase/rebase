import { describe, expect, it } from 'vitest'
import {
  customerKindText,
  normaliseVat,
  orderedRoles,
  rolesInWords,
  supplierKindText,
  supplierTermsLong,
  supplierTermsShort,
} from './roles'

describe('the words of the registry', () => {
  it('keeps roles in the registry\'s order and an unknown one after them', () => {
    expect(orderedRoles(['supplier', 'customer'])).toEqual(['customer', 'supplier'])
    expect(orderedRoles(['mystery', 'freelancer', 'customer'])).toEqual(['customer', 'freelancer', 'mystery'])
  })

  it('says the roles in a sentence', () => {
    expect(rolesInWords(['customer'])).toBe('cliente')
    expect(rolesInWords(['supplier', 'customer'])).toBe('cliente e fornitore')
    expect(rolesInWords(['supplier', 'customer', 'freelancer'])).toBe('cliente, fornitore e freelance')
  })

  it('normalises a VAT number as the server does: upper case, letters and digits only', () => {
    expect(normaliseVat('nl 1234.56.789 b01')).toBe('NL123456789B01')
    expect(normaliseVat(' - ')).toBe('')
  })

  it('names the fiscal kind, with area and supply for a foreign one only when the row says them', () => {
    const base = { supplier_kind: null, supplier_area: null, supplier_supplies: null }
    expect(supplierKindText(base)).toBe('—')
    expect(supplierKindText({ ...base, supplier_kind: 'company' })).toBe('Società')
    expect(supplierKindText({ supplier_kind: 'foreign', supplier_area: 'eu', supplier_supplies: null })).toBe(
      'Estero · UE',
    )
  })

  it('names the terms short and long', () => {
    expect(supplierTermsShort({ supplier_payment_days: 60, supplier_payment_end_of_month: false })).toBe('60 gg')
    expect(supplierTermsLong({ supplier_payment_days: 60, supplier_payment_end_of_month: true })).toBe(
      '60 giorni data fattura fine mese',
    )
    expect(supplierTermsLong({ supplier_payment_days: null, supplier_payment_end_of_month: false })).toBe('—')
  })

  it('names the customer kind', () => {
    expect(customerKindText({ customer_kind: 'public_body' })).toBe('Ente pubblico')
    expect(customerKindText({ customer_kind: null })).toBe('—')
  })
})
