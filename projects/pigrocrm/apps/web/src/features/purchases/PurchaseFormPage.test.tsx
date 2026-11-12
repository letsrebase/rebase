import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, fetchWithRefresh } from '@/lib/api'
import { LegalEntityContext, type LegalEntityRecord, type LegalEntityValue } from '@/lib/legalEntity'
import { spaceModules } from '@/lib/modules.testing'
import { toast } from '@rebase/ui/sonner'
import { PurchaseFormPage } from './PurchaseFormPage'

/** `api` is an openapi-fetch client that captured `fetch` at import time, so the module is
 *  mocked (the shape every panel test of this app uses); `unwrap`/`toProblem` stay real. */
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return {
    ...actual,
    api: { GET: vi.fn(), POST: vi.fn(), PATCH: vi.fn(), DELETE: vi.fn() },
    fetchWithRefresh: vi.fn(),
  }
})

const navigate = vi.fn()
vi.mock('@tanstack/react-router', () => ({
  Link: ({
    to,
    params,
    // eslint-disable-next-line @typescript-eslint/no-unused-vars -- the mock link has no query string to draw
    search: _search,
    children,
    ...rest
  }: {
    to: string
    params?: Record<string, string>
    search?: unknown
    children: ReactNode
  }) => (
    <a href={params ? to.replace(/\$(\w+)/g, (_, key: string) => params[key] ?? '') : to} {...rest}>
      {children}
    </a>
  ),
  useNavigate: () => navigate,
}))

const auth = { allowed: true }
vi.mock('@/lib/auth', () => ({
  useCan: () => auth.allowed,
  useCanWrite: () => auth.allowed,
}))

vi.mock('@rebase/ui/sonner', () => ({ toast: { info: vi.fn(), success: vi.fn(), error: vi.fn() } }))

const DEAL = 'aaaaaaaa-aaaa-7aaa-8aaa-aaaaaaaaaaaa'
const ITEM = 'bbbbbbbb-bbbb-7bbb-8bbb-bbbbbbbbbbbb'
const SUPPLIER_ID = 's1'

const CATEGORY = {
  id: 'cat1',
  name: 'Software e licenze',
  position: 1,
  code: 'software_licenses',
  archived: false,
  created_at: 'x',
  updated_at: 'x',
}
const EMPTY_SCHEMA = { entity_type: 'purchase', native_fields: [], custom_fields: [] }

const supplier = (overrides: Record<string, unknown> = {}) => ({
  id: SUPPLIER_ID,
  legal_name: 'Studio Rossi',
  country: 'IT',
  supplier_kind: null,
  ...overrides,
})

const purchase = (overrides: Record<string, unknown> = {}) => ({
  id: 'c1',
  deal_id: DEAL,
  legal_entity_id: null,
  category_id: 'cat1',
  date: '2026-03-05',
  amount: '2500.50',
  description: 'Licenza',
  kind: 'expense',
  status: 'registered',
  supplier_id: null,
  merchant_name: 'ACME',
  number: null,
  received_on: null,
  iva_period: null,
  currency: 'EUR',
  currency_amount: null,
  exchange_rate: null,
  exchange_rate_date: null,
  cost_amount: '2049.59',
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
  created_at: '2026-03-05T00:00:00Z',
  updated_at: '2026-03-05T00:00:00Z',
  ...overrides,
})

const inboxItem = (overrides: Record<string, unknown> = {}) => ({
  id: ITEM,
  original_name: 'scontrino-trattoria.png',
  file_type: 'png',
  has_file: true,
  ...overrides,
})

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) } as never)
}

function failed(error: unknown, status: number) {
  return Promise.resolve({ error, response: new Response(null, { status }) } as never)
}

type Routes = Record<string, () => Promise<never>>

/** Every endpoint the page reads, with the ones a test cares about overridden. */
function serve(overrides: Routes = {}, off: Parameters<typeof spaceModules>[0] = []) {
  const routes: Routes = {
    '/api/cost-categories': () => ok([CATEGORY]),
    '/api/schema/{entity_type}': () => ok(EMPTY_SCHEMA),
    '/api/settings/modules': () => ok(spaceModules(off)),
    '/api/organizations': () => ok({ items: [supplier()], next_cursor: null }),
    '/api/organizations/{organization_id}': () => ok(supplier()),
    '/api/deals/{deal_id}': () => ok({ id: DEAL, name: 'Sito web Acme' }),
    '/api/purchases': () => ok({ items: [], next_cursor: null, total_count: 0, total_amount: '0.00' }),
    ...overrides,
  }
  vi.mocked(api.GET).mockImplementation(((path: string) => {
    const route = routes[path]
    return route ? route() : failed({ detail: `unexpected GET ${path}` }, 500)
  }) as never)
}

/** `window.matchMedia` for a viewport `width` wide. */
function viewport(width: number) {
  window.matchMedia = ((query: string) => {
    const min = /min-width:\s*(\d+)px/.exec(query)
    return {
      matches: min === null ? false : width >= Number(min[1]),
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    }
  }) as never
}

let client: QueryClient

function renderPage(props: Parameters<typeof PurchaseFormPage>[0] = {}, legal_entity?: LegalEntityValue) {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const page = <PurchaseFormPage {...props} />
  return render(
    <QueryClientProvider client={client}>
      {legal_entity ? <LegalEntityContext value={legal_entity}>{page}</LegalEntityContext> : page}
    </QueryClientProvider>,
  )
}

function sent(mock: typeof api.POST | typeof api.PATCH) {
  const [path, options] = vi.mocked(mock).mock.calls[0] as unknown as [
    string,
    { body: Record<string, unknown>; params?: { path: { purchase_id: string } } },
  ]
  return { path, body: options.body, params: options.params }
}

async function pickKind(label: string) {
  await userEvent.click(screen.getByRole('combobox', { name: 'Tipo' }))
  await userEvent.click(await screen.findByRole('option', { name: label }))
}

