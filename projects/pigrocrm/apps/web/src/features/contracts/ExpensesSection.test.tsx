import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { ExpensesSection } from './ExpensesSection'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PUT: vi.fn(), PATCH: vi.fn() } }
})
vi.mock('@rebase/ui/sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))
vi.mock('@/lib/auth', () => ({ useCan: () => true }))

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) }) as never
}

const CATEGORIES = [
  { id: 'cat-1', nome: 'Trasferte', archiviata: false },
  { id: 'cat-2', nome: 'Vecchia', archiviata: true },
]
const base = {
  contract_id: 'c1',
  category_id: 'cat-1',
  data: '2026-03-05',
  pre_autorizzata: false,
  riferimento_autorizzazione: null,
  document_id: null,
}
const WAITING = { ...base, id: 'e1', importo: '42.00', descrizione: 'Treno', rimborsabile: true, invoice_line_id: null }
const BILLED = { ...base, id: 'e2', importo: '10.00', descrizione: 'Taxi', rimborsabile: true, invoice_line_id: 'l1' }
const NOT_REBILLABLE = { ...base, id: 'e3', importo: '5.00', descrizione: 'Caffè', rimborsabile: false, invoice_line_id: null }

function renderSection() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ExpensesSection contractId="c1" />
    </QueryClientProvider>,
  )
}

function mockGets(expenses: unknown[]) {
  vi.mocked(api.GET).mockImplementation(((path: string) =>
    path === '/api/cost-categories' ? ok(CATEGORIES) : ok(expenses)) as never)
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  vi.mocked(api.PATCH).mockReset()
})

describe('ExpensesSection', () => {
  it('says which expenses wait to be invoiced, which are on an invoice and which cannot be', async () => {
    mockGets([WAITING, BILLED, NOT_REBILLABLE])
    renderSection()
    expect(await screen.findByText('Treno')).toBeInTheDocument()
    expect(screen.getByText('Da fatturare')).toBeInTheDocument()
    expect(screen.getByText('In fattura')).toBeInTheDocument()
    expect(screen.getByText('Non rimborsabile')).toBeInTheDocument()
  })

  it('offers no edit on an expense that is already on an invoice', async () => {
    mockGets([WAITING, BILLED])
    renderSection()
    await screen.findByText('Taxi')
    expect(screen.getByRole('button', { name: 'Modifica la spesa Treno' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Modifica la spesa Taxi' })).not.toBeInTheDocument()
  })

  it('records an expense and sends the amount as the string typed', async () => {
    mockGets([])
    vi.mocked(api.POST).mockImplementation((() => ok(WAITING)) as never)
    renderSection()
    await userEvent.click(await screen.findByRole('button', { name: 'Nuova spesa' }))
    const dialog = screen.getByRole('dialog')
    await userEvent.click(within(dialog).getByLabelText('Categoria'))
    await userEvent.click(await screen.findByRole('option', { name: 'Trasferte' }))
    await userEvent.type(within(dialog).getByLabelText('Importo (€)'), '42,00')
    await userEvent.type(within(dialog).getByLabelText('Descrizione'), 'Treno')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Salva' }))

    expect(api.POST).toHaveBeenCalledWith(
      '/api/contracts/{contract_id}/expenses',
      expect.objectContaining({
        params: { path: { contract_id: 'c1' } },
        body: expect.objectContaining({
          category_id: 'cat-1',
          importo: '42.00',
          descrizione: 'Treno',
          pre_autorizzata: false,
          riferimento_autorizzazione: null,
        }),
      }),
    )
  })

  it('does not offer an archived category for a new expense', async () => {
    mockGets([])
    renderSection()
    await userEvent.click(await screen.findByRole('button', { name: 'Nuova spesa' }))
    await userEvent.click(within(screen.getByRole('dialog')).getByLabelText('Categoria'))
    expect(await screen.findByRole('option', { name: 'Trasferte' })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: 'Vecchia' })).not.toBeInTheDocument()
  })

  it('edits an expense without resending its category, which may be archived since', async () => {
    mockGets([{ ...WAITING, category_id: 'cat-2' }])
    vi.mocked(api.PATCH).mockImplementation((() => ok(WAITING)) as never)
    renderSection()
    await userEvent.click(await screen.findByRole('button', { name: 'Modifica la spesa Treno' }))
    const dialog = screen.getByRole('dialog')
    const importo = within(dialog).getByLabelText('Importo (€)')
    await userEvent.clear(importo)
    await userEvent.type(importo, '50.00')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Salva' }))

    const call = vi.mocked(api.PATCH).mock.calls[0]
    const body = (call?.[1] as unknown as { body: Record<string, unknown> }).body
    expect(body.importo).toBe('50.00')
    expect('category_id' in body).toBe(false)
  })
})
