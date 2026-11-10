import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { ok, refused, renderWithQuery, viewport } from '@/features/inbox/testing'
import { PurchasesPage } from './PurchasesPage'
import type { PurchasesSearch } from './search'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn() } }
})

const navigate = vi.fn()
let search: PurchasesSearch = {}
let writer = true

vi.mock('@tanstack/react-router', () => ({
  Link: ({
    to,
    params,
    children,
    className,
  }: {
    to: string
    params?: Record<string, string>
    children: React.ReactNode
    className?: string
  }) => (
    <a className={className} href={params ? to.replace(/\$(\w+)/g, (_, key: string) => params[key] ?? '') : to}>
      {children}
    </a>
  ),
  useNavigate: () => navigate,
  useSearch: () => search,
}))

vi.mock('@/lib/auth', () => ({
  useCanWrite: () => writer,
  useAuth: () => ({ user: { id: 'u1', role: writer ? 'admin' : 'readonly' }, isLoading: false }),
}))

const CATEGORY = {
  id: '0199aaaa-0000-7000-8000-0000000000c1',
  name: 'Consulenze',
  position: 0,
  code: null,
  archived: false,
  created_at: 'x',
  updated_at: 'x',
}

function purchase(overrides: Record<string, unknown> = {}) {
  return {
    id: 'p1',
    kind: 'invoice',
    status: 'registered',
    legal_entity_id: null,
    deal_id: null,
    category_id: CATEGORY.id,
    date: '2026-10-28',
    amount: '1464.00',
    description: '',
    supplier_id: null,
    merchant_name: 'Studio Rossi',
    number: '14/E',
    received_on: null,
    iva_period: null,
    currency: 'EUR',
    currency_amount: null,
    exchange_rate: null,
    exchange_rate_date: null,
    cost_amount: '1200.00',
    vat_lines: [],
    stamp_duty: null,
    withholding_rate: null,
    withholding_base: null,
    withholding_amount: null,
    fund_rate: null,
    fund_amount: null,
    split_payment: false,
    reverse_charge: false,
    corrects_purchase_id: null,
    inbox_item_id: null,
    document_id: null,
    custom_fields: {},
    created_at: '2026-10-28T00:00:00Z',
    updated_at: '2026-10-28T00:00:00Z',
    ...overrides,
  }
}

const SCHEMA = { entity_type: 'purchase', native_fields: [], custom_fields: [] }
const ORGS = { items: [], next_cursor: null }

/** Answers each path; `purchases` is what `/api/purchases` says, in order, the last one repeated. */
function answering(...purchases: unknown[]) {
  let call = 0
  vi.mocked(api.GET).mockImplementation(((path: string) => {
    if (path === '/api/purchases') return Promise.resolve(purchases[Math.min(call++, purchases.length - 1)])
    if (path === '/api/cost-categories') return Promise.resolve(ok([CATEGORY]))
    if (path === '/api/schema/{entity_type}') return Promise.resolve(ok(SCHEMA))
    return Promise.resolve(ok(ORGS))
  }) as never)
}

const page = (items: unknown[], extra: Record<string, unknown> = {}) =>
  ok({ items, next_cursor: null, total_count: items.length, total_amount: '1464.00', ...extra })

function purchaseCalls() {
  const calls = vi.mocked(api.GET).mock.calls as unknown as [
    string,
    { params: { query: Record<string, unknown> } },
  ][]
  return calls.filter(([path]) => path === '/api/purchases')
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  navigate.mockReset()
  search = {}
  writer = true
  viewport(1280)
})