async function fillCommon() {
  await userEvent.click(await screen.findByLabelText(/categoria/i))
  await userEvent.click(await screen.findByRole('option', { name: 'Software e licenze' }))
  await userEvent.type(screen.getByLabelText(/descrizione/i), 'Hosting')
}

const createObjectURL = vi.fn(() => 'blob:the-file')
const revokeObjectURL = vi.fn()

beforeEach(() => {
  navigate.mockReset()
  auth.allowed = true
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  vi.mocked(api.PATCH).mockReset()
  vi.mocked(fetchWithRefresh).mockReset()
  vi.mocked(toast.success).mockReset()
  createObjectURL.mockClear()
  revokeObjectURL.mockClear()
  URL.createObjectURL = createObjectURL
  URL.revokeObjectURL = revokeObjectURL
  viewport(390)
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('«Nuovo acquisto»', () => {
  it('opens blank on a spesa, with the page title and a way back', async () => {
    serve()
    renderPage()
    expect(await screen.findByRole('heading', { level: 1, name: 'Nuovo acquisto' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /acquisti/i })).toHaveAttribute('href', '/app/purchases')
    expect(await screen.findByRole('combobox', { name: 'Tipo' })).toHaveTextContent('Spesa')
    expect(screen.getByLabelText('Esercente')).toBeInTheDocument()
    expect(screen.queryByLabelText('Cerca un fornitore')).not.toBeInTheDocument()
    expect(screen.queryByText('Riepilogo IVA')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Registra' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Annulla' })).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('waits for the categories with a status and not a skeleton', async () => {
    serve({ '/api/cost-categories': () => new Promise<never>(() => {}) })
    renderPage()
    expect(await screen.findByRole('status', { name: 'Leggo le categorie…' })).toBeInTheDocument()
  })

  it('says so, with «Riprova», when the categories cannot be read', async () => {
    serve({ '/api/cost-categories': () => failed({ detail: 'Boom' }, 500) })
    renderPage()
    expect(await screen.findByRole('alert')).toHaveTextContent('Boom')
    expect(screen.getByRole('button', { name: 'Riprova' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Registra' })).not.toBeInTheDocument()
  })

  it('registers a general purchase, goes to its page and says so', async () => {
    serve()
    vi.mocked(api.POST).mockImplementation((() => ok(purchase({ id: 'new1', deal_id: null }))) as never)
    renderPage()

    await fillCommon()
    await userEvent.type(screen.getByLabelText(/importo \(€\)/i), '120')
    await userEvent.click(screen.getByRole('button', { name: 'Registra' }))

    await waitFor(() => expect(api.POST).toHaveBeenCalled())
    const { path, body } = sent(api.POST)
    expect(path).toBe('/api/purchases')
    // The amount travels as the text typed; `deal_id: null` says «general», it is not forgotten.
    expect(body).toMatchObject({
      kind: 'expense',
      deal_id: null,
      category_id: 'cat1',
      amount: '120',
      description: 'Hosting',
    })
    expect(body).not.toHaveProperty('inbox_item_id')
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith({
        to: '/app/purchases/$purchaseId',
        params: { purchaseId: 'new1' },
      }),
    )
    expect(toast.success).toHaveBeenCalledWith('Acquisto registrato')
  })

  it('keeps the action bar above the phone navigation below sm', async () => {
    serve()
    renderPage()
    const bar = (await screen.findByRole('button', { name: 'Registra' })).parentElement?.parentElement
    expect(bar).toHaveClass('bottom-(--phone-bar)', 'sm:bottom-0')
    expect(bar?.style.getPropertyValue('--phone-bar')).toBe('calc(4rem + env(safe-area-inset-bottom))')
  })

  it('«Annulla» goes back to the list without writing', async () => {
    serve()
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Annulla' }))
    expect(navigate).toHaveBeenCalledWith({ to: '/app/purchases' })
    expect(api.POST).not.toHaveBeenCalled()
  })

  it('files the purchase on the deal it was opened from and names it', async () => {
    serve()
    vi.mocked(api.POST).mockImplementation((() => ok(purchase())) as never)
    renderPage({ defaults: { deal_id: DEAL } })

    expect(await screen.findByText('Sito web Acme')).toBeInTheDocument()
    await fillCommon()
    await userEvent.type(screen.getByLabelText(/importo \(€\)/i), '10')
    await userEvent.click(screen.getByRole('button', { name: 'Registra' }))
    await waitFor(() => expect(api.POST).toHaveBeenCalled())
    expect(sent(api.POST).body).toMatchObject({ deal_id: DEAL })
  })

  it('draws no deal with the deals module off, and still files on it', async () => {
    serve({}, ['deals'])
    vi.mocked(api.POST).mockImplementation((() => ok(purchase())) as never)
    renderPage({ defaults: { deal_id: DEAL } })

    await screen.findByRole('button', { name: 'Registra' })
    await waitFor(() => expect(api.GET).toHaveBeenCalledWith('/api/settings/modules'))
    expect(screen.queryByText('Sito web Acme')).not.toBeInTheDocument()
    expect(api.GET).not.toHaveBeenCalledWith('/api/deals/{deal_id}', expect.anything())
  })

  it('opens on an invoice for a supplier given in the link', async () => {
    serve()
    renderPage({ defaults: { supplier_id: SUPPLIER_ID } })
    expect(await screen.findByRole('combobox', { name: 'Tipo' })).toHaveTextContent('Fattura')
    expect(await screen.findByText('Studio Rossi')).toBeInTheDocument()
    expect(screen.getByLabelText('Aliquota IVA, riga 1')).toBeInTheDocument()
  })

  it('swaps the merchant for a supplier and a VAT recap on a fattura, and back', async () => {
    serve()
    renderPage()
    await screen.findByLabelText('Esercente')
    await pickKind('Fattura')
    expect(screen.getByLabelText('Cerca un fornitore')).toBeInTheDocument()
    expect(screen.getByText('Riepilogo IVA')).toBeInTheDocument()
    expect(screen.getByLabelText('Aliquota IVA, riga 1')).toHaveValue('22')
    expect(screen.getByLabelText(/periodo iva/i)).toBeInTheDocument()

    await pickKind('Ricevuta')
    expect(screen.queryByLabelText('Cerca un fornitore')).not.toBeInTheDocument()
    expect(screen.queryByText('Riepilogo IVA')).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/periodo iva/i)).not.toBeInTheDocument()
  })

  it('does not build a supplier inline: it points to «Fornitori»', async () => {
    serve()
    renderPage()
    await screen.findByLabelText('Esercente')
    await pickKind('Fattura')
    expect(screen.getByRole('link', { name: /^fornitori$/i })).toHaveAttribute(
      'href',
      '/app/suppliers',
    )
    expect(screen.queryByRole('button', { name: /nuovo fornitore/i })).not.toBeInTheDocument()
    // A new tab: the page holds a half-filled form.
    expect(screen.getByRole('link', { name: /^fornitori$/i })).toHaveAttribute('target', '_blank')
    expect(screen.getByRole('link', { name: /^fornitori$/i })).toHaveAttribute('rel', 'noreferrer')
  })

  it('asks the organizations list for suppliers only', async () => {
    serve()
    renderPage()
    await screen.findByLabelText('Esercente')
    await pickKind('Fattura')
    await screen.findByRole('button', { name: 'Studio Rossi' })
    expect(api.GET).toHaveBeenCalledWith(
      '/api/organizations',
      expect.objectContaining({ params: { query: expect.objectContaining({ role: 'supplier' }) } }),
    )
  })

  it('registers a fattura with its supplier and its VAT recap, every figure as typed text', async () => {
    serve()
    vi.mocked(api.POST).mockImplementation((() => ok(purchase())) as never)
    renderPage()
    await screen.findByLabelText('Esercente')
    await pickKind('Fattura')
    await userEvent.click(await screen.findByRole('button', { name: 'Studio Rossi' }))
    await fillCommon()
    await userEvent.type(screen.getByLabelText('Numero'), 'F-12')
    await userEvent.type(screen.getByLabelText(/importo \(€\)/i), '122')
    await userEvent.type(screen.getByLabelText('Imponibile, riga 1'), '100.00')
    await userEvent.type(screen.getByLabelText('IVA, riga 1'), '22.00')
    await userEvent.click(screen.getByRole('button', { name: 'Registra' }))

    await waitFor(() => expect(api.POST).toHaveBeenCalled())
    const { body } = sent(api.POST)
    expect(body).toMatchObject({
      kind: 'invoice',
      supplier_id: SUPPLIER_ID,
      category_id: 'cat1',
      number: 'F-12',
      amount: '122',
      currency: 'EUR',
      vat_lines: [{ rate: '22', taxable_amount: '100.00', vat_amount: '22.00', deductible_share: '100' }],
    })
    expect(body).not.toHaveProperty('merchant_name')
    expect(body).not.toHaveProperty('currency_amount')
    expect(body).not.toHaveProperty('exchange_rate')
  })

  it('draws the foreign-currency block only when the currency is not the euro, typed', async () => {
    serve()
    vi.mocked(api.POST).mockImplementation((() => ok(purchase())) as never)
    renderPage()
    await screen.findByLabelText('Valuta')
    expect(screen.queryByLabelText('Importo in valuta')).not.toBeInTheDocument()

    await userEvent.clear(screen.getByLabelText('Valuta'))
    await userEvent.type(screen.getByLabelText('Valuta'), 'usd')
    await userEvent.type(await screen.findByLabelText('Importo in valuta'), '110')
    await userEvent.type(screen.getByLabelText('Cambio'), '1.1')
    await fillCommon()
    await userEvent.type(screen.getByLabelText(/importo \(€\)/i), '100')
    await userEvent.click(screen.getByRole('button', { name: 'Registra' }))

    await waitFor(() => expect(api.POST).toHaveBeenCalled())
    expect(sent(api.POST).body).toMatchObject({
      currency: 'USD',
      currency_amount: '110',
      exchange_rate: '1.1',
    })
  })
})

describe('what the supplier decides', () => {
  async function openInvoice(kind: string | null, country = 'IT') {
    serve({
      '/api/organizations/{organization_id}': () => ok(supplier({ supplier_kind: kind, country })),
    })
    renderPage()
    await screen.findByLabelText('Esercente')
    await pickKind('Fattura')
    await userEvent.click(await screen.findByRole('button', { name: 'Studio Rossi' }))
    await waitFor(() =>
      expect(api.GET).toHaveBeenCalledWith('/api/organizations/{organization_id}', expect.anything()),
    )
  }

  it.each(['professional', 'occasional'])('draws the withholding block for a %s supplier', async (kind) => {
    await openInvoice(kind)
    expect(await screen.findByLabelText('Ritenuta (%)')).toBeInTheDocument()
    expect(screen.getByLabelText('Cassa previdenziale (%)')).toBeInTheDocument()
    expect(screen.getByLabelText('Bollo (€)')).toBeInTheDocument()
  })

  it('draws no withholding block for any other supplier, and keeps the flags', async () => {
    await openInvoice('company')
    await screen.findByRole('checkbox', { name: 'Scissione dei pagamenti' })
    expect(screen.queryByLabelText('Ritenuta (%)')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Bollo (€)')).not.toBeInTheDocument()
  })

  it('sends no withholding for a supplier that carries none', async () => {
    await openInvoice(null)
    vi.mocked(api.POST).mockImplementation((() => ok(purchase())) as never)
    await fillCommon()
    await userEvent.type(screen.getByLabelText(/importo \(€\)/i), '10')
    await userEvent.click(screen.getByRole('button', { name: 'Registra' }))
    await waitFor(() => expect(api.POST).toHaveBeenCalled())
    expect(sent(api.POST).body).not.toHaveProperty('withholding_rate')
    expect(sent(api.POST).body).not.toHaveProperty('stamp_duty')
  })

  it('leaves the reverse charge to the person for a foreign supplier, never preselected', async () => {
    await openInvoice(null, 'DE')
    expect(await screen.findByText(/Lo decidi tu: il fornitore è estero/)).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'Inversione contabile' })).not.toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Scissione dei pagamenti' })).not.toBeChecked()
  })

  it('says nothing about it for an Italian supplier', async () => {
    await openInvoice(null, 'IT')
    await screen.findByRole('checkbox', { name: 'Inversione contabile' })
    expect(screen.queryByText(/Lo decidi tu/)).not.toBeInTheDocument()
  })
})

