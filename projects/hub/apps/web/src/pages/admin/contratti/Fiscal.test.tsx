import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactElement } from 'react'
import { describe, expect, it } from 'vitest'
import type { Fiscal } from '@/lib/api'
import { FiscalSection } from './Fiscal'

const SAVED: Fiscal = {
  freelancer_id: 'f1',
  codice_fiscale: 'LVLDAA85T50H501Z',
  partita_iva: '01234567890',
  domicilio: 'Via Roma 1, Milano',
  pec: null,
  updated_by: 'a1',
  updated_at: '2026-09-23T10:00:00Z',
}
// The same record saved again elsewhere, by another admin or another tab.
const NEWER: Fiscal = {
  ...SAVED,
  partita_iva: '01234567899',
  domicilio: 'Via Po 2, 10121 Torino',
  updated_at: '2026-09-25T09:00:00Z',
}

function mount(fiscale: Fiscal | null) {
  const client = new QueryClient()
  const section = (value: Fiscal | null): ReactElement => (
    <QueryClientProvider client={client}>
      <FiscalSection freelancerId="f1" fiscale={value} onSaving={() => {}} onSaved={() => {}} />
    </QueryClientProvider>
  )
  const { rerender } = render(section(fiscale))
  return (value: Fiscal | null) => rerender(section(value))
}

describe('«Dati fiscali» on «Match e contratti»', () => {
  it('fills a form nobody typed in with the newer saved record', () => {
    const show = mount(SAVED)
    expect(screen.getByLabelText('Domicilio professionale')).toHaveValue('Via Roma 1, Milano')
    show(NEWER)
    expect(screen.getByLabelText('Domicilio professionale')).toHaveValue('Via Po 2, 10121 Torino')
    expect(screen.getByLabelText('Partita IVA')).toHaveValue('01234567899')
  })

  it('keeps what the admin is typing, and takes the newer record in the fields left alone', async () => {
    const show = mount(SAVED)
    await userEvent.click(screen.getByText('Dati fiscali'))
    const domicilio = screen.getByLabelText('Domicilio professionale')
    await userEvent.clear(domicilio)
    await userEvent.type(domicilio, 'Via Nuova 3, Bari')
    show(NEWER)
    expect(screen.getByLabelText('Domicilio professionale')).toHaveValue('Via Nuova 3, Bari')
    expect(screen.getByLabelText('Partita IVA')).toHaveValue('01234567899')
  })

  it('fills the form once the first record is saved elsewhere', () => {
    const show = mount(null)
    expect(screen.getByLabelText('Codice fiscale')).toHaveValue('')
    show(SAVED)
    expect(screen.getByLabelText('Codice fiscale')).toHaveValue('LVLDAA85T50H501Z')
  })
})
