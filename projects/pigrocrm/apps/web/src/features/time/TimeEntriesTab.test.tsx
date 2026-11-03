import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { TimeEntriesTab } from './TimeEntriesTab'

/**
 * `api` is an `openapi-fetch` client built at import time, and its `createClient()`
 * captures `globalThis.fetch` into a closure right there -- so neither a `fetch` stub
 * nor a network-level interceptor installed later ever sees a request made through it.
 * Every test in this codebase mocks the module instead (see
 * `features/settings/EmitterPanel.test.tsx`, the canonical shape), which also keeps
 * `toProblem`/`unwrap` real: what is under test here is this tab's handling of what the
 * real error normalisation produces, not a reimplementation of it.
 */
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PATCH: vi.fn(), DELETE: vi.fn() } }
})

// `useAuth` throws outside `AuthProvider`, and mounting the real provider would only
// add a `GET /api/auth/me` to every case below without changing what any of them
// assert. Mirrors `routes/app/deal/$dealId.test.tsx`'s own auth stub.
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

const DEAL = '11111111-1111-7111-8111-111111111111'

const entry = (overrides: Record<string, unknown> = {}) => ({
  id: '22222222-2222-7222-8222-222222222222',
  deal_id: DEAL,
  user_id: '33333333-3333-7333-8333-333333333333',
  data: '2026-03-10',
  hours: '2.50',
  description: 'Analisi',
  billable: true,
  applied_rate: '80.000000',
  applied_cost: null,
  rate_source: 'deal',
  cost_source: 'absent',
  line_value: '200.00',
  line_cost: null,
  invoice_line_id: null,
  note_interne: null,
  custom_fields: {},
  created_at: '2026-03-10T09:00:00Z',
  updated_at: '2026-03-10T09:00:00Z',
  deleted_at: null,
  ...overrides,
})

const EMPTY_SCHEMA = { entity_type: 'time_entry', native_fields: [], custom_fields: [] }

const RATES = {
  deal_id: DEAL,
  user_id: '33333333-3333-7333-8333-333333333333',
  rate: '80.000000',
  rate_source: 'deal',
  cost: null,
  cost_source: 'absent',
}

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) }) as never
}

function failed(error: unknown, status: number) {
  return Promise.resolve({ error, response: new Response(null, { status }) }) as never
}

/** Routes each mocked call by the *templated* path openapi-fetch is called with --
 *  `/api/deals/{deal_id}/rates`, never the interpolated URL -- so a mistyped path in
 *  the component surfaces as a missing handler rather than as a silently empty screen. */
function routeGet(responses: Record<string, ReturnType<typeof ok>>) {
  vi.mocked(api.GET).mockImplementation(((path: string) => {
    const response = responses[path]
    if (!response) throw new Error(`unexpected GET ${path}`)
    return response
  }) as never)
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  vi.mocked(api.PATCH).mockReset()
  vi.mocked(api.DELETE).mockReset()
})

function renderTab() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <TimeEntriesTab dealId={DEAL} />
    </QueryClientProvider>,
  )
}

describe('TimeEntriesTab', () => {
  it('shows the summary figures the API already summed, and never recomputes them', async () => {
    routeGet({
      '/api/time-entries': ok({
        items: [entry(), entry({ id: 'x', hours: '1.25' })],
        next_cursor: null,
      }),
      '/api/deals/{deal_id}/time-summary': ok({
        deal_id: DEAL,
        status: 'in_progress',
        total_hours: '3.75',
        billable_uninvoiced_hours: '3.75',
        unbilled_hours_value: '300.00',
        work_cost: '0.00',
        hours_without_rate: 1,
        entries: 2,
      }),
      '/api/schema/{entity_type}': ok(EMPTY_SCHEMA),
      '/api/deals/{deal_id}/rates': ok(RATES),
    })
    renderTab()
    // `findAllBy`: "Ore consuntivate" and "Da fatturare" both read 3,75 here, which is
    // the point -- the two figures come from two API fields that happen to agree, and
    // nothing on screen re-derived either of them from the two rows in the table.
    expect(await screen.findAllByText('3,75')).toHaveLength(2)
    // Thousands separator forced on: it-IT's default withholds it below five digits.
    expect(await screen.findByText('300,00 €')).toBeInTheDocument()
    // Unpriced hours are named with their count, never valued at zero.
    expect(await screen.findByText(/1 voce senza tariffa/i)).toBeInTheDocument()
    expect(screen.getByText(/in corso/i)).toBeInTheDocument()
  })

  it('renders a banner, never an empty table, when the request fails', async () => {
    vi.mocked(api.GET).mockImplementation(((path: string) =>
      path === '/api/schema/{entity_type}'
        ? ok(EMPTY_SCHEMA)
        : failed({ detail: 'Boom' }, 500)) as never)
    renderTab()
    // `waitFor` on the settled state, not `findByRole`: the costs panel now mounted
    // under the hours table reads the same failing `api.GET` and renders its own
    // banner, which can appear a tick before this tab's early return replaces the whole
    // tree with its one. `findByRole` resolves on that first, doomed node and then
    // asserts against a detached element -- a race, not a regression. What this test
    // was ever about is the state the screen comes to rest in.
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Boom'))
    expect(screen.queryByText(/nessuna voce/i)).not.toBeInTheDocument()
  })

  it('surfaces a closed-period conflict as the server worded it', async () => {
    routeGet({
      '/api/time-entries': ok({ items: [], next_cursor: null }),
      '/api/deals/{deal_id}/time-summary': ok({
        deal_id: DEAL,
        status: 'in_progress',
        total_hours: '0.00',
        billable_uninvoiced_hours: '0.00',
        unbilled_hours_value: '0.00',
        work_cost: '0.00',
        hours_without_rate: 0,
        entries: 0,
      }),
      '/api/schema/{entity_type}': ok(EMPTY_SCHEMA),
      '/api/deals/{deal_id}/rates': ok(RATES),
    })
    vi.mocked(api.POST).mockImplementation((() =>
      failed(
        {
          type: 'https://pigrocrm.dev/errors/conflict',
          title: 'Conflitto con lo stato attuale',
          status: 409,
          detail:
            'time_entry: il periodo marzo 2026 è chiuso: riaprilo per modificare voci datate in quel mese',
          code: 'conflict',
          instance: '/api/time-entries',
          year: 2026,
          month: 3,
        },
        409,
      )) as never)
    renderTab()
    await userEvent.click(await screen.findByRole('button', { name: /registra ore/i }))
    await userEvent.click(await screen.findByRole('button', { name: /^salva$/i }))
    // The server's own sentence, never a client-side rewording.
    await waitFor(() =>
      expect(screen.getByText(/il periodo marzo 2026 è chiuso/i)).toBeInTheDocument(),
    )
  })

  it('marks a billed entry as not editable', async () => {
    routeGet({
      '/api/time-entries': ok({
        items: [entry({ invoice_line_id: 'line-1' })],
        next_cursor: null,
      }),
      '/api/deals/{deal_id}/time-summary': ok({
        deal_id: DEAL,
        status: 'closed',
        total_hours: '2.50',
        billable_uninvoiced_hours: '0.00',
        unbilled_hours_value: '0.00',
        work_cost: '0.00',
        hours_without_rate: 0,
        entries: 1,
      }),
      '/api/schema/{entity_type}': ok(EMPTY_SCHEMA),
      '/api/deals/{deal_id}/rates': ok(RATES),
    })
    renderTab()
    expect(await screen.findByText(/fatturata/i)).toBeInTheDocument()
  })
})
