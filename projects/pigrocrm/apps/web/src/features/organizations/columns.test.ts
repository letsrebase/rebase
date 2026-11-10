import { render, screen } from '@testing-library/react'
import type { ReactElement } from 'react'
import { describe, expect, it } from 'vitest'
import { buildOrganizationColumns } from './columns'
import type { FieldDefinition } from '@/lib/schema'
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
    // An organization shared across the legal entities has none of its own (REB-970).
    expect(legal_entity.accessorFn({ ...BASE_CUSTOMER, legal_entity_id: null }, 0)).toBe('Tutte')
  })
})

describe('the columns of each registry view (the approved brief of 2026-11-10)', () => {
  const FOREIGN: Organization = {
    ...BASE_CUSTOMER,
    roles: ['supplier'],
    country: 'US',
    supplier_kind: 'foreign',
    supplier_area: 'extra_eu',
    supplier_supplies: 'services',
    supplier_payment_days: 30,
    supplier_payment_end_of_month: true,
  }

  function read(column: ReturnType<typeof buildOrganizationColumns>[number], row: Organization) {
    if (!('accessorFn' in column) || typeof column.accessorFn !== 'function') {
      throw new Error(`column "${String(column.header)}" has no accessorFn to read`)
    }
    return column.accessorFn(row, 0)
  }

  function renderCell(column: ReturnType<typeof buildOrganizationColumns>[number], row: Organization) {
    if (typeof column.cell !== 'function') throw new Error('the column has no cell renderer')
    return render(column.cell({ row: { original: row } } as never) as ReactElement)
  }

  it('Clienti is the default and keeps today\'s columns, custom fields included', () => {
    expect(buildOrganizationColumns([]).map((column) => column.header)).toEqual(
      buildOrganizationColumns([], { role: 'customer' }).map((column) => column.header),
    )
  })

  it('Fornitori: name, P.IVA, tipo fiscale and pagamento, and no «Da pagare» until due items exist', () => {
    const columns = buildOrganizationColumns([], { role: 'supplier' })
    expect(columns.map((column) => column.header)).toEqual([
      'Ragione sociale',
      'P.IVA',
      'Tipo fiscale',
      'Pagamento',
    ])
  })

  it('Fornitori does not take the customers\' custom fields', () => {
    const fields: FieldDefinition[] = [
      { key: 'settore', label: 'Settore', type: 'text', required: false, options: [] },
    ]
    expect(buildOrganizationColumns(fields, { role: 'supplier' }).map((column) => column.header)).not.toContain(
      'Settore',
    )
  })

  it('reads a foreign supplier as «Estero · extra UE · servizi» and a domestic one by its kind', () => {
    const [, , kind] = buildOrganizationColumns([], { role: 'supplier' })
    expect(read(kind!, FOREIGN)).toBe('Estero · extra UE · servizi')
    expect(read(kind!, { ...FOREIGN, supplier_area: 'eu', supplier_supplies: 'goods' })).toBe('Estero · UE · beni')
    expect(read(kind!, { ...BASE_CUSTOMER, supplier_kind: 'professional' })).toBe('Professionista')
    expect(read(kind!, { ...BASE_CUSTOMER, supplier_kind: 'flat_rate' })).toBe('Forfettario')
    expect(read(kind!, { ...BASE_CUSTOMER, supplier_kind: 'occasional' })).toBe('Occasionale')
    // Nobody has said what it is yet.
    expect(read(kind!, BASE_CUSTOMER)).toBe('—')
  })

  it('reads the terms the supplier grants as «30 gg» or «30 gg f.m.»', () => {
    const [, , , terms] = buildOrganizationColumns([], { role: 'supplier' })
    expect(read(terms!, FOREIGN)).toBe('30 gg f.m.')
    expect(read(terms!, { ...FOREIGN, supplier_payment_end_of_month: false })).toBe('30 gg')
    expect(read(terms!, { ...FOREIGN, supplier_payment_days: null })).toBe('—')
  })

  it('Tutte: name with the role chips, P.IVA, comune and email', () => {
    const columns = buildOrganizationColumns([], { role: 'any' })
    expect(columns.map((column) => column.header)).toEqual(['Ragione sociale', 'P.IVA', 'Comune', 'Email'])
    renderCell(columns[0]!, { ...BASE_CUSTOMER, roles: ['supplier', 'customer', 'freelancer'] })
    expect(screen.getAllByText(/^(Cliente|Fornitore|Freelance)$/).map((chip) => chip.textContent)).toEqual([
      'Cliente',
      'Fornitore',
      'Freelance',
    ])
  })

  it('«Azienda» follows the name on every view, from the second legal entity on', () => {
    const entities = [{ id: 'a-1', name: 'humancraft' }] as NonNullable<
      Parameters<typeof buildOrganizationColumns>[1]
    >['legal_entities']
    for (const role of ['customer', 'supplier', 'any'] as const) {
      expect(
        buildOrganizationColumns([], { role, legal_entities: entities })
          .map((column) => column.header)
          .slice(0, 2),
      ).toEqual(['Ragione sociale', 'Azienda'])
    }
  })

  it('gives the whole name as the title of the cell, which truncates it', () => {
    const name = 'Consorzio Cooperative Artigiane del Nord Est Italia e Servizi S.r.l.'
    for (const role of ['customer', 'supplier', 'any'] as const) {
      const [first] = buildOrganizationColumns([], { role })
      const { container, unmount } = renderCell(first!, { ...BASE_CUSTOMER, legal_name: name })
      expect(container.querySelector(`[title="${name}"]`)).not.toBeNull()
      expect(screen.getByText(name)).toHaveClass('truncate')
      unmount()
    }
  })
})
