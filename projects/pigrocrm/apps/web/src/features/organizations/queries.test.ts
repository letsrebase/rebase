import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { describe, expect, it } from 'vitest'
import { queryKeys } from '@/lib/query'
import { buildOrganizationColumns } from './columns'
import type { FieldDefinition } from '@/lib/schema'
import { useOrganizationsByVat, type Organization } from './queries'

/**
 * Reads a native column's cell value the same way `DataTable` does internally --
 * through the column's own `accessorFn` -- for the two columns below that carry
 * one. Verified live in a browser first (see task-6-report.md): after using
 * `OrganizationForm`'s edit dialog to clear a previously-set "Telefono", the detail
 * page rendered a blank cell instead of the dash every other absent value gets,
 * because `OrganizationUpdate.model_dump(exclude_none=True)` only clears a native
 * column on an explicit `""`, never `null` -- so the value really is `""`, not
 * `null`, and a naive `value ?? EMPTY` does not catch it.
 */
function cellValue(column: ReturnType<typeof buildOrganizationColumns>[number], customer: Organization) {
  if (!('accessorFn' in column) || typeof column.accessorFn !== 'function') {
    throw new Error(`column "${String(column.header)}" has no accessorFn to read`)
  }
  return column.accessorFn(customer, 0)
}

const BASE_CUSTOMER: Organization = {
  id: 'c1',
  legal_entity_id: 'a-1',
  owner_id: null,
  legal_name: 'ACME Srl',
  vat_number: null,
  tax_code: null,
  sdi_code: null,
  certified_email: null,
  address: null,
  postal_code: null,
  city: null,
  province: null,
  country: 'IT',
  email: null,
  phone: null,
  contact_points: [],
  website: null,
  status: null,
  payment_days: null,
  payment_end_of_month: false,
  supplier_payment_days: null,
  supplier_payment_end_of_month: false,
  roles: ['customer'],
  supplier_kind: null,
  supplier_area: null,
  supplier_supplies: null,
  customer_kind: null,
  is_withholding_agent: false,
  hub_freelancer_id: null,
  custom_fields: {},
  created_at: '2026-08-06T00:00:00Z',
  updated_at: '2026-08-06T00:00:00Z',
}

describe('buildOrganizationColumns', () => {
  it('always shows the core commercial columns', () => {
    const headers = buildOrganizationColumns([]).map((column) => column.header)
    expect(headers).toEqual(['Ragione sociale', 'P.IVA', 'Comune', 'Email', 'Telefono'])
  })

  it('appends one column per custom field', () => {
    const fields: FieldDefinition[] = [
      { key: 'settore', label: 'Settore', type: 'text', required: false, options: [] },
    ]
    const headers = buildOrganizationColumns(fields).map((column) => column.header)
    expect(headers).toContain('Settore')
  })

  it('does not add columns for archived fields, which are simply not returned', () => {
    expect(buildOrganizationColumns([])).toHaveLength(5)
  })

  it('renders a null native field as the empty dash', () => {
    const [, vatNumber] = buildOrganizationColumns([])
    expect(cellValue(vatNumber!, { ...BASE_CUSTOMER, vat_number: null })).toBe('—')
  })

  it('renders an explicitly-cleared ("") native field as the same dash, never a blank cell', () => {
    const [, vatNumber] = buildOrganizationColumns([])
    expect(cellValue(vatNumber!, { ...BASE_CUSTOMER, vat_number: '' })).toBe('—')
  })

  it('renders a present native field as itself', () => {
    const [, vatNumber] = buildOrganizationColumns([])
    expect(cellValue(vatNumber!, { ...BASE_CUSTOMER, vat_number: '01234567890' })).toBe(
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
    const [, , , , , vipColumn] = buildOrganizationColumns([vip])
    expect(cellValue(vipColumn!, { ...BASE_CUSTOMER, custom_fields: {} })).toBe('No')
    expect(cellValue(vipColumn!, { ...BASE_CUSTOMER, custom_fields: { vip: false } })).toBe('No')
    expect(cellValue(vipColumn!, { ...BASE_CUSTOMER, custom_fields: { vip: true } })).toBe('Sì')
  })
})

describe('useOrganizationsByVat', () => {
  it('is not refetched by the writes that invalidate the organizations, so a saved form never finds itself', () => {
    // Saving the form refetched this lookup while the dialog was still closing and it found the
    // record just created: «Esiste già: <itself>» (the e2e suite caught it).
    const client = new QueryClient()
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client }, children)
    renderHook(() => useOrganizationsByVat('12345678901', false), { wrapper })

    void client.invalidateQueries({ queryKey: queryKeys.organizations() })

    const lookup = client.getQueryCache().find({ queryKey: ['organization-vat-lookup', '12345678901'] })
    expect(lookup).toBeDefined()
    expect(lookup?.state.isInvalidated).toBe(false)
  })
})
