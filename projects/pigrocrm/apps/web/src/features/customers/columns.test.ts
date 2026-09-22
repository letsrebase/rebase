import { render, screen } from '@testing-library/react'
import type { ReactElement } from 'react'
import { describe, expect, it } from 'vitest'
import { buildCustomerColumns } from './columns'
import type { Customer } from './queries'

/**
 * Where a customer column test belongs -- the other five features each have their own
 * `columns.test.ts`. The older assertions on `buildCustomerColumns` still live in
 * `queries.test.ts`, which is where this feature first put them; they are not moved
 * here in the same breath as adding these, because moving a green file's contents and
 * changing behaviour in one commit hides which of the two broke something.
 */
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

/**
 * The first cell of a customer's row: the row *is* a company, so it carries its
 * identity as an initials chip beside the name (design spec §4). The accessor stays
 * the bare `ragione_sociale`, which is what the tests above read.
 */
describe('the customer as an entity in the first cell', () => {
  function renderName(customer: Customer) {
    const column = buildCustomerColumns([])[0]
    if (column === undefined || typeof column.cell !== 'function') {
      throw new Error('la colonna «Ragione sociale» non ha un cell renderer')
    }
    return render(column.cell({ row: { original: customer } } as never) as ReactElement)
  }

  it('shows the ragione sociale beside a chip of its initials', () => {
    renderName({ ...BASE_CUSTOMER, ragione_sociale: 'ACME Srl' })
    expect(screen.getByText('ACME Srl')).toBeInTheDocument()
    expect(screen.getByText('AS')).toBeInTheDocument()
  })

  it('keeps the chip to two letters however long the company name is', () => {
    renderName({ ...BASE_CUSTOMER, ragione_sociale: 'Prima Società Benefit Srl' })
    expect(screen.getByText('PS')).toBeInTheDocument()
  })
})
