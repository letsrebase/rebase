import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { LegalEntityContext, type LegalEntityValue } from '@/lib/legalEntity'
import { YearTab } from './YearTab'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn() } }
})

vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, children }: { to: string; children: React.ReactNode }) => <a href={to}>{children}</a>,
}))

const RIFERIMENTO =
  'Operazione senza applicazione dell’IVA ai sensi dell’art. 1, commi 54-89, L. 190/2014 - regime forfettario'
const BOLLO = 'Imposta di bollo di 2,00 € assolta in modo virtuale a carico dell’emittente.'

const overview = (overrides: Record<string, unknown> = {}) => ({
  anno: 2026,
  azienda_id: 'a1',
  azienda_nome: 'Studio Rossi',
  totali: {
    fatture: 2,
    imponibile: '3580.00',
    rivalsa: '80.00',
    bollo: '2.00',
    totale: '3580.00',
  },
  regimi: [{ codice_regime: 'RF19', pack_id: 'it-flat-rate', fatture: 2, ricavi: '3580.00' }],
  trattamenti: [
    {
      aliquota_iva: '0.00',
      natura: 'N2.2',
      riferimento_normativo: RIFERIMENTO,
      righe: 3,
      ricavi: '3580.00',
    },
  ],
  registro: [
    {
      invoice_id: 'i1',
      anno: 2026,
      numero: 1,
      data_emissione: '2026-03-10',
      customer_id: 'c1',
      ragione_sociale: 'Acme S.r.l.',
      partita_iva: '01234567890',
      tipo_documento: 'TD01',
      imponibile: '2080.00',
      rivalsa: '80.00',
      bollo: '2.00',
      totale: '2080.00',
      dichiarazione_bollo: BOLLO,
      stato_pagamento: 'incassato',
      data_incasso: '2026-04-02',
    },
    {
      invoice_id: 'i2',
      anno: 2026,
      numero: 2,
      data_emissione: '2026-05-04',
      customer_id: 'c2',
      ragione_sociale: 'Beta SpA',
      partita_iva: null,
      tipo_documento: 'TD01',
      imponibile: '1500.00',
      rivalsa: '0.00',
      bollo: '0.00',
      totale: '1500.00',
      dichiarazione_bollo: null,
      stato_pagamento: 'da_incassare',
      data_incasso: null,
    },
  ],
  annullate: 0,
  ...overrides,
})

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) }) as never
}

const ONE: LegalEntityValue = {
  aziende: [],
  selected: null,
  select: () => {},
  several: false,
  scoped: false,
  pinned: false,
  byId: () => undefined,
}

function renderTab(legalEntity: LegalEntityValue = ONE) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <LegalEntityContext value={legalEntity}>
        <YearTab periodo={{ da: '2026-01-01', a: '2026-12-31' }} />
      </LegalEntityContext>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
})