describe('«Registra come acquisto»: the file of «In arrivo» beside the form', () => {
  function serveFile(item: Record<string, unknown> = inboxItem()) {
    serve({ '/api/inbox/items/{item_id}': () => ok(item) })
  }

  function serveBytes() {
    vi.mocked(fetchWithRefresh).mockImplementation(() =>
      Promise.resolve(new Response('bytes', { status: 200, headers: { 'content-type': 'image/png' } })),
    )
  }

  it('titles the page for the capture and opens on an invoice', async () => {
    serveFile()
    serveBytes()
    viewport(1280)
    renderPage({ defaults: { inbox_item_id: ITEM } })
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Registra come acquisto' }),
    ).toBeInTheDocument()
    expect(await screen.findByRole('combobox', { name: 'Tipo' })).toHaveTextContent('Fattura')
  })

  it('draws the file in its own column from lg up, with no «Vedi il file»', async () => {
    serveFile()
    serveBytes()
    viewport(1280)
    renderPage({ defaults: { inbox_item_id: ITEM } })

    const aside = await screen.findByRole('complementary', { name: 'Il file dell’acquisto' })
    expect(await within(aside).findByRole('img', { name: 'scontrino-trattoria.png' })).toHaveAttribute(
      'src',
      'blob:the-file',
    )
    expect(fetchWithRefresh).toHaveBeenCalledWith(`/api/inbox/items/${ITEM}/file`)
    expect(within(aside).getByRole('button', { name: 'Apri il file' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: 'Vedi il file' })).not.toBeInTheDocument()
    // The form is beside it, not behind it.
    expect(screen.getByRole('button', { name: 'Registra' })).toBeInTheDocument()
  })

  it('puts the file behind «Vedi il file» below lg and opens it over the form', async () => {
    serveFile()
    serveBytes()
    renderPage({ defaults: { inbox_item_id: ITEM } })

    const open = await screen.findByRole('button', { name: 'Vedi il file' })
    expect(screen.queryByRole('complementary')).not.toBeInTheDocument()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    await userEvent.click(open)

    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByRole('img', { name: 'scontrino-trattoria.png' })).toBeInTheDocument()
  })

  it('shows a PDF in the browser\'s viewer', async () => {
    serveFile(inboxItem({ file_type: 'pdf', original_name: 'fattura.pdf' }))
    serveBytes()
    viewport(1280)
    renderPage({ defaults: { inbox_item_id: ITEM } })
    const aside = await screen.findByRole('complementary')
    await waitFor(() =>
      expect(aside.querySelector('object[type="application/pdf"]')).toHaveAttribute('data', 'blob:the-file'),
    )
  })

  it('registers the purchase with the item it came from', async () => {
    serveFile()
    serveBytes()
    viewport(1280)
    vi.mocked(api.POST).mockImplementation((() => ok(purchase({ id: 'p9' }))) as never)
    renderPage({ defaults: { inbox_item_id: ITEM, supplier_id: SUPPLIER_ID } })

    await screen.findByText('Studio Rossi')
    await fillCommon()
    await userEvent.type(screen.getByLabelText(/importo \(€\)/i), '122')
    await userEvent.type(screen.getByLabelText('Imponibile, riga 1'), '100.00')
    await userEvent.type(screen.getByLabelText('IVA, riga 1'), '22.00')
    await userEvent.click(screen.getByRole('button', { name: 'Registra' }))

    await waitFor(() => expect(api.POST).toHaveBeenCalled())
    expect(sent(api.POST).body).toMatchObject({
      inbox_item_id: ITEM,
      supplier_id: SUPPLIER_ID,
      kind: 'invoice',
      deal_id: null,
    })
  })

  it('draws nothing for an item that has no file', async () => {
    serveFile(inboxItem({ has_file: false }))
    viewport(1280)
    renderPage({ defaults: { inbox_item_id: ITEM } })
    await screen.findByRole('button', { name: 'Registra' })
    await waitFor(() => expect(api.GET).toHaveBeenCalledWith('/api/inbox/items/{item_id}', expect.anything()))
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Apri il file' })).not.toBeInTheDocument()
    // No file, no second column: the form is alone.
    expect(screen.queryByRole('complementary')).not.toBeInTheDocument()
  })

  it('offers no «Vedi il file» below lg for an item that has no file', async () => {
    serveFile(inboxItem({ has_file: false }))
    renderPage({ defaults: { inbox_item_id: ITEM } })
    await screen.findByRole('button', { name: 'Registra' })
    await waitFor(() => expect(api.GET).toHaveBeenCalledWith('/api/inbox/items/{item_id}', expect.anything()))
    expect(screen.queryByRole('button', { name: 'Vedi il file' })).not.toBeInTheDocument()
  })

  it('refreshes the inbox, so the item and its badge move, once the purchase is registered', async () => {
    serveFile()
    serveBytes()
    vi.mocked(api.POST).mockImplementation((() => ok(purchase({ id: 'p9' }))) as never)
    renderPage({ defaults: { inbox_item_id: ITEM, supplier_id: SUPPLIER_ID } })
    const invalidate = vi.spyOn(client, 'invalidateQueries')

    await screen.findByText('Studio Rossi')
    await fillCommon()
    await userEvent.type(screen.getByLabelText(/importo \(€\)/i), '122')
    await userEvent.type(screen.getByLabelText('Imponibile, riga 1'), '100.00')
    await userEvent.type(screen.getByLabelText('IVA, riga 1'), '22.00')
    await userEvent.click(screen.getByRole('button', { name: 'Registra' }))

    await waitFor(() => expect(navigate).toHaveBeenCalled())
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['inbox'] })
  })

  it('leaves the inbox alone for a purchase that came from nowhere', async () => {
    serve()
    vi.mocked(api.POST).mockImplementation((() => ok(purchase())) as never)
    renderPage()
    const invalidate = vi.spyOn(client, 'invalidateQueries')
    await fillCommon()
    await userEvent.type(screen.getByLabelText(/importo \(€\)/i), '5')
    await userEvent.click(screen.getByRole('button', { name: 'Registra' }))
    await waitFor(() => expect(navigate).toHaveBeenCalled())
    expect(invalidate).not.toHaveBeenCalledWith({ queryKey: ['inbox'] })
  })

  it('says why when the file cannot be read, and offers «Riprova», with the form still there', async () => {
    serveFile()
    vi.mocked(fetchWithRefresh).mockImplementation(() =>
      Promise.resolve(
        new Response(JSON.stringify({ code: 'not_found', detail: 'Il file non c’è più.' }), {
          status: 404,
          headers: { 'content-type': 'application/json' },
        }),
      ),
    )
    viewport(1280)
    renderPage({ defaults: { inbox_item_id: ITEM } })

    const aside = await screen.findByRole('complementary')
    expect(await within(aside).findByRole('alert')).toHaveTextContent('Il file non c’è più.')
    expect(within(aside).getByRole('button', { name: 'Riprova' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Registra' })).toBeInTheDocument()
  })

  it('waits for the bytes of the file with a status', async () => {
    serve({ '/api/inbox/items/{item_id}': () => ok(inboxItem()) })
    vi.mocked(fetchWithRefresh).mockImplementation(() => new Promise<never>(() => {}))
    viewport(1280)
    renderPage({ defaults: { inbox_item_id: ITEM } })
    const aside = await screen.findByRole('complementary')
    expect(await within(aside).findByRole('status', { name: 'Leggo il file…' })).toBeInTheDocument()
  })
})

describe('editing', () => {
  it('waits for the purchase with a status', async () => {
    serve({ '/api/purchases/{purchase_id}': () => new Promise<never>(() => {}) })
    renderPage({ purchaseId: 'c1' })
    expect(await screen.findByRole('status', { name: 'Leggo l’acquisto…' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Salva' })).not.toBeInTheDocument()
  })

  it('shows the error of the read and offers «Riprova»', async () => {
    serve({ '/api/purchases/{purchase_id}': () => failed({ detail: 'Boom' }, 500) })
    renderPage({ purchaseId: 'c1' })
    expect(await screen.findByRole('alert')).toHaveTextContent('Boom')
    expect(screen.getByRole('button', { name: 'Riprova' })).toBeInTheDocument()
  })

  it('seeds the form from the purchase, fixes the kind and sends only what changed', async () => {
    serve({ '/api/purchases/{purchase_id}': () => ok(purchase()) })
    vi.mocked(api.PATCH).mockImplementation((() => ok(purchase())) as never)
    renderPage({ purchaseId: 'c1' })

    expect(await screen.findByRole('heading', { level: 1, name: 'Modifica acquisto' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /torna all’acquisto/i })).toHaveAttribute(
      'href',
      '/app/purchases/c1',
    )
    expect(await screen.findByRole('combobox', { name: 'Tipo' })).toBeDisabled()
    expect(screen.getByLabelText(/descrizione/i)).toHaveValue('Licenza')
    expect(screen.getByLabelText('Esercente')).toHaveValue('ACME')
    expect(screen.getByLabelText(/importo \(€\)/i)).toHaveValue(2500.5)
    expect(screen.queryByRole('button', { name: 'Registra' })).not.toBeInTheDocument()

    const description = screen.getByLabelText(/descrizione/i)
    await userEvent.clear(description)
    await userEvent.type(description, 'Licenza annuale')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    await waitFor(() => expect(api.PATCH).toHaveBeenCalled())
    const { path, body, params } = sent(api.PATCH)
    expect(path).toBe('/api/purchases/{purchase_id}')
    expect(params?.path.purchase_id).toBe('c1')
    expect(body).toEqual({ description: 'Licenza annuale', custom_fields: {} })
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith({
        to: '/app/purchases/$purchaseId',
        params: { purchaseId: 'c1' },
      }),
    )
    expect(toast.success).toHaveBeenCalledWith('Acquisto aggiornato')
  })

  it('«Annulla» goes back to the purchase', async () => {
    serve({ '/api/purchases/{purchase_id}': () => ok(purchase()) })
    renderPage({ purchaseId: 'c1' })
    await userEvent.click(await screen.findByRole('button', { name: 'Annulla' }))
    expect(navigate).toHaveBeenCalledWith({
      to: '/app/purchases/$purchaseId',
      params: { purchaseId: 'c1' },
    })
    expect(api.PATCH).not.toHaveBeenCalled()
  })

  it('does not edit a cancelled purchase: a sentence and the way back', async () => {
    serve({ '/api/purchases/{purchase_id}': () => ok(purchase({ status: 'cancelled' })) })
    renderPage({ purchaseId: 'c1' })
    expect(await screen.findByText(/Un acquisto annullato non si modifica/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /torna all’acquisto/i })).toHaveAttribute(
      'href',
      '/app/purchases/c1',
    )
    expect(screen.queryByRole('button', { name: 'Salva' })).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/descrizione/i)).not.toBeInTheDocument()
  })

  it('shows the rate and its date the API holds as a sentence, not as inputs', async () => {
    serve({
      '/api/purchases/{purchase_id}': () =>
        ok(
          purchase({
            currency: 'USD',
            currency_amount: '110.00',
            exchange_rate: '1.100000',
            exchange_rate_date: '2026-03-05',
          }),
        ),
    })
    renderPage({ purchaseId: 'c1' })
    expect(await screen.findByText('Cambio 1,1 del 05/03/2026')).toBeInTheDocument()
    expect(screen.queryByLabelText('Cambio')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Data del cambio')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Importo in valuta')).toHaveValue(110)
  })

  it('opens the file beside the form of a purchase that came from «In arrivo»', async () => {
    serve({
      '/api/purchases/{purchase_id}': () => ok(purchase({ inbox_item_id: ITEM })),
      '/api/inbox/items/{item_id}': () => ok(inboxItem()),
    })
    vi.mocked(fetchWithRefresh).mockImplementation(() =>
      Promise.resolve(new Response('bytes', { status: 200, headers: { 'content-type': 'image/png' } })),
    )
    viewport(1280)
    renderPage({ purchaseId: 'c1' })
    const aside = await screen.findByRole('complementary')
    expect(await within(aside).findByRole('img')).toBeInTheDocument()
  })

  it('offers the legal entity of a general purchase from the second one on, «Condivisa» first', async () => {
    const HUMANCRAFT = { id: 'a1', name: 'humancraft', active: true } as LegalEntityRecord
    const REBASE = { id: 'a2', name: 'rebase', active: true } as LegalEntityRecord
    const TWO: LegalEntityValue = {
      legal_entities: [HUMANCRAFT, REBASE],
      selected: null,
      select: vi.fn(),
      several: true,
      scoped: false,
      pinned: false,
      loaded: true,
      byId: (id) => [HUMANCRAFT, REBASE].find((a) => a.id === id),
    }
    serve({ '/api/purchases/{purchase_id}': () => ok(purchase({ deal_id: null, legal_entity_id: 'a1' })) })
    renderPage({ purchaseId: 'c1' }, TWO)
    const picker = await screen.findByRole('combobox', { name: 'Azienda' })
    expect(picker).toHaveTextContent('humancraft')
    await userEvent.click(picker)
    expect(screen.getAllByRole('option').map((option) => option.textContent)).toEqual([
      'Condivisa',
      'humancraft',
      'rebase',
    ])
  })

  it('draws no legal entity for a purchase on a deal', async () => {
    const A = { id: 'a1', name: 'humancraft', active: true } as LegalEntityRecord
    const B = { id: 'a2', name: 'rebase', active: true } as LegalEntityRecord
    const TWO: LegalEntityValue = {
      legal_entities: [A, B],
      selected: null,
      select: vi.fn(),
      several: true,
      scoped: false,
      pinned: false,
      loaded: true,
      byId: (id) => [A, B].find((a) => a.id === id),
    }
    serve({ '/api/purchases/{purchase_id}': () => ok(purchase()) })
    renderPage({ purchaseId: 'c1' }, TWO)
    await screen.findByRole('button', { name: 'Salva' })
    expect(screen.queryByRole('combobox', { name: 'Azienda' })).not.toBeInTheDocument()
  })

  it('never shows the legal entity\'s id as a custom field that happens to share its name', async () => {
    serve({
      '/api/purchases/{purchase_id}': () => ok(purchase({ deal_id: null, legal_entity_id: 'a1' })),
      '/api/schema/{entity_type}': () =>
        ok({
          entity_type: 'purchase',
          native_fields: [],
          custom_fields: [
            { key: 'legal_entity_id', label: 'Codice interno', type: 'text', required: false, options: [] },
          ],
        }),
    })
    renderPage({ purchaseId: 'c1' })
    expect(await screen.findByLabelText('Codice interno')).toHaveValue('')
  })
})

