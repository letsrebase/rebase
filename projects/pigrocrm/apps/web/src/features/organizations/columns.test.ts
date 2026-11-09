import { render, screen } from '@testing-library/react'
import type { ReactElement } from 'react'
import { describe, expect, it } from 'vitest'
import { buildOrganizationColumns } from './columns'
import type { Organization } from './queries'

/**
 * Where a customer column test belongs -- the other five features each have their own
 * `columns.test.ts`. The older assertions on `buildOrganizationColumns` still live in
 * `queries.test.ts`, which is where this feature first put them; they are not moved
 * here in the same breath as adding these, because moving a green file's contents and
 * changing behaviour in one commit hides which of the two broke something.
 */
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
  custom_fields: {},
  created_at: '2026-08-06T00:00:00Z',
  updated_at: '2026-08-06T00:00:00Z',
}

/**
 * The first cell of a customer's row: the row *is* a company, so it carries its
 * identity as an initials chip beside the name (design spec §4). The accessor stays
 * the bare `legal_name`, which is what the tests above read.
 */
describe('the customer as an entity in the first cell', () => {
  function renderName(customer: Organization) {
    const column = buildOrganizationColumns([])[0]
    if (column === undefined || typeof column.cell !== 'function') {
      throw new Error('la colonna «Ragione sociale» non ha un cell renderer')
    }
    return render(column.cell({ row: { original: customer } } as never) as ReactElement)
  }

  it('shows the ragione sociale beside a chip of its initials', () => {
    renderName({ ...BASE_CUSTOMER, legal_name: 'ACME Srl' })
    expect(screen.getByText('ACME Srl')).toBeInTheDocument()
    expect(screen.getByText('AS')).toBeInTheDocument()
  })

  it('keeps the chip to two letters however long the company name is', () => {
    renderName({ ...BASE_CUSTOMER, legal_name: 'Prima Società Benefit Srl' })
    expect(screen.getByText('PS')).toBeInTheDocument()
  })
})

describe('the «Azienda» column (REB-625)', () => {
  const LEGAL_ENTITIES = [
    { id: 'a-1', name: 'humancraft' },
    { id: 'a-2', name: 'rebase' },
  ] as NonNullable<Parameters<typeof buildOrganizationColumns>[1]>['legal_entities']

  it('is absent without the option, so a one-legal-entity space keeps its five columns', () => {
    const headers = buildOrganizationColumns([]).map((column) => column.header)
    expect(headers).not.toContain('Azienda')
  })

  it('follows the name under «tutte» and reads each row\'s azienda by id', () => {
    const columns = buildOrganizationColumns([], { legal_entities: LEGAL_ENTITIES })
    expect(columns.map((column) => column.header).slice(0, 3)).toEqual([
      'Ragione sociale',
      'Azienda',
      'P.IVA',
    ])
    const legal_entity = columns[1]
    if (legal_entity === undefined || !('accessorFn' in legal_entity) || legal_entity.accessorFn === undefined) {
      throw new Error('the «Azienda» column has no accessorFn')
    }
    expect(legal_entity.accessorFn({ ...BASE_CUSTOMER, legal_entity_id: 'a-2' }, 0)).toBe('rebase')
    expect(legal_entity.accessorFn({ ...BASE_CUSTOMER, legal_entity_id: 'gone' }, 0)).toBe('—')
  })
})