describe('«Acquisti» on a desk', () => {
  it('draws the header, the strip with the count and the signature cell', async () => {
    answering(page([purchase()], { total_count: 212, next_cursor: 'c2', total_amount: '98765.40' }))
    renderWithQuery(<PurchasesPage />)
    expect(await screen.findByRole('heading', { level: 1, name: 'Acquisti' })).toBeInTheDocument()
    expect(await screen.findByText('Studio Rossi')).toBeInTheDocument()
    expect(screen.getByText('Fattura 14/E')).toBeInTheDocument()
    expect(screen.getByText('Registrati nel periodo')).toBeInTheDocument()
    expect(screen.getByText(/98\.765,40/)).toBeInTheDocument()
    expect(screen.getByText('212 acquisti')).toBeInTheDocument()
    // Due items are not built: nothing about paying.
    expect(screen.queryByText(/da pagare|scaduti|pagamento|scadenza/i)).not.toBeInTheDocument()
  })

  it('asks only for the registered purchases, 50 at a time, and shows «Mostra altri» with the total', async () => {
    answering(page([purchase()], { total_count: 212, next_cursor: 'c2' }))
    renderWithQuery(<PurchasesPage />)
    expect(await screen.findByRole('button', { name: 'Mostra altri (212 in tutto)' })).toBeInTheDocument()
    expect(purchaseCalls()[0]?.[1].params.query).toMatchObject({ status: 'registered', limit: 50 })
  })

  it('follows the cursor when «Mostra altri» is pressed', async () => {
    answering(
      page([purchase()], { total_count: 2, next_cursor: 'c2' }),
      page([purchase({ id: 'p2', merchant_name: 'Bianchi Srl' })], { total_count: 2 }),
    )
    renderWithQuery(<PurchasesPage />)
    await userEvent.click(await screen.findByRole('button', { name: /Mostra altri/ }))
    expect(await screen.findByText('Bianchi Srl')).toBeInTheDocument()
    expect(purchaseCalls()[1]?.[1].params.query).toMatchObject({ cursor: 'c2' })
    expect(screen.queryByRole('button', { name: /Mostra altri/ })).not.toBeInTheDocument()
  })

  it('opens a purchase on its own page when the row is pressed', async () => {
    answering(page([purchase()]))
    renderWithQuery(<PurchasesPage />)
    await userEvent.click(await screen.findByText('Studio Rossi'))
    expect(navigate).toHaveBeenCalledWith({ to: '/app/purchases/$purchaseId', params: { purchaseId: 'p1' } })
  })

  it('says the empty sentence with «Nuovo acquisto» for a writer', async () => {
    answering(page([], { total_amount: '0.00' }))
    renderWithQuery(<PurchasesPage />)
    expect(
      await screen.findByText(
        'Nessun acquisto. Qui compaiono le fatture dei fornitori, gli scontrini e le spese: carica un documento o registra un acquisto.',
      ),
    ).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: /Nuovo acquisto/ }).length).toBeGreaterThan(0)
  })

  it('waits with «Leggo gli acquisti…» and no strip', async () => {
    vi.mocked(api.GET).mockImplementation((() => new Promise(() => {})) as never)
    renderWithQuery(<PurchasesPage />)
    expect(await screen.findByRole('status', { name: 'Leggo gli acquisti…' })).toBeInTheDocument()
    expect(screen.queryByText('Registrati nel periodo')).not.toBeInTheDocument()
  })

  it('shows the server error with «Riprova», and no strip and no list', async () => {
    vi.mocked(api.GET).mockImplementation(((path: string) =>
      Promise.resolve(
        path === '/api/purchases'
          ? refused(500, { title: 'Errore', detail: 'Il server non risponde.' })
          : path === '/api/cost-categories'
            ? ok([])
            : ok(SCHEMA),
      )) as never)
    renderWithQuery(<PurchasesPage />)
    expect(await screen.findByText('Il server non risponde.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Riprova' })).toBeInTheDocument()
    expect(screen.queryByText('Registrati nel periodo')).not.toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('offers no «Nuovo acquisto» to a read-only member', async () => {
    writer = false
    answering(page([purchase()]))
    renderWithQuery(<PurchasesPage />)
    await screen.findByText('Studio Rossi')
    expect(screen.queryByRole('link', { name: /Nuovo acquisto/ })).not.toBeInTheDocument()
  })

  it('links «Nuovo acquisto» to the form page for a writer', async () => {
    answering(page([purchase()]))
    renderWithQuery(<PurchasesPage />)
    await screen.findByText('Studio Rossi')
    expect(screen.getByRole('link', { name: /Nuovo acquisto/ })).toHaveAttribute('href', '/app/purchases/new')
  })

  it('hides the cancelled ones by default and shows them, muted, under «Annullati»', async () => {
    search = { cancelled: true }
    answering(page([purchase({ status: 'cancelled' })], { total_amount: '1464.00' }))
    renderWithQuery(<PurchasesPage />)
    expect(await screen.findByText('Annullato')).toHaveAttribute('data-tone', 'muted')
    expect(screen.getByRole('button', { name: 'Annullati' })).toHaveAttribute('aria-pressed', 'true')
    expect(purchaseCalls()[0]?.[1].params.query).toMatchObject({ status: 'cancelled' })
    expect(screen.getByText('Annullati nel periodo')).toBeInTheDocument()
  })

  it('puts the «Annullati» chip in the URL', async () => {
    answering(page([purchase()]))
    renderWithQuery(<PurchasesPage />)
    await userEvent.click(await screen.findByRole('button', { name: 'Annullati' }))
    expect(navigate).toHaveBeenCalledWith({
      to: '/app/purchases',
      search: expect.objectContaining({ cancelled: true }),
      replace: true,
    })
  })

  it('sends the filters of the URL to the API and keeps the period in it', async () => {
    search = {
      kind: 'credit_note',
      supplier: '0199aaaa-0000-7000-8000-0000000000a1',
      category: CATEGORY.id,
      from: '2026-10-01',
      to: '2026-10-31',
    }
    answering(page([purchase()]))
    renderWithQuery(<PurchasesPage />)
    await screen.findByText('Studio Rossi')
    expect(purchaseCalls()[0]?.[1].params.query).toMatchObject({
      kind: 'credit_note',
      supplier_id: search.supplier,
      category_id: CATEGORY.id,
      from: '2026-10-01',
      to: '2026-10-31',
    })
    expect(screen.getByLabelText('Dal')).toHaveValue('2026-10-01')
  })

  it('writes a preset period to the URL', async () => {
    answering(page([purchase()]))
    renderWithQuery(<PurchasesPage />)
    await userEvent.click(await screen.findByRole('button', { name: 'Anno' }))
    const next = navigate.mock.calls.at(-1)?.[0] as { search: PurchasesSearch }
    expect(next.search.from).toMatch(/^\d{4}-01-01$/)
    expect(next.search.to).toMatch(/^\d{4}-12-31$/)
  })

  it('writes a chosen kind to the URL', async () => {
    answering(page([purchase()]))
    renderWithQuery(<PurchasesPage />)
    await userEvent.click(await screen.findByRole('combobox', { name: 'Tipo' }))
    await userEvent.click(await screen.findByRole('option', { name: 'Nota di credito' }))
    expect(navigate).toHaveBeenCalledWith({
      to: '/app/purchases',
      search: expect.objectContaining({ kind: 'credit_note' }),
      replace: true,
    })
  })

  it('says so when the filters select nothing', async () => {
    search = { kind: 'receipt' }
    answering(page([], { total_amount: '0.00' }))
    renderWithQuery(<PurchasesPage />)
    expect(await screen.findByText('Nessun acquisto con questi filtri.')).toBeInTheDocument()
  })
})