describe('when the server refuses', () => {
  it('keeps the form and puts the refusal on the field it names', async () => {
    serve()
    vi.mocked(api.POST).mockImplementation((() =>
      failed(
        {
          type: 'x',
          title: 'x',
          status: 422,
          code: 'validation_failed',
          field: 'amount',
          detail: 'Importo non valido',
          reason: 'il totale non torna con il riepilogo IVA',
        },
        422,
      )) as never)
    renderPage()
    await fillCommon()
    await userEvent.type(screen.getByLabelText(/importo \(€\)/i), '5')
    await userEvent.click(screen.getByRole('button', { name: 'Registra' }))

    expect(await screen.findByText('il totale non torna con il riepilogo IVA')).toBeInTheDocument()
    expect(screen.getByLabelText(/descrizione/i)).toHaveValue('Hosting')
    expect(screen.getByRole('button', { name: 'Registra' })).toBeEnabled()
    expect(navigate).not.toHaveBeenCalled()
  })

  it('shows a refusal that names no field as the server words it, beside the buttons', async () => {
    serve()
    vi.mocked(api.POST).mockImplementation((() =>
      failed(
        { type: 'x', title: 'x', status: 409, code: 'period_closed', detail: 'Il periodo IVA è chiuso: non si può modificare.' },
        409,
      )) as never)
    renderPage()
    await fillCommon()
    await userEvent.type(screen.getByLabelText(/importo \(€\)/i), '5')
    await userEvent.click(screen.getByRole('button', { name: 'Registra' }))

    const alerts = await screen.findAllByRole('alert')
    expect(alerts).toHaveLength(1)
    expect(alerts[0]).toHaveTextContent('Il periodo IVA è chiuso: non si può modificare.')
    expect(screen.getByLabelText(/descrizione/i)).toHaveValue('Hosting')
    expect(navigate).not.toHaveBeenCalled()
  })

  it('shows a refusal on a field the form does not draw as its message alone, never the key', async () => {
    serve()
    vi.mocked(api.POST).mockImplementation((() =>
      failed(
        {
          type: 'x',
          title: 'x',
          status: 422,
          code: 'validation_failed',
          field: 'deal_id',
          detail: 'x',
          reason: 'il deal non è di questa azienda',
        },
        422,
      )) as never)
    renderPage()
    await fillCommon()
    await userEvent.type(screen.getByLabelText(/importo \(€\)/i), '5')
    await userEvent.click(screen.getByRole('button', { name: 'Registra' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('il deal non è di questa azienda')
    expect(alert).not.toHaveTextContent('deal_id')
  })

  it('shows a refusal on a control of its own once, on that control', async () => {
    serve()
    vi.mocked(api.POST).mockImplementation((() =>
      failed(
        {
          type: 'x',
          title: 'x',
          status: 422,
          code: 'validation_failed',
          field: 'category_id',
          detail: 'Categoria archiviata',
          reason: 'la categoria è archiviata',
        },
        422,
      )) as never)
    renderPage()
    await fillCommon()
    await userEvent.type(screen.getByLabelText(/importo \(€\)/i), '5')
    await userEvent.click(screen.getByRole('button', { name: 'Registra' }))
    expect(await screen.findAllByText('la categoria è archiviata')).toHaveLength(1)
  })
})

describe('permissions', () => {
  it('gives a read-only member a sentence and the way back, never a form', async () => {
    auth.allowed = false
    serve()
    renderPage()
    expect(await screen.findByText(/puoi leggere gli acquisti, ma non registrarli/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /acquisti/i })).toHaveAttribute('href', '/app/purchases')
    expect(screen.queryByRole('button', { name: 'Registra' })).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/descrizione/i)).not.toBeInTheDocument()
  })

  it('does not even read the purchase for a read-only member who opens the edit page', async () => {
    auth.allowed = false
    serve({ '/api/purchases/{purchase_id}': () => ok(purchase()) })
    renderPage({ purchaseId: 'c1' })
    expect(await screen.findByText(/non registrarli né modificarli/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Salva' })).not.toBeInTheDocument()
  })
})

describe('the rows of the form', () => {
  const VIP = { key: 'rush', label: 'Urgente', type: 'checkbox', required: false, options: [] }

  it('keeps every label a real label of its control, custom fields included', async () => {
    serve({
      '/api/schema/{entity_type}': () =>
        ok({ entity_type: 'purchase', native_fields: [], custom_fields: [VIP] }),
    })
    renderPage()
    await screen.findByLabelText('Esercente')
    for (const name of ['Numero', 'Data documento', 'Importo (€)', 'Valuta']) {
      expect(screen.getByLabelText(new RegExp(name.replace(/[()€]/g, '\\$&')))).toBeInTheDocument()
    }
    expect(screen.getByRole('checkbox', { name: 'Urgente' })).toBeInTheDocument()
  })

  it('answers an untouched custom checkbox «no» on create, as the other forms do', async () => {
    serve({
      '/api/schema/{entity_type}': () =>
        ok({ entity_type: 'purchase', native_fields: [], custom_fields: [VIP] }),
    })
    vi.mocked(api.POST).mockImplementation((() => ok(purchase())) as never)
    renderPage()
    await fillCommon()
    await userEvent.type(screen.getByLabelText(/importo \(€\)/i), '5')
    await userEvent.click(screen.getByRole('button', { name: 'Registra' }))
    await waitFor(() => expect(api.POST).toHaveBeenCalled())
    expect(sent(api.POST).body).toMatchObject({ custom_fields: { rush: false } })
  })
})

describe('what the buyer\'s pack lets a purchase carry', () => {
  const caps = (overrides: Record<string, unknown> = {}) => ({
    legal_entity_id: null,
    vat_recap: true,
    withholding: true,
    reverse_charge: true,
    split_payment: true,
    deductibility: true,
    ...overrides,
  })

  async function openInvoice(capabilities: () => Promise<never>, kind: string | null = 'professional') {
    serve({
      '/api/purchases/capabilities': capabilities,
      '/api/organizations/{organization_id}': () => ok(supplier({ supplier_kind: kind })),
    })
    renderPage()
    await screen.findByLabelText('Esercente')
    await pickKind('Fattura')
    await userEvent.click(await screen.findByRole('button', { name: 'Studio Rossi' }))
    await waitFor(() =>
      expect(api.GET).toHaveBeenCalledWith('/api/organizations/{organization_id}', expect.anything()),
    )
  }

  it('shows everything while the capabilities load, and never blocks the form', async () => {
    await openInvoice(() => new Promise<never>(() => {}))
    expect(await screen.findByLabelText('Ritenuta (%)')).toBeInTheDocument()
    expect(screen.getByText('Riepilogo IVA')).toBeInTheDocument()
    expect(screen.getByLabelText(/periodo iva/i)).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'Inversione contabile' })).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'Scissione dei pagamenti' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Registra' })).toBeEnabled()
  })

  it('shows everything when the capabilities cannot be read', async () => {
    await openInvoice(() => failed({ detail: 'Boom' }, 500))
    expect(await screen.findByLabelText('Ritenuta (%)')).toBeInTheDocument()
    expect(screen.getByText('Riepilogo IVA')).toBeInTheDocument()
  })

  it('hides the withholding block when the pack refuses it, even for a professional supplier', async () => {
    await openInvoice(() => ok(caps({ withholding: false })))
    await waitFor(() => expect(screen.queryByLabelText('Ritenuta (%)')).not.toBeInTheDocument())
    expect(screen.queryByLabelText('Cassa previdenziale (%)')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Bollo (€)')).not.toBeInTheDocument()
    expect(screen.getByText('Riepilogo IVA')).toBeInTheDocument()
  })

  it('keeps the block hidden for a supplier that carries none even when the pack allows it', async () => {
    await openInvoice(() => ok(caps()), 'company')
    await screen.findByRole('checkbox', { name: 'Scissione dei pagamenti' })
    expect(screen.queryByLabelText('Ritenuta (%)')).not.toBeInTheDocument()
  })

  it('hides the recap, the VAT period and each flag the pack refuses, and sends none of them', async () => {
    await openInvoice(() =>
      ok(caps({ vat_recap: false, reverse_charge: false, split_payment: false })),
    )
    await waitFor(() => expect(screen.queryByText('Riepilogo IVA')).not.toBeInTheDocument())
    expect(screen.queryByLabelText(/periodo iva/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('checkbox', { name: 'Inversione contabile' })).not.toBeInTheDocument()
    expect(screen.queryByRole('checkbox', { name: 'Scissione dei pagamenti' })).not.toBeInTheDocument()

    vi.mocked(api.POST).mockImplementation((() => ok(purchase())) as never)
    await fillCommon()
    await userEvent.type(screen.getByLabelText(/importo \(€\)/i), '122')
    await userEvent.click(screen.getByRole('button', { name: 'Registra' }))
    await waitFor(() => expect(api.POST).toHaveBeenCalled())
    const { body } = sent(api.POST)
    for (const key of ['vat_lines', 'iva_period', 'reverse_charge', 'split_payment']) {
      expect(body).not.toHaveProperty(key)
    }
    expect(body).toMatchObject({ kind: 'invoice', supplier_id: SUPPLIER_ID, amount: '122' })
  })

  it('hides the natura fiscale and the asset group when the pack does not read them, and sends neither', async () => {
    await openInvoice(() => ok(caps({ deductibility: false })))
    await waitFor(() => expect(screen.queryByText('Natura fiscale')).not.toBeInTheDocument())
    expect(screen.queryByText('Bene ammortizzabile')).not.toBeInTheDocument()

    vi.mocked(api.POST).mockImplementation((() => ok(purchase())) as never)
    await fillCommon()
    await userEvent.type(screen.getByLabelText(/importo \(€\)/i), '122')
    await userEvent.click(screen.getByRole('button', { name: 'Registra' }))
    await waitFor(() => expect(api.POST).toHaveBeenCalled())
    const { body } = sent(api.POST)
    expect(body).not.toHaveProperty('deductibility_kind')
    expect(body).not.toHaveProperty('asset_group')
  })

  it('sends the natura fiscale and the asset group the person picked', async () => {
    await openInvoice(() => ok(caps()))
    await userEvent.click(await screen.findByRole('combobox', { name: 'Natura fiscale' }))
    await userEvent.click(await screen.findByRole('option', { name: 'Telefonia' }))
    await userEvent.click(screen.getByRole('combobox', { name: 'Bene ammortizzabile' }))
    await userEvent.click(await screen.findByRole('option', { name: 'Autovetture' }))

    vi.mocked(api.POST).mockImplementation((() => ok(purchase())) as never)
    await fillCommon()
    await userEvent.type(screen.getByLabelText(/importo \(€\)/i), '122')
    await userEvent.click(screen.getByRole('button', { name: 'Registra' }))
    await waitFor(() => expect(api.POST).toHaveBeenCalled())
    expect(sent(api.POST).body).toMatchObject({
      deductibility_kind: 'phone',
      asset_group: 'cars',
    })
  })

  it('states nothing by default, and draws no asset group for a credit note', async () => {
    await openInvoice(() => ok(caps()))
    await screen.findByRole('combobox', { name: 'Bene ammortizzabile' })

    await pickKind('Nota di credito')
    await waitFor(() =>
      expect(screen.queryByRole('combobox', { name: 'Bene ammortizzabile' })).not.toBeInTheDocument(),
    )
    expect(screen.getByRole('combobox', { name: 'Natura fiscale' })).toBeInTheDocument()
  })

  it('does not hide the foreign-supplier sentence\'s checkbox from itself', async () => {
    serve({
      '/api/purchases/capabilities': () => ok(caps({ reverse_charge: false })),
      '/api/organizations/{organization_id}': () => ok(supplier({ country: 'DE' })),
    })
    renderPage()
    await screen.findByLabelText('Esercente')
    await pickKind('Fattura')
    await userEvent.click(await screen.findByRole('button', { name: 'Studio Rossi' }))
    await screen.findByRole('checkbox', { name: 'Scissione dei pagamenti' })
    await waitFor(() => expect(screen.queryByText(/Lo decidi tu/)).not.toBeInTheDocument())
  })

  it('asks for the deal\'s legal entity when the purchase is on a deal', async () => {
    serve({
      '/api/deals/{deal_id}': () => ok({ id: DEAL, name: 'Sito web Acme', legal_entity_id: 'e1' }),
      '/api/purchases/capabilities': () => ok(caps()),
    })
    renderPage({ defaults: { deal_id: DEAL } })
    await waitFor(() =>
      expect(api.GET).toHaveBeenCalledWith('/api/purchases/capabilities', {
        params: { query: { legal_entity_id: 'e1' } },
      }),
    )
  })

  it('asks for the legal entity chosen on the form, before the deal\'s', async () => {
    const A = { id: 'a1', name: 'humancraft', active: true } as LegalEntityRecord
    const B = { id: 'a2', name: 'rebase', active: true } as LegalEntityRecord
    const TWO: LegalEntityValue = {
      legal_entities: [A, B],
      selected: null,
      select: vi.fn(),
      several: true,
      scoped: false,
      pinned: false,
      loaded: true,
      byId: (id) => [A, B].find((a) => a.id === id),
    }
    serve({ '/api/purchases/capabilities': () => ok(caps()) })
    renderPage({}, TWO)
    await userEvent.click(await screen.findByRole('combobox', { name: 'Azienda' }))
    await userEvent.click(await screen.findByRole('option', { name: 'rebase' }))
    await waitFor(() =>
      expect(api.GET).toHaveBeenCalledWith('/api/purchases/capabilities', {
        params: { query: { legal_entity_id: 'a2' } },
      }),
    )
  })
})
