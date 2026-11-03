import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactElement, ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { DealDetail } from './DealDetail'
import { api } from '@/lib/api'
import { LegalEntityContext, type LegalEntityRecord, type LegalEntityValue } from '@/lib/legalEntity'

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return {
    ...actual,
    useParams: () => ({ dealId: 'd1' }),
    useNavigate: () => vi.fn(),
    // The tabs below this header render links of their own, and a real `Link` needs a
    // router context this test has no reason to build. The house stub
    // (deal/list.test.tsx, invoices/index.test.tsx) renders it as a plain anchor.
    Link: ({ children }: { children: ReactNode }) => <a href="#">{children}</a>,
  }
})

// Hoisted so one test can flip it: the header's actions exist only for a writer, and
// everything else here is about what any reader sees.
const auth = vi.hoisted(() => ({ canWrite: false }))
vi.mock('@/lib/auth', () => ({
  useCanWrite: () => auth.canWrite,
  // REB-294: the pages below mount controls that read their own action from the
  // table (`NewProformaButton`, `DocumentsTab`); they follow the same switch, so a
  // test asking for a writer gets every writer control and a reader gets none.
  useCan: () => auth.canWrite,
  // The owner row names the signed-in person (REB-780).
  useAuth: () => ({ user: { id: 'u-me', name: 'Io' } }),
}))

// `api.GET` is spied on directly, mirroring `customers/$customerId.test.tsx`: what
// is under test is this route's own handling of what the real `unwrap` produces,
// not a reimplementation of it. `DealDetail` also mounts `useEntitySchema` and
// `useStages` unconditionally -- both resolve to a harmless empty success below,
// since neither is ever read before the isError/404 guard under test here
// returns.
const mockGet = vi.spyOn(api, 'GET')

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) }) as never
}
function failed(error: unknown, status: number) {
  return Promise.resolve({ error, response: new Response(null, { status }) }) as never
}

function mockDealFetch(result: ReturnType<typeof ok> | ReturnType<typeof failed>) {
  mockGet.mockImplementation(
    ((path: string) => (path === '/api/deals/{deal_id}' ? result : path === '/api/users/directory' ? ok([]) : ok({ items: [] }))) as never,
  )
}

beforeEach(() => {
  mockGet.mockReset()
  auth.canWrite = false
})

/** Enough of a `DealRead` for the page to render past its guards. Every nullable field
 *  the Panoramica formats is present as `null`: absent is not the same as null to
 *  `formatDate`/`formatMoney`, which read the string they are handed. */
const DEAL = {
  id: 'd1',
  name: 'Sito vetrina',
  customer_id: 'c1',
  legal_entity_id: 'a2',
  customer_legal_name: 'ACME Srl',
  pipeline_stage_id: 's1',
  expected_value: '4500.00',
  one_off_value: '4500.00',
  monthly_recurring_value: null,
  value_lines: [],
  probability: 50,
  expected_close_date: null,
  budgeted_hours: null,
  budgeted_value: null,
  hourly_rate: null,
  owner_id: null,
  closed_at: null,
  custom_fields: {},
} as never

const NOTE = {
  id: 'n1',
  entity_type: 'deal',
  entity_id: 'd1',
  text: 'Richiamare a settembre.',
  author: 'Giulia Rossi',
  author_id: 'u1',
  source: 'web',
  created_at: '2026-10-01T09:00:00Z',
  edited_at: null,
  edited_by: null,
  editable: true,
} as never