describe('YearTab', () => {
  it('shows the year\'s revenue, the rivalsa inside it and the stamp duty outside it', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(overview()))
    renderTab()

    expect(
      within(await screen.findByRole('group', { name: 'Ricavi dell’anno' })).getByText('3.580,00 €'),
    ).toBeInTheDocument()
    expect(
      within(screen.getByRole('group', { name: 'di cui rivalsa INPS' })).getByText('80,00 €'),
    ).toBeInTheDocument()
    expect(
      within(screen.getByRole('group', { name: 'Bollo virtuale' })).getByText('2,00 €'),
    ).toBeInTheDocument()
    expect(api.GET).toHaveBeenCalledWith('/api/analytics/year', { params: { query: { anno: 2026 } } })
  })

  it('prints the legal reference and the stamp-duty sentence exactly as stored', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(overview()))
    renderTab()

    expect(await screen.findByText(RIFERIMENTO)).toBeInTheDocument()
    // Once, with how many invoices carry it, not once per row.
    expect(screen.getByText(`«${BOLLO}»`, { exact: false })).toBeInTheDocument()
    expect(screen.getByText('(1 fattura)')).toBeInTheDocument()
    expect(screen.getByText('N2.2')).toBeInTheDocument()
  })

  it('names the regime in the settings screen\'s words', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(overview()))
    renderTab()

    const table = (await screen.findByText('Ricavi per regime')).closest('section')!
    expect(within(table).getByText('Forfettario (RF19)')).toBeInTheDocument()
    expect(within(table).getByText('3.580,00 €')).toBeInTheDocument()
  })

  it('lists the register with its total, and links each number to its invoice', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(overview()))
    renderTab()

    const register = (await screen.findByText('Registro delle fatture emesse')).closest('section')!
    expect(within(register).getByRole('link', { name: '2026/1' })).toHaveAttribute(
      'href',
      '/app/invoices/$invoiceId',
    )
    expect(within(register).getByText('Acme S.r.l.')).toBeInTheDocument()
    expect(within(register).getByText('P.IVA 01234567890')).toBeInTheDocument()
    expect(within(register).getByText('Incassata il 02/04/2026')).toBeInTheDocument()
    expect(within(register).getByText('Da incassare')).toBeInTheDocument()
    const total = within(register).getByText(/Totale registro · 2 fatture/).closest('tr')!
    // Under a forfettario the document total is the taxable amount: both columns say so.
    expect(within(total).getAllByText('3.580,00 €')).toHaveLength(2)
    expect(within(total).getByText('80,00 €')).toBeInTheDocument()
  })

  it('offers the CSV of the year, from the space\'s own API path', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(overview()))
    renderTab()

    const link = await screen.findByRole('link', { name: /scarica il csv per il commercialista/i })
    expect(link).toHaveAttribute('href', '/api/analytics/year/csv?anno=2026')
  })

  it('says a year with nothing issued has nothing, and still offers the file', async () => {
    vi.mocked(api.GET).mockImplementation(() =>
      ok(
        overview({
          totali: { fatture: 0, imponibile: '0.00', rivalsa: '0.00', bollo: '0.00', totale: '0.00' },
          regimi: [],
          trattamenti: [],
          registro: [],
        }),
      ),
    )
    renderTab()

    expect(await screen.findByText('Nessuna fattura emessa nel 2026.')).toBeInTheDocument()
    expect(screen.queryByText('Ricavi per regime')).not.toBeInTheDocument()
  })

  it('mentions the invoices struck from the register, which no figure counts', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(overview({ annullate: 2 })))
    renderTab()

    expect(await screen.findByText(/2 fatture annullate restano fuori dai totali/)).toBeInTheDocument()
  })

  it('draws one block, and one file, per azienda in «Tutte»', async () => {
    const aziende = [
      { id: 'a1', nome: 'Studio Rossi' },
      { id: 'a2', nome: 'Rossi Ltd' },
    ] as LegalEntityValue['aziende']
    vi.mocked(api.GET).mockImplementation(((_path: string, init: { params: { query: { azienda_id?: string } } }) =>
      ok(
        init.params.query.azienda_id === 'a1'
          ? overview()
          : overview({
              azienda_id: 'a2',
              azienda_nome: 'Rossi Ltd',
              registro: [{ ...overview().registro[1], invoice_id: 'i9' }],
            }),
      )) as never)
    renderTab({ ...ONE, aziende, several: true, selected: null })

    await waitFor(() => expect(screen.getAllByRole('link', { name: /scarica il csv/i })).toHaveLength(2))
    const hrefs = screen
      .getAllByRole('link', { name: /scarica il csv/i })
      .map((link) => link.getAttribute('href'))
    expect(hrefs).toEqual([
      '/api/analytics/year/csv?anno=2026&azienda_id=a1',
      '/api/analytics/year/csv?anno=2026&azienda_id=a2',
    ])
    expect(screen.getByText('Studio Rossi')).toBeInTheDocument()
    expect(screen.getByText('Rossi Ltd')).toBeInTheDocument()
  })

  it('names an azienda with nothing issued instead of leaving the tab blank in «Tutte»', async () => {
    const aziende = [
      { id: 'a1', nome: 'Studio Rossi' },
      { id: 'a2', nome: 'Rossi Ltd' },
    ] as LegalEntityValue['aziende']
    vi.mocked(api.GET).mockImplementation(() =>
      ok(
        overview({
          totali: { fatture: 0, imponibile: '0.00', rivalsa: '0.00', bollo: '0.00', totale: '0.00' },
          regimi: [],
          trattamenti: [],
          registro: [],
        }),
      ),
    )
    renderTab({ ...ONE, aziende, several: true, selected: null })

    await waitFor(() => expect(screen.getAllByText('Nessuna fattura emessa nel 2026.')).toHaveLength(2))
    expect(screen.getByText('Studio Rossi')).toBeInTheDocument()
    expect(screen.getByText('Rossi Ltd')).toBeInTheDocument()
  })

  it('names the azienda the sidebar selected in the file it offers', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(overview()))
    renderTab({ ...ONE, several: true, selected: 'a2' })

    const link = await screen.findByRole('link', { name: /scarica il csv/i })
    expect(link).toHaveAttribute('href', '/api/analytics/year/csv?anno=2026&azienda_id=a2')
  })

  it('shows the server\'s own words when the year cannot be read', async () => {
    vi.mocked(api.GET).mockImplementation(
      () =>
        Promise.resolve({
          error: { detail: 'Servizio non disponibile' },
          response: new Response(null, { status: 500 }),
        }) as never,
    )
    renderTab()

    expect(await screen.findByText(/servizio non disponibile/i)).toBeInTheDocument()
  })
})
