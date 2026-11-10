import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
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

const navigate = vi.fn()
vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return { ...actual, useNavigate: () => navigate }
})

const auth = { canWrite: true }
vi.mock('@/lib/auth', () => ({
  useCanWrite: () => auth.canWrite,
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
function renderPanel(dealId: string | null = DEAL) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <PurchasesPanel dealId={dealId ?? undefined} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  navigate.mockReset()
  auth.canWrite = true
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

  it('opens «Nuovo acquisto» on the deal instead of a dialog, so a deal expense is never filed as a general one', async () => {
    respond({
      '/api/purchases': () => ok({ items: [], next_cursor: null }),
      '/api/cost-categories': () => ok([CATEGORY]),
      '/api/schema/{entity_type}': () => ok(EMPTY_SCHEMA),
    })
    renderPanel()

    await userEvent.click(await screen.findByRole('button', { name: /registra acquisto/i }))
    expect(navigate).toHaveBeenCalledWith({ to: '/app/purchases/new', search: { deal_id: DEAL } })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('opens «Nuovo acquisto» with no deal from the general list', async () => {
    respond({
      '/api/purchases': () => ok({ items: [], next_cursor: null }),
      '/api/cost-categories': () => ok([CATEGORY]),
      '/api/schema/{entity_type}': () => ok(EMPTY_SCHEMA),
    })
    renderPanel(null)

    await userEvent.click(await screen.findByRole('button', { name: /registra acquisto/i }))
    expect(navigate).toHaveBeenCalledWith({ to: '/app/purchases/new', search: { deal_id: undefined } })
  })

  it('opens the purchase\'s own page from a row, for anyone who may read', async () => {
    auth.canWrite = false
    respond({
      '/api/purchases': () => ok({ items: [purchase()], next_cursor: null }),
      '/api/cost-categories': () => ok([CATEGORY]),
      '/api/schema/{entity_type}': () => ok(EMPTY_SCHEMA),
    })
    renderPanel()

    await userEvent.click(await screen.findByText('Licenza'))
    expect(navigate).toHaveBeenCalledWith({
      to: '/app/purchases/$purchaseId',
      params: { purchaseId: 'c1' },
    })
  })

  it('offers no «Registra acquisto» to a read-only member', async () => {
    auth.canWrite = false
    respond({
      '/api/purchases': () => ok({ items: [], next_cursor: null }),
      '/api/cost-categories': () => ok([CATEGORY]),
      '/api/schema/{entity_type}': () => ok(EMPTY_SCHEMA),
    })
    renderPanel()

    await screen.findByText(/nessun acquisto registrato su questo deal/i)
    expect(screen.queryByRole('button', { name: /registra acquisto/i })).not.toBeInTheDocument()
  })
})
