import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { ContractsTab } from './ContractsTab'

const navigate = vi.fn()
vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return { ...actual, useNavigate: () => navigate }
})
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PUT: vi.fn(), PATCH: vi.fn() } }
})
vi.mock('@rebase/ui/sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))

let canCreate = true
vi.mock('@/lib/auth', () => ({ useCan: () => canCreate }))

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) }) as never
}

const CONTRACT = {
  id: 'c1',
  customer_id: 'cust-1',
  titolo: 'Sviluppo piattaforma',
  inizio: '2026-01-01',
  fine: null,
  tipo_rinnovo: 'tacito',
  cadenza_fatturazione: 'mensile',
  stato: 'attivo',
  custom_fields: {},
}

function mockGets(items: unknown[]) {
  vi.mocked(api.GET).mockImplementation(((path: string) => {
    if (path === '/api/contracts') return ok({ items, next_cursor: null })
    if (path === '/api/schema/{entity_type}') {
      return ok({ entity_type: 'contract', native_fields: [], custom_fields: [] })
    }
    return ok({ items: [], next_cursor: null })
  }) as never)
}

function renderTab() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ContractsTab customerId="cust-1" />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  navigate.mockReset()
  canCreate = true
})

describe('ContractsTab', () => {
  it('lists the customer’s contracts with their state and term', async () => {
    mockGets([CONTRACT])
    renderTab()
    expect(await screen.findByText('Sviluppo piattaforma')).toBeInTheDocument()
    expect(screen.getByText('Attivo')).toBeInTheDocument()
    expect(screen.getByText('Rinnovo tacito')).toBeInTheDocument()
    expect(screen.getByText('senza scadenza')).toBeInTheDocument()
  })

  it('asks only for this customer’s contracts', async () => {
    mockGets([])
    renderTab()
    await screen.findByText('Nessun contratto per questo cliente.')
    const call = vi.mocked(api.GET).mock.calls.find((c) => c[0] === '/api/contracts')
    const options = call?.[1] as unknown as { params: { query: Record<string, unknown> } }
    expect(options.params.query).toMatchObject({ customer_id: 'cust-1' })
  })

  it('opens a contract’s page from its row', async () => {
    mockGets([CONTRACT])
    renderTab()
    await userEvent.click(await screen.findByText('Sviluppo piattaforma'))
    expect(navigate).toHaveBeenCalledWith({
      to: '/app/contracts/$contractId',
      params: { contractId: 'c1' },
    })
  })

  it('creates a contract for this customer and lands on its page', async () => {
    mockGets([])
    vi.mocked(api.POST).mockImplementation((() => ok({ ...CONTRACT, id: 'new-1', stato: 'bozza' })) as never)
    renderTab()
    await userEvent.click(await screen.findByRole('button', { name: 'Nuovo contratto' }))
    await userEvent.type(screen.getByLabelText('Titolo'), 'Manutenzione')
    await userEvent.type(screen.getByLabelText('Inizio'), '2026-11-01')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    expect(api.POST).toHaveBeenCalledWith(
      '/api/contracts',
      expect.objectContaining({
        body: expect.objectContaining({
          customer_id: 'cust-1',
          titolo: 'Manutenzione',
          inizio: '2026-11-01',
          tipo_rinnovo: 'nessuno',
          preavviso_disdetta_giorni: 30,
        }),
      }),
    )
    await vi.waitFor(() =>
      expect(navigate).toHaveBeenCalledWith({
        to: '/app/contracts/$contractId',
        params: { contractId: 'new-1' },
      }),
    )
  })

  it('offers no way to create to someone who cannot write', async () => {
    canCreate = false
    mockGets([CONTRACT])
    renderTab()
    await screen.findByText('Sviluppo piattaforma')
    expect(screen.queryByRole('button', { name: 'Nuovo contratto' })).not.toBeInTheDocument()
  })
})
