import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AziendaContext, type AziendaRecord, type AziendaValue } from '@/lib/azienda'
import { api } from '@/lib/api'
import { CostsPanel } from './CostsPanel'

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
    user: { id: '33333333-3333-7333-8333-333333333333', ruolo: 'admin' },
    isLoading: false,
    login: vi.fn(),
    logout: vi.fn(),
  }),
}))

vi.mock('@rebase/ui/sonner', () => ({ toast: { info: vi.fn(), success: vi.fn(), error: vi.fn() } }))

const DEAL = 'aaaaaaaa-aaaa-7aaa-8aaa-aaaaaaaaaaaa'

const cost = (overrides: Record<string, unknown> = {}) => ({
  id: 'c1',
  deal_id: DEAL,
  category_id: 'cat1',
  data: '2026-03-05',
  importo: '2500.50',
  descrizione: 'Licenza',
  fornitore: 'ACME',
  document_id: null,
  custom_fields: {},
  created_at: '2026-03-05T00:00:00Z',
  updated_at: '2026-03-05T00:00:00Z',
  ...overrides,
})

const CATEGORY = {
  id: 'cat1',
  nome: 'Software e licenze',
  posizione: 1,
  code: 'software_licenze',
  archiviata: false,
  created_at: 'x',
  updated_at: 'x',
}

const EMPTY_SCHEMA = { entity_type: 'cost', native_fields: [], custom_fields: [] }

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

/** `null` is the general-costs panel, the one with no deal: a defaulted parameter
 *  cannot be handed `undefined` to mean that, it would take the default instead. */
function renderPanel(dealId: string | null = DEAL, azienda?: AziendaValue) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const panel = <CostsPanel dealId={dealId ?? undefined} />
  return render(
    <QueryClientProvider client={client}>
      {azienda ? <AziendaContext value={azienda}>{panel}</AziendaContext> : panel}
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  vi.mocked(api.PATCH).mockReset()
  vi.mocked(api.DELETE).mockReset()
})

describe('CostsPanel', () => {
  it('formats amounts with a forced thousands separator and resolves the category name', async () => {
    respond({
      '/api/costs': () => ok({ items: [cost()], next_cursor: null }),
      '/api/cost-categories': () => ok([CATEGORY]),
      '/api/schema/{entity_type}': () => ok(EMPTY_SCHEMA),
    })
    renderPanel()
    expect(await screen.findByText('2.500,50 €')).toBeInTheDocument()
    expect(await screen.findByText('Software e licenze')).toBeInTheDocument()
  })

  it('shows a negative amount as a refund rather than as an error', async () => {
    respond({
      '/api/costs': () => ok({ items: [cost({ importo: '-45.50' })], next_cursor: null }),
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
      '/api/costs': () => failed({ detail: 'Boom' }, 500),
      '/api/cost-categories': () => ok([]),
      '/api/schema/{entity_type}': () => ok(EMPTY_SCHEMA),
    })
    renderPanel()
    expect(await screen.findByRole('alert')).toHaveTextContent('Boom')
    expect(screen.queryByText(/nessun costo/i)).not.toBeInTheDocument()
  })

  it('sends the deal id with the cost, so a deal expense is never filed as a general one', async () => {
    respond({
      '/api/costs': () => ok({ items: [], next_cursor: null }),
      '/api/cost-categories': () => ok([CATEGORY]),
      '/api/schema/{entity_type}': () => ok(EMPTY_SCHEMA),
    })
    vi.mocked(api.POST).mockImplementation((() => ok(cost())) as never)
    renderPanel()

    await userEvent.click(await screen.findByRole('button', { name: /registra costo/i }))
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
    expect(path).toBe('/api/costs')
    // The amount travels as the string the user typed, never through a JS float: the
    // whole point of §4's decimal discipline is that no economic value is born here.
    // `category_id` is the category's UUID and not its name -- a rename must not
    // orphan the cost.
    expect(options.body).toMatchObject({
      deal_id: DEAL,
      category_id: 'cat1',
      importo: '120',
      descrizione: 'Hosting',
    })
  })
})

describe('the «Azienda» picker of a general expense (REB-626)', () => {
  const HUMANCRAFT = { id: 'a1', nome: 'humancraft', attiva: true } as AziendaRecord
  const REBASE = { id: 'a2', nome: 'rebase', attiva: true } as AziendaRecord
  const TWO: AziendaValue = {
    aziende: [HUMANCRAFT, REBASE],
    selected: null,
    select: vi.fn(),
    several: true,
    byId: (id) => [HUMANCRAFT, REBASE].find((a) => a.id === id),
  }

  function serveEmpty() {
    respond({
      '/api/costs': () => ok({ items: [], next_cursor: null }),
      '/api/cost-categories': () => ok([CATEGORY]),
      '/api/schema/{entity_type}': () => ok(EMPTY_SCHEMA),
    })
  }

  it('offers «Condivisa» first for a cost with no deal, from the second azienda on', async () => {
    serveEmpty()
    renderPanel(null, TWO)
    await userEvent.click(await screen.findByRole('button', { name: /Registra costo/ }))
    const picker = await screen.findByRole('combobox', { name: 'Azienda' })
    expect(picker).toHaveTextContent('Condivisa')
    await userEvent.click(picker)
    expect(screen.getAllByRole('option').map((option) => option.textContent)).toEqual([
      'Condivisa',
      'humancraft',
      'rebase',
    ])
  })

  it('draws nothing for a cost on a deal, nor in a one-azienda space', async () => {
    serveEmpty()
    renderPanel(DEAL, TWO)
    await userEvent.click(await screen.findByRole('button', { name: /Registra costo/ }))
    await screen.findByRole('dialog')
    expect(screen.queryByRole('combobox', { name: 'Azienda' })).not.toBeInTheDocument()
  })

  it('draws nothing for a general expense in a one-azienda space', async () => {
    serveEmpty()
    renderPanel(null)
    await userEvent.click(await screen.findByRole('button', { name: /Registra costo/ }))
    await screen.findByRole('dialog')
    expect(screen.queryByRole('combobox', { name: 'Azienda' })).not.toBeInTheDocument()
  })
})

describe('a custom cost field that happens to be named azienda_id (CodeRabbit, PR #509)', () => {
  const HUMANCRAFT = { id: 'a1', nome: 'humancraft', attiva: true } as AziendaRecord
  const REBASE = { id: 'a2', nome: 'rebase', attiva: true } as AziendaRecord
  const TWO: AziendaValue = {
    aziende: [HUMANCRAFT, REBASE],
    selected: null,
    select: vi.fn(),
    several: true,
    byId: (id) => [HUMANCRAFT, REBASE].find((a) => a.id === id),
  }

  it('never shows the owning azienda\'s id as the custom field\'s value', async () => {
    respond({
      '/api/costs': () =>
        ok({ items: [cost({ deal_id: null, azienda_id: 'a1', custom_fields: {} })], next_cursor: null }),
      '/api/cost-categories': () => ok([CATEGORY]),
      '/api/schema/{entity_type}': () =>
        ok({
          entity_type: 'cost',
          native_fields: [],
          custom_fields: [
            { key: 'azienda_id', label: 'Codice interno', type: 'text', required: false, options: [] },
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
