import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactElement } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
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

afterEach(() => vi.restoreAllMocks())

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

  it('keeps a field typed in while the save is on its way, and takes the record in the fields it saved', async () => {
    let answer!: (response: Response) => void
    const sent: unknown[] = []
    vi.spyOn(globalThis, 'fetch').mockImplementation((_input, init) => {
      sent.push(JSON.parse(String(init!.body)))
      return new Promise<Response>((settle) => {
        answer = settle
      })
    })
    const show = mount(SAVED)
    await userEvent.click(screen.getByText('Dati fiscali'))
    const codice = screen.getByLabelText('Codice fiscale')
    const domicilio = screen.getByLabelText('Domicilio professionale')
    await userEvent.clear(codice)
    await userEvent.type(codice, 'lvldaa85t50h501z')
    await userEvent.clear(domicilio)
    await userEvent.type(domicilio, 'Corso Como 1, Milano')
    await userEvent.click(screen.getByRole('button', { name: 'Salva i dati fiscali' }))
    expect(sent).toHaveLength(1)

    // Typed while the save is on its way.
    await userEvent.clear(domicilio)
    await userEvent.type(domicilio, 'Corso Como 2, Milano')
    // What the server saved, in its own spelling, and then the page's refetch of it.
    const record = { ...SAVED, codice_fiscale: 'LVLDAA85T50H501Z', domicilio: 'Corso Como 1, Milano', updated_at: '2026-09-25T10:00:00Z' }
    answer(new Response(JSON.stringify(record), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    expect(await screen.findByText('Dati fiscali salvati.')).toBeInTheDocument()
    show(record)

    expect(domicilio).toHaveValue('Corso Como 2, Milano')
    expect(codice).toHaveValue('LVLDAA85T50H501Z')
  })

  it('keeps the saved values when an older refetch arrives after the save', async () => {
    const record = {
      ...SAVED,
      codice_fiscale: 'LVLDAA85T50H501Z',
      domicilio: 'Corso Como 1, Milano',
      updated_at: '2026-09-25T10:00:00Z',
    }
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(record), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    )
    const show = mount(SAVED)
    await userEvent.click(screen.getByText('Dati fiscali'))
    const domicilio = screen.getByLabelText('Domicilio professionale')
    await userEvent.clear(domicilio)
    await userEvent.type(domicilio, 'corso como 1, milano ')
    await userEvent.click(screen.getByRole('button', { name: 'Salva i dati fiscali' }))
    expect(await screen.findByText('Dati fiscali salvati.')).toBeInTheDocument()
    // The record the save answered fills the form at once, in the server's spelling.
    expect(domicilio).toHaveValue('Corso Como 1, Milano')

    // A refetch older than the save, with values of its own.
    show({ ...SAVED, domicilio: 'Via Vecchia 9, Milano', updated_at: '2026-09-24T08:00:00Z' })
    expect(domicilio).toHaveValue('Corso Como 1, Milano')
    expect(screen.getByLabelText('Partita IVA')).toHaveValue('01234567890')

    // A newer one still does.
    show({ ...record, partita_iva: '01234567899', updated_at: '2026-09-26T08:00:00Z' })
    expect(screen.getByLabelText('Partita IVA')).toHaveValue('01234567899')
  })

  it('fills the form once the first record is saved elsewhere', () => {
    const show = mount(null)
    expect(screen.getByLabelText('Codice fiscale')).toHaveValue('')
    show(SAVED)
    expect(screen.getByLabelText('Codice fiscale')).toHaveValue('LVLDAA85T50H501Z')
  })
})
