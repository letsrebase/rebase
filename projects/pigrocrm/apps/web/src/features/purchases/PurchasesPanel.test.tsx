import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { LegalEntityContext, type LegalEntityRecord, type LegalEntityValue } from '@/lib/legalEntity'
import { api } from '@/lib/api'
import { PurchasesPanel } from './PurchasesPanel'

/**
 * The brief wrote these against `msw`, which is not a dependency here and would not
 * work if it were: `api` is an openapi-fetch client that captured `globalThis.fetch`
 * into a closure at import time, so nothing installed on the network afterwards sees a
 * request made through it. Mocking the module is the shape every other panel test in
 * this codebase uses, and it keeps `unwrap`/`toProblem` real.
 */
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PATCH: vi.fn(), DELETE: vi.fn() } }
})

vi.mock('@/lib/auth', () => ({
  useCanWrite: () => true,
  useAuth: () => ({
    user: { id: '33333333-3333-7333-8333-333333333333', role: 'admin' },
    isLoading: false,
    login: vi.fn(),
    logout: vi.fn(),
  }),
}))

vi.mock('@rebase/ui/sonner', () => ({ toast: { info: vi.fn(), success: vi.fn(), error: vi.fn() } }))

const DEAL = 'aaaaaaaa-aaaa-7aaa-8aaa-aaaaaaaaaaaa'

const purchase = (overrides: Record<string, unknown> = {}) => ({
  id: 'c1',
  deal_id: DEAL,
  category_id: 'cat1',
  date: '2026-03-05',
  amount: '2500.50',
  description: 'Licenza',
  kind: 'expense',
  status: 'registered',
  supplier_id: null,
  merchant_name: 'ACME',
  number: null,
  cost_amount: '2049.59',
  vat_lines: [],
  document_id: null,
  custom_fields: {},
  created_at: '2026-03-05T00:00:00Z',
  updated_at: '2026-03-05T00:00:00Z',
  ...overrides,
})

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

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) } as never)
}

function failed(error: unknown, status: number) {
  return Promise.resolve({ error, response: new Response(null, { status }) } as never)
}

/** Routes by path: the panel reads three endpoints at once, and one blanket
 *  `mockResolvedValue` would answer all three with the same document. */
function respond(routes: Record<string, () => Promise<never>>) {
  vi.mocked(api.GET).mockImplementation(((path: string) => {
    const route = routes[path]
    if (!route) throw new Error(`unexpected GET ${path}`)
    return route()
  }) as never)
}

/** `null` is the general-purchases panel, the one with no deal: a defaulted parameter
 *  cannot be handed `undefined` to mean that, it would take the default instead. */
function renderPanel(dealId: string | null = DEAL, legal_entity?: LegalEntityValue) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const panel = <PurchasesPanel dealId={dealId ?? undefined} />
  return render(
    <QueryClientProvider client={client}>
      {legal_entity ? <LegalEntityContext value={legal_entity}>{panel}</LegalEntityContext> : panel}
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  vi.mocked(api.PATCH).mockReset()
  vi.mocked(api.DELETE).mockReset()
})

