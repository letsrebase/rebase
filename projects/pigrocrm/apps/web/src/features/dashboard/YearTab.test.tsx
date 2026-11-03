import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { downloadFromApi } from '@/lib/download'
import { LegalEntityContext, type LegalEntityValue } from '@/lib/legalEntity'
import { YearTab } from './YearTab'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn() } }
})

vi.mock('@/lib/download', () => ({ downloadFromApi: vi.fn().mockResolvedValue(undefined) }))

vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, children }: { to: string; children: React.ReactNode }) => <a href={to}>{children}</a>,
}))

const REFERENCE =
  'Operazione senza applicazione dell’IVA ai sensi dell’art. 1, commi 54-89, L. 190/2014 - regime forfettario'
const STAMP_DUTY = 'Imposta di bollo di 2,00 € assolta in modo virtuale a carico dell’emittente.'

const overview = (overrides: Record<string, unknown> = {}) => ({
  year: 2026,
  legal_entity_id: 'a1',
  legal_entity_name: 'Studio Rossi',
  total: {
    invoices: 2,
    taxable_amount: '3580.00',
    social_security_surcharge: '80.00',
    stamp_duty: '2.00',
    total: '3580.00',
  },
  regimes: [{ regime_code: 'RF19', pack_id: 'it-flat-rate', invoices: 2, revenue: '3580.00' }],
  treatments: [
    {
      vat_rate: '0.00',
      vat_nature: 'N2.2',
      legal_reference: REFERENCE,
      lines: 3,
      revenue: '3580.00',
    },
  ],
  register: [
    {
      invoice_id: 'i1',
      year: 2026,
      number: 1,
      issue_date: '2026-03-10',
      customer_id: 'c1',
      legal_name: 'Acme S.r.l.',
      vat_number: '01234567890',
      sdi_document_type: 'TD01',
      taxable_amount: '2080.00',
      social_security_surcharge: '80.00',
      stamp_duty: '2.00',
      total: '2080.00',
      stamp_duty_declaration: STAMP_DUTY,
      payment_status: 'paid',
      payment_date: '2026-04-02',
    },
    {
      invoice_id: 'i2',
      year: 2026,
      number: 2,
      issue_date: '2026-05-04',
      customer_id: 'c2',
      legal_name: 'Beta SpA',
      vat_number: null,
      sdi_document_type: 'TD01',
      taxable_amount: '1500.00',
      social_security_surcharge: '0.00',
      stamp_duty: '0.00',
      total: '1500.00',
      stamp_duty_declaration: null,
      payment_status: 'unpaid',
      payment_date: null,
    },
  ],
  cancelled: 0,
  ...overrides,
})

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) }) as never
}

const ONE: LegalEntityValue = {
  legal_entities: [],
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
        <YearTab period={{ from: '2026-01-01', to: '2026-12-31' }} />
      </LegalEntityContext>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(downloadFromApi).mockClear()
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
    expect(api.GET).toHaveBeenCalledWith('/api/analytics/year', { params: { query: { year: 2026 } } })
  })

  it('prints the legal reference and the stamp-duty sentence exactly as stored', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(overview()))
    renderTab()

    expect(await screen.findByText(REFERENCE)).toBeInTheDocument()
    // Once, with how many invoices carry it, not once per row.
    expect(screen.getByText(`«${STAMP_DUTY}»`, { exact: false })).toBeInTheDocument()
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

  it('downloads the CSV of the year through the API', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(overview()))
    renderTab()

    await userEvent.click(
      await screen.findByRole('button', { name: /scarica il csv per il commercialista/i }),
    )
    expect(downloadFromApi).toHaveBeenCalledWith('/api/analytics/year/csv?year=2026')
  })

  it('says a year with nothing issued has nothing, and still offers the file', async () => {
    vi.mocked(api.GET).mockImplementation(() =>
      ok(
        overview({
          total: { invoices: 0, taxable_amount: '0.00', social_security_surcharge: '0.00', stamp_duty: '0.00', total: '0.00' },
          regimes: [],
          treatments: [],
          register: [],
        }),
      ),
    )
    renderTab()

    expect(await screen.findByText('Nessuna fattura emessa nel 2026.')).toBeInTheDocument()
    expect(screen.queryByText('Ricavi per regime')).not.toBeInTheDocument()
  })

  it('mentions the invoices struck from the register, which no figure counts', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(overview({ cancelled: 2 })))
    renderTab()

    expect(await screen.findByText(/2 fatture annullate restano fuori dai totali/)).toBeInTheDocument()
  })

  it('draws one block, and one file, per azienda in «Tutte»', async () => {
    const legal_entities = [
      { id: 'a1', name: 'Studio Rossi' },
      { id: 'a2', name: 'Rossi Ltd' },
    ] as LegalEntityValue['legal_entities']
    vi.mocked(api.GET).mockImplementation(((_path: string, init: { params: { query: { legal_entity_id?: string } } }) =>
      ok(
        init.params.query.legal_entity_id === 'a1'
          ? overview()
          : overview({
              legal_entity_id: 'a2',
              legal_entity_name: 'Rossi Ltd',
              register: [{ ...overview().register[1], invoice_id: 'i9' }],
            }),
      )) as never)
    renderTab({ ...ONE, legal_entities, several: true, selected: null })

    await waitFor(() =>
      expect(screen.getAllByRole('button', { name: /scarica il csv/i })).toHaveLength(2),
    )
    for (const button of screen.getAllByRole('button', { name: /scarica il csv/i })) {
      await userEvent.click(button)
    }
    expect(vi.mocked(downloadFromApi).mock.calls.map(([path]) => path)).toEqual([
      '/api/analytics/year/csv?year=2026&legal_entity_id=a1',
      '/api/analytics/year/csv?year=2026&legal_entity_id=a2',
    ])
    expect(screen.getByText('Studio Rossi')).toBeInTheDocument()
    expect(screen.getByText('Rossi Ltd')).toBeInTheDocument()
  })

  it('names an azienda with nothing issued instead of leaving the tab blank in «Tutte»', async () => {
    const legal_entities = [
      { id: 'a1', name: 'Studio Rossi' },
      { id: 'a2', name: 'Rossi Ltd' },
    ] as LegalEntityValue['legal_entities']
    vi.mocked(api.GET).mockImplementation(() =>
      ok(
        overview({
          total: { invoices: 0, taxable_amount: '0.00', social_security_surcharge: '0.00', stamp_duty: '0.00', total: '0.00' },
          regimes: [],
          treatments: [],
          register: [],
        }),
      ),
    )
    renderTab({ ...ONE, legal_entities, several: true, selected: null })

    await waitFor(() => expect(screen.getAllByText('Nessuna fattura emessa nel 2026.')).toHaveLength(2))
    expect(screen.getByText('Studio Rossi')).toBeInTheDocument()
    expect(screen.getByText('Rossi Ltd')).toBeInTheDocument()
  })

  it('names the azienda the sidebar selected in the file it offers', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(overview()))
    renderTab({ ...ONE, several: true, selected: 'a2' })

    await userEvent.click(await screen.findByRole('button', { name: /scarica il csv/i }))
    expect(downloadFromApi).toHaveBeenCalledWith('/api/analytics/year/csv?year=2026&legal_entity_id=a2')
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
