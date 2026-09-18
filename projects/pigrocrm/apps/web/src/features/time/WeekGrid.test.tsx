import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import type { Deal } from '@/features/deals/queries'
import type { TimeEntry } from './queries'
import { WeekGrid } from './WeekGrid'
import { weekDays } from './week'

/**
 * `api` is an `openapi-fetch` client built at import time, and `createClient()` captures
 * `globalThis.fetch` into a closure right there -- so neither a `fetch` stub nor a
 * network-level interceptor installed later ever sees a request made through it. Every
 * test in this codebase mocks the module instead (`features/settings/EmitterPanel.test.tsx`
 * is the canonical shape), which also keeps `toProblem`/`unwrap` real: what is under test
 * is this grid's handling of what the real error normalisation produces.
 */
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PATCH: vi.fn(), DELETE: vi.fn() } }
})

const USER = '33333333-3333-7333-8333-333333333333'

// `vi.hoisted`, not a plain `let`: the mock factory below is evaluated during the import
// phase, before this module's own body runs, so a bare binding would still be in its
// temporal dead zone when the factory closes over it.
const session = vi.hoisted(() => ({
  user: { id: '33333333-3333-7333-8333-333333333333', ruolo: 'admin' } as {
    id: string
    ruolo: string
  } | null,
}))

// `useAuth` throws outside `AuthProvider`, and mounting the real provider would only add
// a `GET /api/auth/me` to every case below. Mirrors `TimeEntriesTab.test.tsx`'s own stub.
vi.mock('@/lib/auth', () => ({
  useCanWrite: () => true,
  useAuth: () => ({ user: session.user, isLoading: false, login: vi.fn(), logout: vi.fn() }),
}))

vi.mock('@rebase/ui/sonner', () => ({ toast: { info: vi.fn(), success: vi.fn(), error: vi.fn() } }))

const DEAL = 'aaaaaaaa-aaaa-7aaa-8aaa-aaaaaaaaaaaa'

const deal = (id: string, nome: string) => ({
  id,
  nome,
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
  custom_fields: {},
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
})

const timeEntry = (overrides: Record<string, unknown> = {}) => ({
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
  deleted_at: null,
  ...overrides,
})

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) }) as never
}




// Fake timers so "this week" is a fixed week, with `shouldAdvanceTime` because React
// Query and `userEvent` both need the clock to keep moving; `advanceTimers` hands
// `userEvent` the same mocked clock so its internal delays are not a real-time wait.
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
  vi.mocked(api.PATCH).mockReset()
  vi.mocked(api.DELETE).mockReset()
})

/** The grid is the table alone since 2026-09-09: `TimePage` owns the week and the two
 *  reads and hands the rows down, so this renders it the way the page does. */
function renderGrid(entries: ReturnType<typeof timeEntry>[] = []) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <WeekGrid
        days={weekDays(new Date())}
        userId={USER}
        entries={entries as unknown as TimeEntry[]}
        deals={[deal(DEAL, 'Progetto Alfa')] as unknown as Deal[]}
      />
    </QueryClientProvider>,
  )
}

describe('WeekGrid', () => {
  it('shows a row total, a column total and a grand total summed in integer hundredths', async () => {
    renderGrid([timeEntry(), timeEntry({ id: 'e2', data: '2026-03-11', ore: '1.25' })])
    expect(await screen.findByText('Progetto Alfa')).toBeInTheDocument()
    expect(await screen.findByTestId(`row-total-${DEAL}`)).toHaveTextContent('3,75')
    expect(await screen.findByTestId('column-total-2026-03-09')).toHaveTextContent('2,5')
    expect(await screen.findByTestId('grid-total')).toHaveTextContent('3,75')
  })

  it('typing in an empty cell logs an hour for that deal and that day', async () => {
    const posted: unknown[] = []
    vi.mocked(api.POST).mockImplementation(((_path: string, init: { body: unknown }) => {
      posted.push(init.body)
      return ok(timeEntry({ id: 'new', ore: '3.5' }))
    }) as never)

    renderGrid()
    const cell = await screen.findByLabelText(/Progetto Alfa, lunedì 9 marzo/i)
    await typist().type(cell, '3,5')
    await typist().tab()
    await waitFor(() => expect(posted).toHaveLength(1))
    // The comma the Italian keyboard produces is normalised to the dot the API's
    // Decimal parser wants. `user_id` travels with it: `TimeEntryCreate` requires it,
    // and the grid is the logged-in user's own week.
    expect(posted[0]).toMatchObject({
      deal_id: DEAL,
      data: '2026-03-09',
      ore: '3.5',
      user_id: USER,
    })
  })

  it('clearing a cell deletes its entry instead of logging zero hours', async () => {
    vi.mocked(api.DELETE).mockImplementation((() => ok(null)) as never)

    renderGrid([timeEntry()])
    const cell = await screen.findByLabelText(/Progetto Alfa, lunedì 9 marzo/i)
    expect(cell).toHaveValue('2,50')
    const user = typist()
    await user.clear(cell)
    await user.tab()
    // `ore > 0` is the rule (§2.2), so an emptied cell is a reversible soft delete and
    // never a zero-hour row -- and never a POST either.
    await waitFor(() => expect(vi.mocked(api.DELETE)).toHaveBeenCalledTimes(1))
    expect(vi.mocked(api.POST)).not.toHaveBeenCalled()
  })

  it('edits an existing entry through PATCH rather than logging a second one', async () => {
    vi.mocked(api.PATCH).mockImplementation((() => ok(timeEntry({ ore: '4' }))) as never)

    renderGrid([timeEntry()])
    const cell = await screen.findByLabelText(/Progetto Alfa, lunedì 9 marzo/i)
    const user = typist()
    await user.clear(cell)
    await user.type(cell, '4')
    await user.tab()
    await waitFor(() => expect(vi.mocked(api.PATCH)).toHaveBeenCalledTimes(1))
    expect(vi.mocked(api.PATCH).mock.calls[0]?.[1]).toMatchObject({
      params: { path: { entry_id: 'e1' } },
      body: { ore: '4' },
    })
    expect(vi.mocked(api.POST)).not.toHaveBeenCalled()
  })

  it('writes nothing when a cell is left exactly as it was found', async () => {
    renderGrid([timeEntry()])
    const cell = await screen.findByLabelText(/Progetto Alfa, lunedì 9 marzo/i)
    const user = typist()
    await user.click(cell)
    await user.tab()
    expect(vi.mocked(api.POST)).not.toHaveBeenCalled()
    expect(vi.mocked(api.PATCH)).not.toHaveBeenCalled()
    expect(vi.mocked(api.DELETE)).not.toHaveBeenCalled()
  })
})