describe('PurchasesPanel', () => {
  it('formats amounts with a forced thousands separator and resolves the category name', async () => {
    respond({
      '/api/purchases': () => ok({ items: [purchase()], next_cursor: null }),
      '/api/cost-categories': () => ok([CATEGORY]),
      '/api/schema/{entity_type}': () => ok(EMPTY_SCHEMA),
    })
    renderPanel()
    expect(await screen.findByText('2.500,50 €')).toBeInTheDocument()
    expect(await screen.findByText('Software e licenze')).toBeInTheDocument()
  })

  it('shows a negative amount as a refund rather than as an error', async () => {
    respond({
      '/api/purchases': () => ok({ items: [purchase({ amount: '-45.50' })], next_cursor: null }),
      '/api/cost-categories': () => ok([]),
      '/api/schema/{entity_type}': () => ok(EMPTY_SCHEMA),
    })
    renderPanel()
    // One assertion on the whole cell, not two: the panel's own standing note also says
    // the word "rimborso", so a bare `/rimborso/i` matches two nodes and proves nothing
    // about the row.
    expect(await screen.findByText('-45,50 € (rimborso)')).toBeInTheDocument()
  })

  it('renders a banner, never an empty table, when the list fails', async () => {
    respond({
      '/api/purchases': () => failed({ detail: 'Boom' }, 500),
      '/api/cost-categories': () => ok([]),
      '/api/schema/{entity_type}': () => ok(EMPTY_SCHEMA),
    })
    renderPanel()
    expect(await screen.findByRole('alert')).toHaveTextContent('Boom')
    expect(screen.queryByText(/nessun acquisto/i)).not.toBeInTheDocument()
  })

  it('sends the deal id with the purchase, so a deal expense is never filed as a general one', async () => {
    respond({
      '/api/purchases': () => ok({ items: [], next_cursor: null }),
      '/api/cost-categories': () => ok([CATEGORY]),
      '/api/schema/{entity_type}': () => ok(EMPTY_SCHEMA),
    })
    vi.mocked(api.POST).mockImplementation((() => ok(purchase())) as never)
    renderPanel()

    await userEvent.click(await screen.findByRole('button', { name: /registra acquisto/i }))
    await userEvent.click(await screen.findByLabelText(/categoria/i))
    await userEvent.click(await screen.findByRole('option', { name: 'Software e licenze' }))
    await userEvent.type(await screen.findByLabelText(/importo/i), '120')
    await userEvent.type(screen.getByLabelText(/descrizione/i), 'Hosting')
    await userEvent.click(screen.getByRole('button', { name: /^salva$/i }))

    await waitFor(() => expect(api.POST).toHaveBeenCalled())
    // `noUncheckedIndexedAccess` types element 0 as possibly `undefined`, so the cast
    // goes through `unknown` -- the `toHaveBeenCalled` above is what already ruled it out.
    const [path, options] = vi.mocked(api.POST).mock.calls[0] as unknown as [
      string,
      { body: Record<string, unknown> },
    ]
    expect(path).toBe('/api/purchases')
    // The amount travels as the string the user typed, never through a JS float: the
    // whole point of §4's decimal discipline is that no economic value is born here.
    // `category_id` is the category's UUID and not its name -- a rename must not
    // orphan the cost.
    expect(options.body).toMatchObject({
      deal_id: DEAL,
      category_id: 'cat1',
      amount: '120',
      description: 'Hosting',
    })
  })
})

describe('the «Azienda» picker of a general purchase (REB-626)', () => {
  const HUMANCRAFT = { id: 'a1', name: 'humancraft', active: true } as LegalEntityRecord
  const REBASE = { id: 'a2', name: 'rebase', active: true } as LegalEntityRecord
  const TWO: LegalEntityValue = {
    legal_entities: [HUMANCRAFT, REBASE],
    selected: null,
    select: vi.fn(),
    several: true, scoped: false, pinned: false, loaded: true,
    byId: (id) => [HUMANCRAFT, REBASE].find((a) => a.id === id),
  }

  function serveEmpty() {
    respond({
      '/api/purchases': () => ok({ items: [], next_cursor: null }),
      '/api/cost-categories': () => ok([CATEGORY]),
      '/api/schema/{entity_type}': () => ok(EMPTY_SCHEMA),
    })
  }

  it('offers «Condivisa» first for a purchase with no deal, from the second azienda on', async () => {
    serveEmpty()
    renderPanel(null, TWO)
    await userEvent.click(await screen.findByRole('button', { name: /Registra acquisto/ }))
    const picker = await screen.findByRole('combobox', { name: 'Azienda' })
    expect(picker).toHaveTextContent('Condivisa')
    await userEvent.click(picker)
    expect(screen.getAllByRole('option').map((option) => option.textContent)).toEqual([
      'Condivisa',
      'humancraft',
      'rebase',
    ])
  })

  it('draws nothing for a purchase on a deal, nor in a one-legal-entity space', async () => {
    serveEmpty()
    renderPanel(DEAL, TWO)
    await userEvent.click(await screen.findByRole('button', { name: /Registra acquisto/ }))
    await screen.findByRole('dialog')
    expect(screen.queryByRole('combobox', { name: 'Azienda' })).not.toBeInTheDocument()
  })

  it('draws nothing for a general purchase in a one-legal-entity space', async () => {
    serveEmpty()
    renderPanel(null)
    await userEvent.click(await screen.findByRole('button', { name: /Registra acquisto/ }))
    await screen.findByRole('dialog')
    expect(screen.queryByRole('combobox', { name: 'Azienda' })).not.toBeInTheDocument()
  })
})