function renderWithClient(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

describe('DealDetail', () => {
  it('shows "Deal non trovato" for a genuine 404', async () => {
    mockDealFetch(
      failed(
        { type: 'about:blank', title: 'Non trovato', status: 404, detail: 'deal non trovato', code: 'not_found' },
        404,
      ),
    )
    renderWithClient(<DealDetail />)
    expect(await screen.findByText('Deal non trovato.')).toBeInTheDocument()
  })

  /**
   * The bug this guards against: before this fix, `if (!deal) return <p>Deal
   * non trovato.</p>` fired for *any* failed fetch, not only a real 404 -- a
   * 500, a 502 or a dropped connection told the user the record does not
   * exist. This reuses `QueryErrorBanner`, the same surface `DataTable` and
   * `Timeline` already show for a failed request everywhere else in this app,
   * rather than inventing a fourth way to say "something went wrong".
   */
  it('shows the failed-request banner, not "Deal non trovato", when the fetch fails for a reason other than 404', async () => {
    mockDealFetch(failed({ code: 'http_error', detail: 'Il server non risponde.', status: 503 }, 503))
    renderWithClient(<DealDetail />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Il server non risponde.')
    expect(screen.queryByText('Deal non trovato.')).not.toBeInTheDocument()
  })

  /** REB-879: the Note card of the overview is the deal's thread, read from the notes
   *  endpoint and not from a field of the deal. */
  it('shows the deal’s notes thread in the overview', async () => {
    mockGet.mockImplementation(
      ((path: string) => {
        if (path === '/api/deals/{deal_id}') return ok(DEAL)
        if (path === '/api/users/directory' || path.endsWith('/timeline')) return ok([])
        if (path === '/api/pipeline-stages') return ok([])
        if (path === '/api/{collection}/{entity_id}/notes') return ok([NOTE])
        return ok({ items: [], next_cursor: null })
      }) as never,
    )
    renderWithClient(<DealDetail />)
    expect(await screen.findByText('Richiamare a settembre.')).toBeInTheDocument()
    expect(screen.getByText('Giulia Rossi')).toBeInTheDocument()
    expect(mockGet).toHaveBeenCalledWith('/api/{collection}/{entity_id}/notes', {
      params: { path: { collection: 'deals', entity_id: 'd1' } },
    })
  })

  /**
   * The commonest invoice of all is the one a deal has just earned, and every field it
   * needs is on this page already. The action is in the header rather than on the
   * Fatture tab because a deal's header is where its verbs live -- and it opens the
   * dialog with the customer, the deal and the first line already answered.
   */
  describe('the «Nuova fattura» action', () => {
    async function openDeal() {
      mockGet.mockImplementation(
        ((path: string) => {
          if (path === '/api/deals/{deal_id}') return ok(DEAL)
          // The member directory is a bare array too (REB-780).
          if (path === '/api/users/directory') return ok([])
          // `useStages` unwraps to a bare array, not to a page.
          if (path === '/api/pipeline-stages')
            return ok([
              { id: 's1', name: 'Lead', position: 1, type: 'open', code: null, default_probability: 10 },
              { id: 'won', name: 'Vinto', position: 4, type: 'won', code: 'won', default_probability: 100 },
              { id: 'lost', name: 'Perso', position: 5, type: 'lost', code: 'lost', default_probability: 0 },
            ])
          // So does the timeline, which the Pipedrive-shaped page mounts in its right
          // column («Ultimi eventi», 2026-09-09). Handed a page envelope it would
          // render `{items: []}.map` and take the whole tree down with it, and every
          // `getByRole` below would then fail on an empty body -- a mock shape reported
          // as a missing button.
          if (path.endsWith('/timeline')) return ok([])
          // And the notes thread beside it (REB-879).
          if (path === '/api/{collection}/{entity_id}/notes') return ok([])
          return ok({ items: [], next_cursor: null })
        }) as never,
      )
      renderWithClient(<DealDetail />)
      await screen.findByRole('tab', { name: 'Panoramica' })
    }

    it('opens a proforma already filled in from the deal', async () => {
      auth.canWrite = true
      await openDeal()

      const button = await screen.findByRole('button', { name: 'Nuova fattura' })
      // Before «Modifica»: creating is the thing this header is most often opened for.
      // The whole header, in order, and not just the two buttons a stage-less mock
      // would leave standing: «Vinto» and «Perso» come from the pipeline, so a mock
      // that answers `/api/pipeline-stages` with an empty array stubs away exactly
      // the neighbours this assertion is about. After the four named actions comes
      // the `⋯` menu (no label of its own) and then the stage bar.
      const actions = screen.getAllByRole('button')
      expect(actions.slice(0, 4).map((element) => element.textContent)).toEqual([
        'Nuova fattura',
        'Vinto',
        'Perso',
        'Modifica',
      ])
      // «Una sola cosa forte per schermata» (UI revision spec §92): exactly one filled
      // button in this header, and it is the one Ivan asked for. `Button` stamps its
      // variant on the element, so this reads the rendered fact rather than a class.
      expect(
        actions
          .filter((element) => element.getAttribute('data-variant') === 'default')
          .map((element) => element.textContent),
      ).toEqual(['Nuova fattura'])

      await userEvent.click(button)
      const dialog = await screen.findByRole('dialog')
      // Neither picker: this dialog was opened from the record that answers both.
      expect(within(dialog).queryByLabelText(/^Cliente/)).not.toBeInTheDocument()
      expect(within(dialog).queryByLabelText(/^Deal/)).not.toBeInTheDocument()
      expect(within(dialog).getByLabelText('Descrizione riga 1')).toHaveValue('Sito vetrina')
      expect(within(dialog).getByLabelText('Prezzo unitario riga 1')).toHaveValue('4500.00')
      // Both parties named out of the deal this page has already read: no second
      // request, and no «…» to watch resolve. `DealCustomerCard`'s own `useCustomer`
      // is no help here -- it lives in the Collegamenti tab, still unmounted.
      // Scoped to the dialog since the Pipedrive-shaped page (2026-09-09) also names the
      // customer in its own Riepilogo: an unscoped query would match twice and say
      // "found multiple" about a dialog that is in fact correct.
      expect(within(dialog).getByText('ACME Srl')).toBeInTheDocument()
      expect(mockGet).not.toHaveBeenCalledWith('/api/customers/{customer_id}', expect.anything())
    })

    it('is not offered to a reader', async () => {
      await openDeal()
      expect(screen.queryByRole('button', { name: 'Nuova fattura' })).not.toBeInTheDocument()
    })
  })
})

describe('the azienda in the header (REB-626)', () => {
  const HUMANCRAFT = { id: 'a1', name: 'humancraft', active: true } as LegalEntityRecord
  const REBASE = { id: 'a2', name: 'rebase', active: true } as LegalEntityRecord
  const TWO: LegalEntityValue = {
    legal_entities: [HUMANCRAFT, REBASE],
    selected: null,
    select: vi.fn(),
    several: true, scoped: false, pinned: false, loaded: true,
    byId: (id) => [HUMANCRAFT, REBASE].find((a) => a.id === id),
  }

  /** The deal and the stages the header needs; the rest of the page's reads answer empty. */
  function mockPage() {
    mockGet.mockImplementation(
      ((path: string) => {
        if (path === '/api/deals/{deal_id}') return ok(DEAL)
        if (path === '/api/pipeline-stages')
          return ok([{ id: 's1', name: 'Lead', position: 1, type: 'open', code: null, default_probability: 10 }])
        return ok([])
      }) as never,
    )
  }

  it("names the deal's azienda after the customer and the amount, from the second azienda on", async () => {
    mockPage()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <LegalEntityContext value={TWO}>
          <DealDetail />
        </LegalEntityContext>
      </QueryClientProvider>,
    )
    expect(await screen.findByText(/^ACME Srl · 4\.500,00\s€ · rebase$/)).toBeInTheDocument()
  })

  it('says nothing about the azienda in a one-legal-entity space', async () => {
    mockPage()
    renderWithClient(<DealDetail />)
    expect(await screen.findByText(/^ACME Srl · 4\.500,00\s€$/)).toBeInTheDocument()
  })

  /** The deal the spec uses: 20.000 up front plus a 500 fee with no end, which counts 12 months. */
  const MIXED = {
    ...(DEAL as object),
    expected_value: '26000.00',
    one_off_value: '20000.00',
    monthly_recurring_value: '500.00',
    value_lines: [
      {
        id: 'l1', position: 0, description: 'Sviluppo', unit: 'flat', delivery_period: 'one_off',
        unit_price: '20000.00', quantity: '1.00', start: null, duration_months: null,
      },
      {
        id: 'l2', position: 1, description: 'Manutenzione', unit: 'flat', delivery_period: 'monthly',
        unit_price: '500.00', quantity: '1.00', start: null, duration_months: null,
      },
    ],
  }

  it('shows the lines and the three figures, and says an open-ended fee counts 12 months', async () => {
    mockGet.mockImplementation(
      ((path: string) =>
        path === '/api/deals/{deal_id}' ? ok(MIXED) : path === '/api/pipeline-stages' ? ok([]) : ok([])) as never,
    )
    renderWithClient(<DealDetail />)

    const lines = await screen.findByRole('list', { name: 'Righe di valore' })
    expect(within(lines).getByText('Sviluppo')).toBeInTheDocument()
    expect(within(lines).getByText(/^500,00\s€ · ogni mese · senza scadenza$/)).toBeInTheDocument()
    expect(screen.getByText(/^26\.000,00\s€$/)).toBeInTheDocument()
    expect(screen.getByText(/^20\.000,00\s€$/)).toBeInTheDocument()
    expect(screen.getByText(/^500,00\s€\/mese$/)).toBeInTheDocument()
    expect(screen.getByText(/conta 12 mesi/)).toBeInTheDocument()
  })

  it('starts the proforma at the one-off value, never at the total', async () => {
    auth.canWrite = true
    mockGet.mockImplementation(
      ((path: string) => (path === '/api/deals/{deal_id}' ? ok(MIXED) : ok([]))) as never,
    )
    renderWithClient(<DealDetail />)
    await userEvent.click(await screen.findByRole('button', { name: 'Nuova fattura' }))
    expect(await screen.findByLabelText('Prezzo unitario riga 1')).toHaveDisplayValue('20000.00')
  })

  it('starts the proforma empty when the deal has nothing one-off', async () => {
    auth.canWrite = true
    const feesOnly = { ...MIXED, one_off_value: '0.00', expected_value: '6000.00' }
    mockGet.mockImplementation(
      ((path: string) => (path === '/api/deals/{deal_id}' ? ok(feesOnly) : ok([]))) as never,
    )
    renderWithClient(<DealDetail />)
    await userEvent.click(await screen.findByRole('button', { name: 'Nuova fattura' }))
    expect(await screen.findByLabelText('Prezzo unitario riga 1')).toHaveDisplayValue('')
  })
})
