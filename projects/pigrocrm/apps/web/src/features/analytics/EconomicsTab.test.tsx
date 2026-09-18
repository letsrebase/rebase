import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { EconomicsTab } from './EconomicsTab'

/**
 * `api` is an `openapi-fetch` client built at import time, and `createClient()` captures
 * `globalThis.fetch` into a closure right there -- so no network-level interceptor
 * installed later ever sees a request made through it. This task's brief reached for
 * `msw`, which is not a dependency of this workspace and would not have helped if it
 * were; every test here mocks the module instead (`features/settings/EmitterPanel.test.
 * tsx` is the canonical shape), which keeps `toProblem`/`unwrap` real. What is under
 * test is this tab's handling of what the real error normalisation produces, not a
 * reimplementation of it.
 */
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn() } }
})

// `useIsAdmin` reads `useAuth`, which throws outside `AuthProvider`. Mutable, so the
// admin-only draft button can be checked from both sides -- mirrors
// `features/settings/SettingsLayout.test.tsx`'s own stub.
const mockAuth = { isAdmin: true }
vi.mock('@/lib/auth', () => ({ useIsAdmin: () => mockAuth.isAdmin }))

vi.mock('@rebase/ui/sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const DEAL = 'aaaaaaaa-aaaa-7aaa-8aaa-aaaaaaaaaaaa'
const CUSTOMER = 'bbbbbbbb-bbbb-7bbb-8bbb-bbbbbbbbbbbb'
const ENTRY_A = 'cccccccc-cccc-7ccc-8ccc-cccccccccccc'
const ENTRY_B = 'dddddddd-dddd-7ddd-8ddd-dddddddddddd'

const pnl = (overrides: Record<string, unknown> = {}) => ({
  deal_id: DEAL,
  stato: 'in corso',
  ricavi: '0.00',
  costi_diretti: '500.00',
  costo_lavoro: '600.00',
  margine_lordo: '-1100.00',
  margine_percentuale: null,
  ore_totali: '20.00',
  ore_fatturabili_non_fatturate: '20.00',
  valore_maturato: '2000.00',
  ore_senza_tariffa: 0,
  fatture_emesse: 0,
  ...overrides,
})

const totals = (overrides: Record<string, unknown> = {}) => ({
  ricavi: '0.00',
  costi_diretti: '0.00',
  costo_lavoro: '0.00',
  margine_lordo: '0.00',
  margine_percentuale: null,
  deal: 0,
  ...overrides,
})

const periodPnl = (overrides: Record<string, unknown> = {}) => ({
  da: '2026-01-01',
  a: '2026-12-31',
  customer_id: CUSTOMER,
  chiusi: totals({
    ricavi: '10000.00',
    costo_lavoro: '1100.00',
    margine_lordo: '8900.00',
    margine_percentuale: '89.00',
    deal: 2,
  }),
  in_corso: totals({ margine_lordo: '-1100.00', costo_lavoro: '1100.00', deal: 1 }),
  spese_generali: '450.00',
  periodo_chiuso: false,
  voci_scritte_in_ritardo: 0,
  ...overrides,
})

const entry = (overrides: Record<string, unknown> = {}) => ({
  id: ENTRY_A,
  deal_id: DEAL,
  user_id: '33333333-3333-7333-8333-333333333333',
  data: '2026-03-10',
  ore: '10.00',
  descrizione: 'Analisi',
  fatturabile: true,
  tariffa_applicata: '100.000000',
  costo_applicato: null,
  tariffa_origine: 'deal',
  costo_origine: 'assente',
  valore_riga: '1000.00',
  costo_riga: null,
  invoice_line_id: null,
  note_interne: null,
  custom_fields: {},
  created_at: '2026-03-10T09:00:00Z',
  updated_at: '2026-03-10T09:00:00Z',
  ...overrides,
})

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) }) as never
}

function failed(error: unknown, status: number) {
  return Promise.resolve({ error, response: new Response(null, { status }) }) as never
}

/** Routes each mocked call by the *templated* path openapi-fetch is called with --
 *  `/api/deals/{deal_id}/pnl`, never the interpolated URL -- so a mistyped path in the
 *  component surfaces as a thrown "unexpected GET" rather than a silently empty screen. */