describe('a custom purchase field that happens to be named azienda_id (CodeRabbit, PR #509)', () => {
  const HUMANCRAFT = { id: 'a1', name: 'humancraft', active: true } as LegalEntityRecord
  const REBASE = { id: 'a2', name: 'rebase', active: true } as LegalEntityRecord
  const TWO: LegalEntityValue = {
    legal_entities: [HUMANCRAFT, REBASE],
    selected: null,
    select: vi.fn(),
    several: true, scoped: false, pinned: false, loaded: true,
    byId: (id) => [HUMANCRAFT, REBASE].find((a) => a.id === id),
  }

  it('never shows the owning azienda\'s id as the custom field\'s value', async () => {
    respond({
      '/api/purchases': () =>
        ok({ items: [purchase({ deal_id: null, legal_entity_id: 'a1', custom_fields: {} })], next_cursor: null }),
      '/api/cost-categories': () => ok([CATEGORY]),
      '/api/schema/{entity_type}': () =>
        ok({
          entity_type: 'purchase',
          native_fields: [],
          custom_fields: [
            { key: 'legal_entity_id', label: 'Codice interno', type: 'text', required: false, options: [] },
          ],
        }),
    })
    renderPanel(null, TWO)
    await userEvent.click(await screen.findByText('Licenza'))
    await screen.findByRole('dialog')
    expect(screen.getByRole('combobox', { name: 'Azienda' })).toHaveTextContent('humancraft')
    expect(screen.getByLabelText('Codice interno')).toHaveValue('')
  })
})

