import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { TimePage } from './TimePage'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PATCH: vi.fn(), DELETE: vi.fn() } }
})

const USER = '33333333-3333-7333-8333-333333333333'
const DEAL = 'aaaaaaaa-aaaa-7aaa-8aaa-aaaaaaaaaaaa'

const session = vi.hoisted(() => ({
  user: { id: '33333333-3333-7333-8333-333333333333', ruolo: 'admin' } as {
    id: string
    ruolo: string
  } | null,
}))

vi.mock('@/lib/auth', () => ({
  useCanWrite: () => true,
  useAuth: () => ({ user: session.user, isLoading: false, login: vi.fn(), logout: vi.fn() }),
}))

vi.mock('@rebase/ui/sonner', () => ({ toast: { info: vi.fn(), success: vi.fn(), error: vi.fn() } }))

const deal = {
  id: DEAL,
  nome: 'Progetto Alfa',
  customer_id: '11111111-1111-7111-8111-111111111111',
  pipeline_stage_id: '22222222-2222-7222-8222-222222222222',
  valore_previsto: null,
  probabilita: 10,
  data_chiusura_prevista: null,
  owner_id: null,
  note: null,
  ore_preventivate: null,
  valore_preventivato: null,
  tariffa_oraria: null,
  chiuso_il: null,
  custom_fields: {},
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
}

const entry = {
  id: 'e1',
  deal_id: DEAL,
  user_id: USER,
  data: '2026-03-09',
  ore: '2.50',
  descrizione: 'Analisi',
  fatturabile: true,
  tariffa_applicata: null,
  costo_applicato: null,
  tariffa_origine: 'assente',
  costo_origine: 'assente',
  valore_riga: null,
  costo_riga: null,
  invoice_line_id: null,
  note_interne: null,
  custom_fields: {},
  created_at: '2026-03-09T09:00:00Z',
  updated_at: '2026-03-09T09:00:00Z',
}

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) }) as never
}
function failed(error: unknown, status: number) {
  return Promise.resolve({ error, response: new Response(null, { status }) }) as never
}

/** Routes by templated path; the timer and the schema answer their idle shapes unless a
 *  test says otherwise, so a case about the week never has to mention them. */
function routeGet(responses: Record<string, () => ReturnType<typeof ok>> = {}) {
  vi.mocked(api.GET).mockImplementation(((path: string) => {
    const response = responses[path]
    if (response) return response()
    if (path === '/api/deals') return ok({ items: [deal], next_cursor: null })
    if (path === '/api/time-entries') return ok({ items: [], next_cursor: null })
    if (path === '/api/time-entries/timer') return ok(null)
    if (path.includes('field-definitions') || path.includes('schema')) return ok({ custom_fields: [] })
    throw new Error(`unexpected GET ${path}`)
  }) as never)
}

function getCalls(): [string, { params: { query: Record<string, unknown> } }][] {
  return vi.mocked(api.GET).mock.calls as unknown as [
    string,
    { params: { query: Record<string, unknown> } },
  ][]
}

beforeAll(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date(2026, 2, 12)) // Thursday 12 March 2026
})
afterAll(() => vi.useRealTimers())

const typist = () => userEvent.setup({ advanceTimers: vi.advanceTimersByTime })

beforeEach(() => {
  session.user = { id: USER, ruolo: 'admin' }
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
})

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <TimePage />
    </QueryClientProvider>,
  )
}

describe('TimePage', () => {
  it('opens with its title, the week controls and the two tabs, on the register', async () => {
    routeGet()
    renderPage()
    expect(await screen.findByRole('heading', { level: 1, name: 'Ore' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Questa settimana' })).toBeInTheDocument()
    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual([
      'Registro',
      'Settimana',
    ])
    // The register is the first view: the bar that logs hours by hand is on it, and the
    // stopwatch one click behind it.
    expect(await screen.findByRole('button', { name: /aggiungi/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /usa il timer/i })).toBeInTheDocument()
  })

  it('moves a whole week at a time', async () => {
    routeGet()
    renderPage()
    expect(await screen.findByText(/9 marzo/i)).toBeInTheDocument()
    await typist().click(screen.getByRole('button', { name: /settimana precedente/i }))
    expect(await screen.findByText(/2 marzo/i)).toBeInTheDocument()
  })

  it('asks the API for this user, this week, and nothing wider', async () => {
    routeGet()
    renderPage()
    await screen.findByTestId('week-total')
    const call = getCalls().find(([path]) => path === '/api/time-entries')
    expect(call?.[1].params.query).toMatchObject({
      user_id: USER,
      da: '2026-03-09',
      a: '2026-03-15',
    })
  })

  it('asks for nothing at all until the session is known', async () => {
    session.user = null
    routeGet()
    renderPage()
    await screen.findByRole('heading', { level: 1, name: 'Ore' })
    await waitFor(() => expect(getCalls().some(([path]) => path === '/api/deals')).toBe(true))
    expect(getCalls().some(([path]) => path === '/api/time-entries')).toBe(false)
  })

  it('renders a banner and neither view when the request fails', async () => {
    routeGet({ '/api/time-entries': () => failed({ detail: 'Boom' }, 500) })
    renderPage()
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(screen.queryByTestId('week-total')).not.toBeInTheDocument()
    expect(screen.queryByTestId('grid-total')).not.toBeInTheDocument()
  })

  it('shows the same rows as a register and as a grid, one tab apart', async () => {
    routeGet({ '/api/time-entries': () => ok({ items: [entry], next_cursor: null }) })
    renderPage()
    expect(await screen.findByText('Analisi')).toBeInTheDocument()
    expect(screen.getByTestId('week-total')).toHaveTextContent('2,5 h')

    await typist().click(screen.getByRole('tab', { name: 'Settimana' }))
    expect(await screen.findByTestId('grid-total')).toHaveTextContent('2,5')
    // One read served both: switching tabs asked for nothing new.
    expect(getCalls().filter(([path]) => path === '/api/time-entries')).toHaveLength(1)
  })
})
