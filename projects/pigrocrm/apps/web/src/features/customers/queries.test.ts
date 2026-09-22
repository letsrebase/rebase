import { describe, expect, it } from 'vitest'
import { buildCustomerColumns } from './columns'
import type { FieldDefinition } from '@/lib/schema'
import type { Customer } from './queries'

/**
 * Reads a native column's cell value the same way `DataTable` does internally --
 * through the column's own `accessorFn` -- for the two columns below that carry
 * one. Verified live in a browser first (see task-6-report.md): after using
 * `CustomerForm`'s edit dialog to clear a previously-set "Telefono", the detail
 * page rendered a blank cell instead of the dash every other absent value gets,
 * because `CustomerUpdate.model_dump(exclude_none=True)` only clears a native
 * column on an explicit `""`, never `null` -- so the value really is `""`, not
 * `null`, and a naive `value ?? EMPTY` does not catch it.
 */
function cellValue(column: ReturnType<typeof buildCustomerColumns>[number], customer: Customer) {
  if (!('accessorFn' in column) || typeof column.accessorFn !== 'function') {
    throw new Error(`column "${String(column.header)}" has no accessorFn to read`)
  }
  return column.accessorFn(customer, 0)
}

const BASE_CUSTOMER: Customer = {
  id: 'c1',
  ragione_sociale: 'ACME Srl',
  partita_iva: null,
  codice_fiscale: null,
  codice_sdi: null,
  pec: null,
  indirizzo: null,
  cap: null,
  comune: null,
  provincia: null,
  nazione: 'IT',
  email: null,
  telefono: null,
  sito_web: null,
  stato: null,
  note: null,
  giorni_pagamento: null,
  pagamento_fine_mese: false,
  custom_fields: {},
  created_at: '2026-08-06T00:00:00Z',
  updated_at: '2026-08-06T00:00:00Z',
}

describe('buildCustomerColumns', () => {
  it('always shows the core commercial columns', () => {
    const headers = buildCustomerColumns([]).map((column) => column.header)
    expect(headers).toEqual(['Ragione sociale', 'P.IVA', 'Comune', 'Email', 'Telefono'])
  })

  it('appends one column per custom field', () => {
    const fields: FieldDefinition[] = [
      { key: 'settore', label: 'Settore', type: 'text', required: false, options: [] },
    ]
    const headers = buildCustomerColumns(fields).map((column) => column.header)
    expect(headers).toContain('Settore')
  })

  it('does not add columns for archived fields, which are simply not returned', () => {
    expect(buildCustomerColumns([])).toHaveLength(5)
  })

  it('renders a null native field as the empty dash', () => {
    const [, partitaIva] = buildCustomerColumns([])
    expect(cellValue(partitaIva!, { ...BASE_CUSTOMER, partita_iva: null })).toBe('—')
  })

  it('renders an explicitly-cleared ("") native field as the same dash, never a blank cell', () => {
    const [, partitaIva] = buildCustomerColumns([])
    expect(cellValue(partitaIva!, { ...BASE_CUSTOMER, partita_iva: '' })).toBe('—')
  })

  it('renders a present native field as itself', () => {
    const [, partitaIva] = buildCustomerColumns([])
    expect(cellValue(partitaIva!, { ...BASE_CUSTOMER, partita_iva: '01234567890' })).toBe(
      '01234567890',
    )
  })

  /** The table is one of the two read surfaces the "absent checkbox reads No" rule
   *  has to hold on (the detail page is the other, and goes through the same
   *  `renderFieldValue`). A record created before the field existed, or through the
   *  API, legitimately has no key at all -- and a dash there claims a third state a
   *  checkbox does not have. */
  it('renders a checkbox column as No when the record carries no value for it', () => {
    const vip: FieldDefinition = {
      key: 'vip',
      label: 'Cliente VIP',
      type: 'checkbox',
      required: false,
      options: [],
    }
    const [, , , , , vipColumn] = buildCustomerColumns([vip])
    expect(cellValue(vipColumn!, { ...BASE_CUSTOMER, custom_fields: {} })).toBe('No')
    expect(cellValue(vipColumn!, { ...BASE_CUSTOMER, custom_fields: { vip: false } })).toBe('No')
    expect(cellValue(vipColumn!, { ...BASE_CUSTOMER, custom_fields: { vip: true } })).toBe('Sì')
  })
})