describe('«Acquisti» on a phone', () => {
  beforeEach(() => viewport(390))

  it('draws one column of cards that open the purchase, with the document line and chips', async () => {
    answering(
      page([
        purchase({ reverse_charge: true, currency: 'USD', currency_amount: '540.00' }),
        purchase({ id: 'p2', kind: 'credit_note', amount: '-122.00', merchant_name: 'Bianchi Srl', number: '9' }),
      ]),
    )
    renderWithQuery(<PurchasesPage />)
    const list = await screen.findByRole('list', { name: 'Acquisti' })
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    const cards = within(list).getAllByRole('link')
    expect(cards).toHaveLength(2)
    expect(cards[0]).toHaveAttribute('href', '/app/purchases/p1')
    expect(within(cards[0]!).getByText('Fattura 14/E · 28/10/2026')).toBeInTheDocument()
    expect(within(cards[0]!).getByText('Inversione contabile')).toBeInTheDocument()
    expect(within(cards[0]!).getByText('USD 540,00')).toBeInTheDocument()
    expect(within(cards[1]!).getByText('Nota di credito', { selector: '[data-variant="outline"]' })).toBeInTheDocument()
  })

  it('says «Mostra gli acquisti» in the sheet until the total is known', async () => {
    vi.mocked(api.GET).mockImplementation(((path: string) =>
      path === '/api/purchases'
        ? new Promise(() => {})
        : Promise.resolve(path === '/api/cost-categories' ? ok([]) : ok(SCHEMA))) as never)
    renderWithQuery(<PurchasesPage />)
    await userEvent.click(await screen.findByRole('button', { name: /Filtri/ }))
    expect(await screen.findByRole('button', { name: 'Mostra gli acquisti' })).toBeInTheDocument()
  })

  it('opens the filters in a sheet with supplier, category, type and period', async () => {
    answering(page([purchase()]))
    renderWithQuery(<PurchasesPage />)
    await userEvent.click(await screen.findByRole('button', { name: /Filtri/ }))
    const sheet = await screen.findByRole('dialog')
    expect(within(sheet).getByLabelText('Cerca un fornitore')).toBeInTheDocument()
    expect(within(sheet).getByRole('combobox', { name: 'Categoria' })).toBeInTheDocument()
    expect(within(sheet).getByRole('combobox', { name: 'Tipo' })).toBeInTheDocument()
    expect(within(sheet).getByLabelText('Dal')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('Registrati nel periodo')).toBeInTheDocument())
    expect(within(sheet).getByRole('button', { name: 'Mostra 1 acquisti' })).toBeInTheDocument()
  })
})