function routeGet(responses: Record<string, unknown>) {
  vi.mocked(api.GET).mockImplementation(((path: string) => {
    const response = responses[path]
    if (!response) throw new Error(`unexpected GET ${path}`)
    return response
  }) as never)
}

function renderTab(props: { dealId: string } | { customerId: string }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <EconomicsTab {...props} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  mockAuth.isAdmin = true
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('EconomicsTab on a deal', () => {
  it('never prints a bare margin for a deal in progress', async () => {
    routeGet({ '/api/deals/{deal_id}/pnl': ok(pnl()) })
    renderTab({ dealId: DEAL })

    // On an `in corso` deal the margin is provisional and the accrued value sits beside
    // it under a heading of its own. The margin must never appear unqualified.
    expect(await screen.findByText(/provvisorio/i)).toBeInTheDocument()
    expect(screen.getByText('Valore maturato')).toBeInTheDocument()
    expect(screen.getByText(/non è un ricavo/i)).toBeInTheDocument()
    expect(screen.getByText('2.000,00 €')).toBeInTheDocument()
    expect(screen.queryByText(/definitivo/i)).not.toBeInTheDocument()
  })

  /** «da fatturare» is finished work not yet billed, not a closed deal: the margin is
   *  still provisional there, and reading `stato !== 'in corso'` as "done" would report
   *  a definitive loss on every deal still waiting for its invoice. */
  it('still calls the margin provisional on a deal that is only da fatturare', async () => {
    routeGet({ '/api/deals/{deal_id}/pnl': ok(pnl({ stato: 'da fatturare' })) })
    renderTab({ dealId: DEAL })

    expect(await screen.findByText(/provvisorio/i)).toBeInTheDocument()
    expect(screen.getByText('Valore maturato')).toBeInTheDocument()
  })

  it('shows a null margin percentage as "non calcolabile", not as zero per cent', async () => {
    routeGet({ '/api/deals/{deal_id}/pnl': ok(pnl()) })
    renderTab({ dealId: DEAL })

    expect(await screen.findByText(/non calcolabile/i)).toBeInTheDocument()
    expect(screen.queryByText('0,00 %')).not.toBeInTheDocument()
  })

  it('calls a closed deal figure definitive', async () => {
    routeGet({
      '/api/deals/{deal_id}/pnl': ok(
        pnl({
          stato: 'chiuso',
          ricavi: '10000.00',
          margine_lordo: '8900.00',
          margine_percentuale: '89.00',
          ore_fatturabili_non_fatturate: '0.00',
          valore_maturato: '10000.00',
          fatture_emesse: 2,
        }),
      ),
    })
    renderTab({ dealId: DEAL })

    expect(await screen.findByText(/definitivo/i)).toBeInTheDocument()
    expect(screen.getByText('89,00 %')).toBeInTheDocument()
    expect(screen.getByText('8.900,00 €')).toBeInTheDocument()
    // The accrued value stands in for revenue that has not arrived; on a closed deal
    // the revenue *is* the invoices, so the row goes away rather than restating it.
    expect(screen.queryByText('Valore maturato')).not.toBeInTheDocument()
    expect(screen.queryByText(/provvisorio/i)).not.toBeInTheDocument()
  })

  it('names the unpriced hours with their count', async () => {
    routeGet({ '/api/deals/{deal_id}/pnl': ok(pnl({ ore_senza_tariffa: 6 })) })
    renderTab({ dealId: DEAL })

    expect(await screen.findByText(/6 voci senza tariffa/i)).toBeInTheDocument()
  })

  it('says nothing about unpriced hours when there are none', async () => {
    routeGet({ '/api/deals/{deal_id}/pnl': ok(pnl()) })
    renderTab({ dealId: DEAL })

    await screen.findByText(/provvisorio/i)
    expect(screen.queryByText(/senza tariffa/i)).not.toBeInTheDocument()
  })

  it('renders a banner and no figures when the request fails', async () => {
    routeGet({ '/api/deals/{deal_id}/pnl': failed({ detail: 'Boom' }, 500) })
    renderTab({ dealId: DEAL })

    expect(await screen.findByRole('alert')).toHaveTextContent('Boom')
    expect(screen.queryByText(/margine/i)).not.toBeInTheDocument()
  })

  /** The one arithmetic-shaped decision on this screen. `Number(...) > 0` on a decimal
   *  string is exactly what `lib/no-float-money.test.ts` forbids, so the button keys on
   *  the string the API sent, and `"0.00"` is the only spelling `Numeric(12,2)` produces
   *  for "nothing left to invoice". */
  it('offers no draft button when there is nothing left to invoice', async () => {
    routeGet({ '/api/deals/{deal_id}/pnl': ok(pnl({ ore_fatturabili_non_fatturate: '0.00' })) })
    renderTab({ dealId: DEAL })

    await screen.findByText(/provvisorio/i)
    expect(screen.queryByRole('button', { name: 'Genera bozza di fattura' })).toBeNull()
  })

  it('hides the draft button from a non-admin, who could not use it', async () => {
    mockAuth.isAdmin = false
    routeGet({ '/api/deals/{deal_id}/pnl': ok(pnl()) })
    renderTab({ dealId: DEAL })

    await screen.findByText(/provvisorio/i)
    expect(screen.queryByRole('button', { name: 'Genera bozza di fattura' })).toBeNull()
  })
})

describe('EconomicsTab on a customer', () => {
  it('asks for the current calendar year, scoped to that customer', async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date(2026, 4, 17))
    routeGet({ '/api/analytics/pnl': ok(periodPnl()) })
    renderTab({ customerId: CUSTOMER })

    await waitFor(() => expect(api.GET).toHaveBeenCalled())
    const [path, options] = vi.mocked(api.GET).mock.calls[0] as unknown as [
      string,
      { params: { query: Record<string, unknown> } },
    ]
    expect(path).toBe('/api/analytics/pnl')
    // Built from local date parts, never `toISOString()`: east of Greenwich late on 31
    // December that would open the report on the year that has not started yet.
    expect(options.params.query).toEqual({
      from: '2026-01-01',
      to: '2026-12-31',
      customer_id: CUSTOMER,
      // Explicit even though it is the server's default: the request states the reading
      // the card shows, and a reader of the network tab should not have to know which
      // one "absent" means.
      base: 'emissione',
    })
  })

  /** The reading the spec recorded (slice 4 §7.1, revenue by the invoice's own date) is
   *  the one the tab opens on, and the revenue row says so beside its label. */
  it('reads revenue by emission date by default, and says so on the row', async () => {
    routeGet({ '/api/analytics/pnl': ok(periodPnl()) })
    renderTab({ customerId: CUSTOMER })

    const closed = await screen.findByRole('group', { name: /deal chiusi/i })
    expect(within(closed).getByText('per emissione')).toBeInTheDocument()
    expect(within(closed).queryByText('per competenza')).toBeNull()
    const chips = screen.getByRole('group', { name: 'Ricavi per' })
    expect(within(chips).getByRole('button', { name: 'Per emissione' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(within(chips).getByRole('button', { name: 'Per competenza' })).toHaveAttribute(
      'aria-pressed',
      'false',
    )
    expect(screen.getByText(/attribuiti alla data di emissione/i)).toBeInTheDocument()
  })

  /** ORB-61: the second reading is the server's `coalesce(competenza_da, data_emissione)`,
   *  asked for with `base=competenza`; nothing is re-attributed in the browser. */
  it('switches to the accrual reading by asking the server again with base=competenza', async () => {
    routeGet({ '/api/analytics/pnl': ok(periodPnl()) })
    renderTab({ customerId: CUSTOMER })
    await screen.findByRole('group', { name: /deal chiusi/i })

    await userEvent.click(screen.getByRole('button', { name: 'Per competenza' }))

    await waitFor(() => {
      const bases = vi
        .mocked(api.GET)
        .mock.calls.map(
          (call) =>
            (call as unknown as [string, { params: { query: Record<string, unknown> } }])[1]
              .params.query.base,
        )
      expect(bases).toContain('competenza')
    })
    expect(screen.getByRole('button', { name: 'Per competenza' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(screen.getByRole('button', { name: 'Per emissione' })).toHaveAttribute(
      'aria-pressed',
      'false',
    )
    const closed = await screen.findByRole('group', { name: /deal chiusi/i })
    expect(await within(closed).findByText('per competenza')).toBeInTheDocument()
    expect(screen.getByText(/attribuiti al periodo di competenza/i)).toBeInTheDocument()
  })

  it('keeps the two chips on screen when the request fails, so the reading can be changed back', async () => {
    routeGet({ '/api/analytics/pnl': failed({ detail: 'Periodo non leggibile' }, 503) })
    renderTab({ customerId: CUSTOMER })

    await screen.findByRole('alert')
    expect(screen.getByRole('button', { name: 'Per emissione' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Per competenza' })).toBeInTheDocument()
  })

  /** The two columns are the point of §7.4: adding a finished job's margin to a half-done
   *  one produces a figure that is neither, so there is no combined total to read by
   *  mistake and the reportable column is the one that says so. */
  it('shows closed and in-progress as two columns, marking only the reportable one', async () => {
    routeGet({ '/api/analytics/pnl': ok(periodPnl()) })
    renderTab({ customerId: CUSTOMER })

    const closed = await screen.findByRole('group', { name: /deal chiusi/i })
    const running = screen.getByRole('group', { name: /deal in corso/i })
    expect(within(closed).getByText('8.900,00 €')).toBeInTheDocument()
    expect(within(closed).getByText('89,00 %')).toBeInTheDocument()
    expect(within(closed).getByText(/dato riportabile/i)).toBeInTheDocument()
    expect(within(running).getByText('-1.100,00 €')).toBeInTheDocument()
    expect(within(running).queryByText(/dato riportabile/i)).toBeNull()
    // 8.900 + (-1.100) = 7.800: the sum nobody should be able to read off this screen.
    expect(screen.queryByText('7.800,00 €')).toBeNull()
  })

  it('says the general expenses belong to no customer', async () => {
    routeGet({ '/api/analytics/pnl': ok(periodPnl()) })
    renderTab({ customerId: CUSTOMER })

    expect(await screen.findByText(/non sono ripartite su nessun cliente/i)).toBeInTheDocument()
    // The figure is still shown -- the expense exists, it is simply attributed nowhere.
    expect(screen.getByText('450,00 €')).toBeInTheDocument()
  })

  it('renders a banner and no figures when the period request fails', async () => {
    routeGet({ '/api/analytics/pnl': failed({ detail: 'Periodo non leggibile' }, 503) })
    renderTab({ customerId: CUSTOMER })

    expect(await screen.findByRole('alert')).toHaveTextContent('Periodo non leggibile')
    expect(screen.queryByRole('group', { name: /deal chiusi/i })).toBeNull()
  })
})

describe('ToInvoiceDialog', () => {
  async function openDialog(responses: Record<string, unknown>) {
    routeGet({ '/api/deals/{deal_id}/pnl': ok(pnl()), ...responses })
    renderTab({ dealId: DEAL })
    await userEvent.click(await screen.findByRole('button', { name: 'Genera bozza di fattura' }))
  }

  const twoEntries = {
    '/api/time-entries': ok({
      items: [entry(), entry({ id: ENTRY_B, descrizione: 'Sviluppo', ore: '5.00' })],
      next_cursor: null,
    }),
  }

  it('asks only for the billable, unbilled hours of this deal', async () => {
    await openDialog(twoEntries)

    await screen.findByRole('button', { name: 'Genera' })
    const call = vi
      .mocked(api.GET)
      .mock.calls.find((entry) => entry[0] === '/api/time-entries') as unknown as [
      string,
      { params: { query: Record<string, unknown> } },
    ]
    expect(call[1].params.query).toMatchObject({
      deal_id: DEAL,
      fatturabile: true,
      fatturato: false,
    })
  })

  it('preselects every listed entry and sends their ids', async () => {
    await openDialog(twoEntries)
    vi.mocked(api.POST).mockImplementation(() => ok({ id: 'inv-1' }))

    await userEvent.click(await screen.findByRole('button', { name: 'Genera' }))

    await waitFor(() => expect(api.POST).toHaveBeenCalled())
    const [path, options] = vi.mocked(api.POST).mock.calls[0] as unknown as [
      string,
      { body: { entry_ids: string[]; raggruppa_per_mese: boolean } },
    ]
    expect(path).toBe('/api/deals/{deal_id}/time-entries/to-invoice-draft')
    expect(options.body.entry_ids).toEqual([ENTRY_A, ENTRY_B])
    expect(options.body.raggruppa_per_mese).toBe(true)
  })

  it('sends only the entries still ticked', async () => {
    await openDialog(twoEntries)
    vi.mocked(api.POST).mockImplementation(() => ok({ id: 'inv-1' }))

    await userEvent.click(await screen.findByRole('checkbox', { name: /Analisi/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Genera' }))

    await waitFor(() => expect(api.POST).toHaveBeenCalled())
    const [, options] = vi.mocked(api.POST).mock.calls[0] as unknown as [
      string,
      { body: { entry_ids: string[] } },
    ]
    expect(options.body.entry_ids).toEqual([ENTRY_B])
  })

  it('sends the grouping toggle as the boolean the API declares', async () => {
    await openDialog(twoEntries)
    vi.mocked(api.POST).mockImplementation(() => ok({ id: 'inv-1' }))

    await userEvent.click(await screen.findByRole('checkbox', { name: /Raggruppa per mese/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Genera' }))

    await waitFor(() => expect(api.POST).toHaveBeenCalled())
    const [, options] = vi.mocked(api.POST).mock.calls[0] as unknown as [
      string,
      { body: { raggruppa_per_mese: unknown } },
    ]
    expect(options.body.raggruppa_per_mese).toBe(false)
  })

  /** `BindTimeRequest.entry_ids` has `min_length=1`, so an empty list is a 422 the user
   *  can do nothing with. The button says so before the round trip instead. */
  it('refuses to send with nothing selected, rather than posting an empty list', async () => {
    await openDialog({ '/api/time-entries': ok({ items: [entry()], next_cursor: null }) })

    await userEvent.click(await screen.findByRole('checkbox', { name: /Analisi/ }))
    expect(screen.getByRole('button', { name: 'Genera' })).toBeDisabled()
    expect(api.POST).not.toHaveBeenCalled()
  })

  /** The server counts the unpriced entries and lists them in `expected`, so its refusal
   *  is an instruction -- "give these entries a rate". Rewording it here throws the list
   *  away, which is the only part that says what to do next. */
  it("shows the server's own sentence when the draft is refused", async () => {
    await openDialog({ '/api/time-entries': ok({ items: [entry()], next_cursor: null }) })
    const reason = '1 voci senza tariffa'
    vi.mocked(api.POST).mockImplementation(() =>
      failed(
        {
          type: 'https://pigrocrm.dev/errors/validation_failed',
          title: 'Dati non validi',
          status: 422,
          entity: 'time_entry',
          field: 'entry_ids',
          reason,
          expected: `dai una tariffa a: ${ENTRY_A}`,
          detail: reason,
          code: 'validation_failed',
        },
        422,
      ),
    )

    await userEvent.click(await screen.findByRole('button', { name: 'Genera' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(reason)
    expect(alert).toHaveTextContent(ENTRY_A)
    // The dialog stays open on a refusal: closing it would take the instruction with it.
    expect(screen.getByRole('button', { name: 'Genera' })).toBeInTheDocument()
  })

  /** The stale-banner defect this codebase has already fixed once. The refusal names
   *  *which* entries are unpriced, so it stops being true the moment the selection
   *  changes -- leaving it on screen next to a different selection is a message about
   *  something the user is no longer doing. */
  it('drops a previous refusal as soon as the selection changes', async () => {
    await openDialog(twoEntries)
    vi.mocked(api.POST).mockImplementation(() =>
      failed({ detail: 'senza tariffa', code: 'validation_failed' }, 422),
    )
    await userEvent.click(await screen.findByRole('button', { name: 'Genera' }))
    await screen.findByRole('alert')

    await userEvent.click(screen.getByRole('checkbox', { name: /Analisi/ }))

    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
  })

  /** A failed read must not leave a Genera button standing over a list nobody could see:
   *  it would post a selection the user never had the chance to make. */
  it('shows the read failure and offers nothing to generate', async () => {
    await openDialog({ '/api/time-entries': failed({ detail: 'Ore non leggibili' }, 503) })

    expect(await screen.findByRole('alert')).toHaveTextContent('Ore non leggibili')
    expect(screen.queryByRole('button', { name: 'Genera' })).toBeNull()
  })

  /** The API answers a deal whose hours are all on issued invoices with an empty page.
   *  An empty list is not a failure, and it is not a reason to offer a draft either. */
  it('says there is nothing to invoice when the list comes back empty', async () => {
    await openDialog({ '/api/time-entries': ok({ items: [], next_cursor: null }) })

    expect(await screen.findByText(/nessuna voce da fatturare/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Genera' })).toBeNull()
  })
})
