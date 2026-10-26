import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { EntityAttivita } from './EntityAttivita'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PATCH: vi.fn(), DELETE: vi.fn() } }
})

vi.mock('@/lib/auth', () => ({
  useCanWrite: () => true,
  useIsAdmin: () => true,
  useAuth: () => ({ user: { id: 'me', nome: 'Lorenzo', ruolo: 'admin' }, isLoading: false }),
}))

vi.mock('@rebase/ui/sonner', () => ({ toast: { info: vi.fn(), success: vi.fn(), error: vi.fn() } }))

const DEAL = 'aaaaaaaa-aaaa-7aaa-8aaa-aaaaaaaaaaaa'

function row(over: Record<string, unknown>) {
  return {
    id: 'a',
    titolo: 'x',
    note: null,
    scadenza: null,
    stato: 'aperta',
    completata_il: null,
    assegnata_a: null,
    customer_id: null,
    person_id: null,
    deal_id: DEAL,
    invoice_id: null,
    origine: 'manuale',
    regola: null,
    custom_fields: {},
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z',
    ...over,
  }
}

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) }) as never
}

function renderTab(link = { kind: 'deal' as const, id: DEAL }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <EntityAttivita link={link} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
})

describe('EntityAttivita', () => {
  it('asks only for this record, lists open ones before closed ones', async () => {
    vi.mocked(api.GET).mockImplementation(((path: string) => {
      if (path === '/api/activities')
        return ok({
          items: [
            row({ id: '1', titolo: 'Fatta ieri', stato: 'completata', completata_il: '2026-10-25' }),
            row({ id: '2', titolo: 'Preparare le proposte', scadenza: '2026-10-30' }),
          ],
          next_cursor: null,
        })
      if (path === '/api/users') return ok([])
      throw new Error(`unexpected GET ${path}`)
    }) as never)
    renderTab()

    expect(await screen.findByText('Preparare le proposte')).toBeInTheDocument()
    const call = vi.mocked(api.GET).mock.calls.find(([path]) => path === '/api/activities')
    expect(call?.[1]).toMatchObject({ params: { query: { deal_id: DEAL } } })
    expect(screen.getByText('Entro il 30/10/2026')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Chiuse' })).toBeInTheDocument()
    const titles = screen.getAllByRole('listitem').map((item) => item.textContent ?? '')
    expect(titles.findIndex((text) => text.includes('Preparare'))).toBeLessThan(
      titles.findIndex((text) => text.includes('Fatta ieri')),
    )
  })

  it('says so when there is none, and adds one linked to the record', async () => {
    vi.mocked(api.GET).mockImplementation(((path: string) => {
      if (path === '/api/activities') return ok({ items: [], next_cursor: null })
      if (path === '/api/users') return ok([])
      if (path === '/api/deals/{deal_id}') return ok({ id: DEAL, nome: 'FloreMoria sito' })
      throw new Error(`unexpected GET ${path}`)
    }) as never)
    vi.mocked(api.POST).mockImplementation((() => ok(row({}))) as never)
    renderTab()

    expect(await screen.findByText('Nessuna attività collegata.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /Nuova attività/ }))
    await userEvent.type(await screen.findByLabelText('Titolo'), 'Preparare le proposte')
    await userEvent.click(screen.getByRole('button', { name: 'Aggiungi' }))

    await waitFor(() => expect(api.POST).toHaveBeenCalled())
    const [path, options] = vi.mocked(api.POST).mock.calls[0] as unknown as [
      string,
      { body: Record<string, unknown> },
    ]
    expect(path).toBe('/api/activities')
    expect(options.body).toMatchObject({ deal_id: DEAL, customer_id: null })
  })
})