describe('the kinds of purchase', () => {
  const SUPPLIER = { id: 's1', legal_name: 'Studio Rossi' }

  function serve(items: unknown[] = []) {
    respond({
      '/api/purchases': () => ok({ items, next_cursor: null }),
      '/api/cost-categories': () => ok([CATEGORY]),
      '/api/schema/{entity_type}': () => ok(EMPTY_SCHEMA),
      '/api/organizations': () => ok({ items: [SUPPLIER], next_cursor: null }),
      '/api/organizations/{organization_id}': () => ok(SUPPLIER),
    })
  }

  async function open() {
    renderPanel()
    await userEvent.click(await screen.findByRole('button', { name: /registra acquisto/i }))
    await screen.findByRole('dialog')
  }

  async function pickKind(label: string) {
    await userEvent.click(screen.getByRole('combobox', { name: 'Tipo' }))
    await userEvent.click(await screen.findByRole('option', { name: label }))
  }

  async function fillCommon() {
    await userEvent.click(screen.getByLabelText(/categoria/i))
    await userEvent.click(await screen.findByRole('option', { name: 'Software e licenze' }))
    await userEvent.type(screen.getByLabelText(/descrizione/i), 'Hosting')
  }

  function postedBody() {
    const [path, options] = vi.mocked(api.POST).mock.calls[0] as unknown as [
      string,
      { body: Record<string, unknown> },
    ]
    return { path, body: options.body }
  }

  it('opens on a spesa: a merchant name, no supplier picker, no VAT recap', async () => {
    serve()
    await open()
    expect(screen.getByRole('combobox', { name: 'Tipo' })).toHaveTextContent('Spesa')
    expect(screen.getByLabelText('Fornitore')).toBeInTheDocument()
    expect(screen.queryByLabelText('Cerca un fornitore')).not.toBeInTheDocument()
    expect(screen.queryByText('Riepilogo IVA')).not.toBeInTheDocument()
  })

  it('swaps the merchant name for a supplier picker and a VAT recap on a fattura, and back', async () => {
    serve()
    await open()
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

  it('asks the organizations list for suppliers only', async () => {
    serve()
    await open()
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
    await open()
    await pickKind('Fattura')
    await userEvent.click(await screen.findByRole('button', { name: 'Studio Rossi' }))
    await fillCommon()
    await userEvent.type(screen.getByLabelText('Numero'), 'F-12')
    await userEvent.type(screen.getByLabelText(/importo \(€\)/i), '122')
    await userEvent.type(screen.getByLabelText('Imponibile, riga 1'), '100.00')
    await userEvent.type(screen.getByLabelText('IVA, riga 1'), '22.00')
    await userEvent.click(screen.getByRole('button', { name: /^salva$/i }))

    await waitFor(() => expect(api.POST).toHaveBeenCalled())
    const { path, body } = postedBody()
    expect(path).toBe('/api/purchases')
    expect(body).toMatchObject({
      kind: 'invoice',
      deal_id: DEAL,
      supplier_id: 's1',
      category_id: 'cat1',
      number: 'F-12',
      amount: '122',
      currency: 'EUR',
      vat_lines: [
        { rate: '22', taxable_amount: '100.00', vat_amount: '22.00', deductible_share: '100' },
      ],
    })
    // What the kind does not take, and what is in euro, is not sent.
    expect(body).not.toHaveProperty('merchant_name')
    expect(body).not.toHaveProperty('currency_amount')
    expect(body).not.toHaveProperty('exchange_rate')
  })

  it('does not carry a recap or a supplier into an expense switched to after a fattura', async () => {
    serve()
    vi.mocked(api.POST).mockImplementation((() => ok(purchase())) as never)
    await open()
    await pickKind('Fattura')
    await userEvent.click(await screen.findByRole('button', { name: 'Studio Rossi' }))
    await userEvent.type(screen.getByLabelText('Imponibile, riga 1'), '100')
    await pickKind('Spesa')
    await fillCommon()
    await userEvent.type(screen.getByLabelText(/importo \(€\)/i), '10')
    await userEvent.type(screen.getByLabelText('Fornitore'), 'Bar Roma')
    await userEvent.click(screen.getByRole('button', { name: /^salva$/i }))

    await waitFor(() => expect(api.POST).toHaveBeenCalled())
    const { body } = postedBody()
    expect(body).toMatchObject({ kind: 'expense', merchant_name: 'Bar Roma', amount: '10' })
    expect(body).not.toHaveProperty('vat_lines')
    expect(body).not.toHaveProperty('supplier_id')
  })

  it('asks for the nature only on a zero-rate line, and sends it only there', async () => {
    serve()
    vi.mocked(api.POST).mockImplementation((() => ok(purchase())) as never)
    await open()
    await pickKind('Fattura')
    expect(screen.queryByLabelText('Natura, riga 1')).not.toBeInTheDocument()

    await userEvent.clear(screen.getByLabelText('Aliquota IVA, riga 1'))
    await userEvent.type(screen.getByLabelText('Aliquota IVA, riga 1'), '0')
    await userEvent.type(await screen.findByLabelText('Natura, riga 1'), 'N2.2')
    await userEvent.type(screen.getByLabelText('Imponibile, riga 1'), '50')
    await userEvent.type(screen.getByLabelText('IVA, riga 1'), '0')
    await userEvent.click(await screen.findByRole('button', { name: 'Studio Rossi' }))
    await fillCommon()
    await userEvent.type(screen.getByLabelText(/importo \(€\)/i), '50')
    await userEvent.click(screen.getByRole('button', { name: /^salva$/i }))

    await waitFor(() => expect(api.POST).toHaveBeenCalled())
    expect(postedBody().body.vat_lines).toEqual([
      {
        rate: '0',
        nature: 'N2.2',
        taxable_amount: '50',
        vat_amount: '0',
        deductible_share: '100',
      },
    ])
  })

  it('adds and removes recap rows', async () => {
    serve()
    await open()
    await pickKind('Fattura')
    await userEvent.click(screen.getByRole('button', { name: 'Aggiungi riga IVA' }))
    expect(screen.getByLabelText('Aliquota IVA, riga 2')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Rimuovi la riga IVA 1' }))
    expect(screen.queryByLabelText('Aliquota IVA, riga 2')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Aliquota IVA, riga 1')).toBeInTheDocument()
  })

  it('draws the foreign-currency block only when the currency is not the euro', async () => {
    serve()
    vi.mocked(api.POST).mockImplementation((() => ok(purchase())) as never)
    await open()
    expect(screen.queryByLabelText('Importo in valuta')).not.toBeInTheDocument()

    await userEvent.clear(screen.getByLabelText('Valuta'))
    await userEvent.type(screen.getByLabelText('Valuta'), 'usd')
    await userEvent.type(await screen.findByLabelText('Importo in valuta'), '110')
    await userEvent.type(screen.getByLabelText('Cambio'), '1.1')
    await fillCommon()
    await userEvent.type(screen.getByLabelText(/importo \(€\)/i), '100')
    await userEvent.click(screen.getByRole('button', { name: /^salva$/i }))

    await waitFor(() => expect(api.POST).toHaveBeenCalled())
    expect(postedBody().body).toMatchObject({
      currency: 'USD',
      currency_amount: '110',
      exchange_rate: '1.1',
    })
  })
})

describe('editing, annulling and archiving', () => {
  function serve(items: unknown[]) {
    respond({
      '/api/purchases': () => ok({ items, next_cursor: null }),
      '/api/cost-categories': () => ok([CATEGORY]),
      '/api/schema/{entity_type}': () => ok(EMPTY_SCHEMA),
    })
  }

  it('patches only what changed and never the kind', async () => {
    serve([purchase()])
    vi.mocked(api.PATCH).mockImplementation((() => ok(purchase())) as never)
    renderPanel()
    await userEvent.click(await screen.findByText('Licenza'))
    await screen.findByRole('dialog')
    expect(screen.getByRole('combobox', { name: 'Tipo' })).toBeDisabled()
    const description = screen.getByLabelText(/descrizione/i)
    await userEvent.clear(description)
    await userEvent.type(description, 'Licenza annuale')
    await userEvent.click(screen.getByRole('button', { name: /^salva$/i }))

    await waitFor(() => expect(api.PATCH).toHaveBeenCalled())
    const [path, options] = vi.mocked(api.PATCH).mock.calls[0] as unknown as [
      string,
      { params: { path: { purchase_id: string } }; body: Record<string, unknown> },
    ]
    expect(path).toBe('/api/purchases/{purchase_id}')
    expect(options.params.path.purchase_id).toBe('c1')
    expect(options.body).toMatchObject({ description: 'Licenza annuale' })
    expect(options.body).not.toHaveProperty('kind')
    expect(options.body).not.toHaveProperty('vat_lines')
  })

  it('annuls a registered purchase after a confirmation', async () => {
    serve([purchase()])
    vi.mocked(api.POST).mockImplementation((() => ok(purchase({ status: 'cancelled' }))) as never)
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    renderPanel()
    await userEvent.click(await screen.findByText('Licenza'))
    await userEvent.click(await screen.findByRole('button', { name: 'Annulla acquisto' }))

    await waitFor(() => expect(api.POST).toHaveBeenCalled())
    expect(confirm).toHaveBeenCalled()
    expect(vi.mocked(api.POST).mock.calls[0]?.[0]).toBe('/api/purchases/{purchase_id}/cancel')
    confirm.mockRestore()
  })

  it('shows an annulled row with a muted badge and offers no second annulment', async () => {
    serve([purchase({ status: 'cancelled' })])
    renderPanel()
    expect(await screen.findByText('Annullato')).toHaveAttribute('data-tone', 'muted')
    await userEvent.click(screen.getByText('Licenza'))
    await screen.findByRole('dialog')
    expect(screen.queryByRole('button', { name: 'Annulla acquisto' })).not.toBeInTheDocument()
  })

  it('archives through DELETE after a confirmation', async () => {
    serve([purchase()])
    vi.mocked(api.DELETE).mockImplementation((() => ok(undefined)) as never)
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    renderPanel()
    await userEvent.click(await screen.findByText('Licenza'))
    await userEvent.click(await screen.findByRole('button', { name: 'Elimina' }))
    await waitFor(() => expect(api.DELETE).toHaveBeenCalled())
    expect(vi.mocked(api.DELETE).mock.calls[0]?.[0]).toBe('/api/purchases/{purchase_id}')
    confirm.mockRestore()
  })
})
