import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

// REB-788: the panels read the role (the estimate is an administrator's), so the tests
// say which role is signed in; the default is the one that may read everything.
const session = vi.hoisted(() => ({ role: 'admin' as 'admin' | 'collaborator' | 'readonly' }))
vi.mock('@/lib/auth', async () => {
  const { can } = await import('@/lib/permissions')
  return {
    useAuth: () => ({ user: { role: session.role } }),
    useCan: (action: Parameters<typeof can>[1]) => can(session.role, action),
  }
})
import { EconomicTab } from './EconomicTab'
import type { CashMonth } from './queries'
import { api } from '@/lib/api'
import { seedModules } from '@/lib/modules.testing'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PUT: vi.fn(), DELETE: vi.fn() } }
})

vi.mock('@tanstack/react-router', () => ({
  Link: ({
    to,
    children,
    ...rest
  }: {
    to: string
    children: React.ReactNode
  } & Record<string, unknown>) => (
    <a href={to} {...rest}>
      {children}
    </a>
  ),
}))

function ok(data: unknown) {
  return { data, response: new Response(null, { status: 200 }) } as never
}

/**
 * Three endpoints answer this screen since the «Stima fiscale» card moved into it:
 * `/api/analytics/overview` for the cards and the charts, `/api/analytics/fiscal` for
 * the card below them and `/api/analytics/ceilings` for the room left under the
 * forfettario's ceilings (REB-755), empty here unless a test says otherwise. Mocked by path rather than with one `mockResolvedValue`, because a
 * single answer for both would feed the panel a payload with no `warning` and no year
 * -- green on an assertion about a card that never rendered what it renders in the app.
 */
function byPath(overview: unknown, tax: unknown = TAX, thresholds: unknown[] = []) {
  return (path: string) =>
    Promise.resolve(
      path === '/api/analytics/fiscal'
        ? ok(tax)
        : path === '/api/analytics/ceilings'
          ? ok({ year: 2026, legal_entity_id: 'a1', pack_id: 'it-flat-rate', pack_version: '1', thresholds })
          : path === '/api/contracts'
            ? ok({ items: [], next_cursor: null })
            : ok(overview),
    )
}

function failed(error: unknown, status: number) {
  return { error, response: new Response(null, { status }) } as never
}

/** The ceilings no azienda here is held to: the panel under the figures draws nothing. */
const NO_CEILINGS = {
  year: 2026,
  legal_entity_id: 'a-1',
  pack_id: 'it-flat-rate',
  pack_version: '1',
  thresholds: [],
}

/** Typed as the wire shape so a field added to `CashMonth` fails here rather than
 *  rendering something nobody asserted on (ORB-139: the two `pila_*` heights). The
 *  heights are illustrative, not sums: the chart prints them, it never checks them. */
function month(
  yearMonth: number,
  paid = '0.00',
  unpaid = '0.00',
  drafts = '0.00',
  costs = '0.00',
): CashMonth {
  return {
    year: 2026,
    month: yearMonth,
    paid,
    unpaid,
    drafts,
    costs,
    trend_stack: paid,
    projection_stack: paid,
    trend_shares: { paid: paid === '0.00' ? 0 : 1, costs: costs === '0.00' ? 0 : 0.1 },
    projection_shares: {
      paid: paid === '0.00' ? 0 : 0.6,
      unpaid: unpaid === '0.00' ? 0 : 0.3,
      drafts: drafts === '0.00' ? 0 : 0.05,
      costs: costs === '0.00' ? 0 : 0.05,
    },
  }
}

const Months = [
  month(1),
  month(2),
  month(3),
  month(4, '3990.00', '0.00', '0.00', '100.00'),
  month(5, '0.00', '0.00', '2500.00', '0.00'),
  month(6, '1200.00'),
  month(7, '6300.00', '0.00', '0.00', '97.50'),
  month(8, '7458.62', '6954.03', '0.00', '100.00'),
  month(9),
  month(10),
  month(11),
  month(12),
]

const TAX = {
  year: 2026,
  estimate: true,
  warning: 'estimate',
  revenue: '20628.62',
  profitability_coefficient: '67.00',
  taxable_amount: '13821.18',
  substitute_tax_rate: '5.00',
  substitute_tax: '691.06',
  social_security_rate: '26.07',
  contributions: '3423.02',
  estimated_net_income: '16514.54',
  due_total: '4114.08',
  basis: {
    year: 2026,
    legal_entity_id: '00000000-0000-7000-8000-000000000001',
    basis: 'cash',
    period_from: '2026-01-01',
    period_to: '2026-12-31',
    label: 'Incassato nel 2026, secondo il principio di cassa',
    wording: 'principio di cassa',
    source: 'L. 190/2014 art. 1 c. 64',
    revenue: '20628.62',
    costs: null,
    left_out: [],
    assumptions: [],
  },
}

const CONCENTRATION = [
  {
    customer_id: 'c-grande',
    legal_name: 'Grande S.r.l.',
    revenue: '6000.00',
    invoices: 2,
    share: 2 / 3,
  },
  {
    customer_id: 'c-piccolo',
    legal_name: 'Piccolo S.r.l.',
    revenue: '3000.00',
    invoices: 1,
    share: 1 / 3,
  },
]

const RESPONSE = {
  calculated_at: '2026-09-08T10:00:00Z',
  pension_fund: {
    year: 2026,
    base: 'accrual',
    paid: '20628.62',
    unpaid: '6954.03',
    drafts: '2500.00',
    projected: '30082.65',
    costs: '297.50',
    actual_gross: '20331.12',
    projected_gross: '29785.15',
    months: Months,
  },
  tax: TAX,
  // A «what if» on the projected revenue names no basis.
  projected_tax: {
    ...TAX,
    basis: null,
    revenue: '30082.65',
    taxable_amount: '20155.38',
    due_total: '5999.55',
  },
  customer_concentration: CONCENTRATION,
  actual_net: '16217.04',
  projected_net: '23785.60',
}

const PERIOD = { from: '2026-09-01', to: '2026-09-30' }

function renderTab(period = PERIOD, base: 'accrual' | 'collection' = 'accrual') {
  const onBaseChange = vi.fn()
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const rendered = render(
    <QueryClientProvider client={client}>
      <EconomicTab period={period} base={base} onBaseChange={onBaseChange} />
    </QueryClientProvider>,
  )
  return { ...rendered, onBaseChange }
}

beforeEach(() => {
  session.role = 'admin'
  vi.mocked(api.GET).mockReset()
})

describe('EconomicTab', () => {
  it('asks for the year the period names, because cash and taxes are told by the year', async () => {
    vi.mocked(api.GET).mockImplementation(byPath(RESPONSE) as never)
    renderTab({ from: '2025-03-01', to: '2025-03-31' })
    await screen.findByText(/Vista economica 2025/)
    expect(api.GET).toHaveBeenCalledWith('/api/analytics/overview', {
      params: { query: { year: 2025, base: 'accrual' } },
    })
  })

  it('offers the two readings as one switch, marks the current one and hands the other back', async () => {
    // ORB-133, Ivan: a switch between the accrual and the payment reading,
    // defaulting to accrual. The switch is a radio group so the reading is announced
    // as a choice, not as two unrelated buttons; the change goes back to the URL through
    // the callback, never into local state.
    vi.mocked(api.GET).mockImplementation(byPath(RESPONSE) as never)
    const { onBaseChange } = renderTab()
    const group = await screen.findByRole('radiogroup', { name: 'Lettura dei mesi' })
    const accrual = within(group).getByRole('radio', { name: 'Competenza' })
    const collection = within(group).getByRole('radio', { name: 'Incasso' })
    expect(accrual).toHaveAttribute('aria-checked', 'true')
    expect(collection).toHaveAttribute('aria-checked', 'false')
    await userEvent.click(collection)
    expect(onBaseChange).toHaveBeenCalledWith('collection')
    expect(onBaseChange).toHaveBeenCalledTimes(1)
  })

  it('requests the reading it is given and says which one the charts show', async () => {
    vi.mocked(api.GET).mockImplementation(
      byPath({ ...RESPONSE, pension_fund: { ...RESPONSE.pension_fund, base: 'collection' } }) as never,
    )
    renderTab(PERIOD, 'collection')
    await screen.findByRole('figure', { name: 'Andamento economico 2026' })
    expect(api.GET).toHaveBeenCalledWith('/api/analytics/overview', {
      params: { query: { year: 2026, base: 'collection' } },
    })
    expect(screen.getByRole('radio', { name: 'Incasso' })).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByText(/Vista economica 2026 per incasso/)).toBeInTheDocument()
  })

  it('asks a collaboratore for no fiscal estimate and says it is reserved, with no 403 to log', async () => {
    session.role = 'collaborator'
    vi.mocked(api.GET).mockImplementation(byPath(RESPONSE) as never)
    renderTab()
    await screen.findByRole('figure', { name: 'Andamento economico 2026' })
    expect(await screen.findByRole('alert')).toHaveTextContent(
      /ruolo in questo spazio è «collaboratore»: la stima fiscale è riservata a «amministratore»/,
    )
    const paths = (vi.mocked(api.GET).mock.calls as unknown as GetCall[]).map(([path]) => path)
    expect(paths).not.toContain('/api/analytics/fiscal')
  })

  it('tells the reader the tax block is on the money whatever the charts read by', async () => {
    // The estimate follows what was collected in the year (DECISIONS.md, 2026-09-10), so
    // under the accrual reading its cards say so rather than letting the reader assume
    // the tax moved with the bars. The projected estimate's hint quotes the revenue the
    // estimate itself was computed on, which under the accrual reading is not the
    // `projected` the cards above show: here they differ on purpose.
    vi.mocked(api.GET).mockImplementation(
      byPath({
        ...RESPONSE,
        projected_tax: { ...RESPONSE.projected_tax, revenue: '31082.65' },
      }) as never,
    )
    renderTab()
    const net = await screen.findByRole('group', { name: 'Totale netto ricavi' })
    expect(net).toHaveTextContent(/su base incasso/)
    expect(screen.getByRole('group', { name: 'Totale netto ricavi con proiezione' })).toHaveTextContent(
      /su base incasso/,
    )
    const projection = screen.getByRole('group', { name: 'Totale da saldare con proiezione' })
    expect(projection).toHaveTextContent('31.082,65 €')
    expect(projection).not.toHaveTextContent('30.082,65 €')
  })

  it('moves the reading with the arrow keys, as a radio group promises', async () => {
    // One tab stop and the arrows, the radio keyboard model: two buttons announced as
    // radios would let a screen-reader user hear «1 of 2» and then find the arrows dead.
    vi.mocked(api.GET).mockImplementation(byPath(RESPONSE) as never)
    const { onBaseChange } = renderTab()
    const accrual = await screen.findByRole('radio', { name: 'Competenza' })
    accrual.focus()
    // Held, not tapped: Radix moves focus on a timeout after the keydown and checks the
    // item on focus only while the arrow is still down, as a finger on a key is. A
    // `{ArrowRight}` that releases in the same tick never selects, in any browser.
    await userEvent.keyboard('{ArrowRight>}')
    await waitFor(() => expect(onBaseChange).toHaveBeenCalledWith('collection'))
    await userEvent.keyboard('{/ArrowRight}')
  })

  it('shows the money figures from the API strings, cents included', async () => {
    vi.mocked(api.GET).mockImplementation(byPath(RESPONSE) as never)
    renderTab()
    const paid = await screen.findByRole('group', { name: 'Ricavi incassati' })
    expect(paid).toHaveTextContent('20.628,62 €')
    expect(screen.getByRole('group', { name: 'Ricavi proiettati' })).toHaveTextContent('30.082,65 €')
    expect(screen.getByRole('group', { name: 'Costi passivi' })).toHaveTextContent('297,50 €')
    expect(screen.getByRole('group', { name: 'Totale lordo effettivo' })).toHaveTextContent(
      '20.331,12 €',
    )
  })

  it('says a line of the estimate is not computable instead of showing a zero', async () => {
    // Without the INPS rate neither the contributions nor the substitute tax (owed on the
    // income less the contributions) have a figure, and the total is not drawn either.
    const missing = {
      ...TAX,
      substitute_tax: null,
      contributions: null,
      social_security_rate: null,
      estimated_net_income: null,
      due_total: null,
    }
    vi.mocked(api.GET).mockImplementation(
      byPath({ ...RESPONSE, tax: missing, projected_tax: missing }) as never,
    )
    renderTab()
    const total = await screen.findByRole('group', { name: 'Totale da saldare' })
    expect(total).toHaveTextContent('imposta sostitutiva non calcolabile · INPS non calcolabile')
    expect(total).not.toHaveTextContent('0,00 €')
  })

  it('shows an srl’s IRES and IRAP, and says what IRAP lacks, instead of the forfettario cards', async () => {
    const step = { lines: [], base: '72000.00', loss: false, region: '03', missing: null, assumptions: [] }
    const srl = {
      ...TAX,
      pack_id: 'it-company',
      substitute_tax: null,
      contributions: null,
      taxable_amount: null,
      due_total: '11640.00',
      basis: { label: 'Ricavi e costi di competenza del 2026' },
      company: {
        ires: { ...step, rate: '24.00', amount: '8832.00' },
        irap: { ...step, rate: '3.90', amount: '2808.00' },
      },
    }
    vi.mocked(api.GET).mockImplementation(byPath({ ...RESPONSE, tax: srl, projected_tax: null }) as never)
    renderTab()
    expect(await screen.findByRole('group', { name: 'IRES stimata' })).toHaveTextContent('8.832,00 €')
    expect(screen.getByRole('group', { name: 'IRAP stimata' })).toHaveTextContent('2.808,00 €')
    expect(screen.getByRole('group', { name: 'Totale da saldare' })).toHaveTextContent('11.640,00 €')
    expect(screen.queryByRole('group', { name: 'Imponibile forfettario stimato' })).toBeNull()
  })

  it('says an srl’s IRAP is split over regions instead of printing a rate (REB-1092)', async () => {
    const step = { lines: [], base: '72000.00', loss: false, region: null, missing: null, assumptions: [] }
    const part = (region: string, name: string) => ({ region, name, pay: '1.00', share: '50.00', base: '36000.00', rate: '3.90', amount: '1404.00' })
    const srl = {
      ...TAX,
      pack_id: 'it-company',
      substitute_tax: null,
      contributions: null,
      taxable_amount: null,
      due_total: '10236.00',
      basis: { label: 'Ricavi e costi di competenza del 2026' },
      company: {
        ires: { ...step, rate: '24.00', amount: '8832.00' },
        irap: { ...step, rate: null, amount: '2808.00', regions: [part('05', 'Veneto'), part('03', 'Lombardia')] },
      },
    }
    vi.mocked(api.GET).mockImplementation(byPath({ ...RESPONSE, tax: srl, projected_tax: null }) as never)
    renderTab()
    const irap = await screen.findByRole('group', { name: 'IRAP stimata' })
    expect(irap).toHaveTextContent('2.808,00 €')
    expect(irap).toHaveTextContent('ripartita su 2 regioni')
    expect(irap).not.toHaveTextContent('non calcolabile')
  })

  it('shows the fiscal estimate on collected and projected revenue', async () => {
    vi.mocked(api.GET).mockImplementation(byPath(RESPONSE) as never)
    renderTab()
    expect(await screen.findByRole('group', { name: 'Totale da saldare' })).toHaveTextContent(
      '4.114,08 €',
    )
    expect(screen.getByRole('group', { name: 'Totale da saldare con proiezione' })).toHaveTextContent(
      '5.999,55 €',
    )
    expect(screen.getByRole('group', { name: 'Totale netto ricavi' })).toHaveTextContent(
      '16.217,04 €',
    )
    expect(screen.getByRole('group', { name: 'Imponibile forfettario stimato' })).toHaveTextContent(
      '13.821,18 €',
    )
    // The rates are on the «Stima fiscale» card below, row by row, so the paragraph that
    // used to repeat them beside a link to another screen has nothing left to add.
    expect(screen.queryByRole('link', { name: /stima fiscale/i })).toBeNull()
    expect(screen.queryByText(/Stima basata sul regime forfettario/)).toBeNull()
  })

  it('shows an ordinary person’s IRPEF and INPS, with no projection and no net (REB-1072)', async () => {
    const line = (code: string, label: string, amount: string | null) => ({ code, label, amount })
    const PERSON_TAX = {
      ...TAX,
      taxable_amount: null,
      substitute_tax: null,
      contributions: '15789.47',
      due_total: '30859.47',
      person: {
        income: {
          lines: [],
          professional_income: '65789.47',
          taxable_income: '50000.00',
          loss: false,
          assumptions: [],
          sources: [],
        },
        inps: {
          lines: [],
          base: '65789.47',
          rate: '24.00',
          parts: [line('percentage', 'Contributi sul reddito', '15789.47')],
          amount: '15789.47',
          credit: null,
          region: null,
          cadastral_code: null,
          missing: [],
          assumptions: [],
          sources: [],
        },
        irpef: {
          lines: [],
          base: '50000.00',
          rate: null,
          parts: [line('gross_irpef', 'IRPEF lorda', '13700.00')],
          amount: '13700.00',
          with_surcharges: '15070.00',
          credit: '0.00',
          region: '08',
          cadastral_code: 'A162',
          missing: [],
          assumptions: ['Le detrazioni non sono applicate.'],
          sources: [],
        },
        missing_parameters: [],
        due_total: '30859.47',
      },
    }
    vi.mocked(api.GET).mockImplementation(
      byPath(
        { ...RESPONSE, tax: PERSON_TAX, projected_tax: null, actual_net: null, projected_net: null },
        { ...PERSON_TAX, warning: 'Stima indicativa dell’ordinario.', year: 2026 },
      ) as never,
    )
    renderTab()
    expect(await screen.findByRole('group', { name: 'IRPEF stimata' })).toHaveTextContent('13.700,00 €')
    expect(screen.getByRole('group', { name: 'Contributi INPS stimati' })).toHaveTextContent('15.789,47 €')
    expect(screen.getByRole('group', { name: 'Totale da saldare' })).toHaveTextContent('30.859,47 €')
    expect(screen.queryByRole('group', { name: 'Totale da saldare con proiezione' })).toBeNull()
    expect(screen.queryByRole('group', { name: /Totale netto ricavi/ })).toBeNull()
    expect(screen.queryByRole('group', { name: 'Imponibile forfettario stimato' })).toBeNull()
    // The card below draws the lines the figures were computed from, from the server's payload.
    expect(await screen.findByText('IRPEF lorda')).toBeInTheDocument()
    expect(screen.getByText('Reddito su cui si calcola l’IRPEF')).toBeInTheDocument()
    expect(screen.getByText('Le detrazioni non sono applicate.')).toBeInTheDocument()
  })

  it('says an ordinary figure it could not give is not computable, not zero (REB-1072)', async () => {
    const empty = { lines: [], base: null, rate: null, parts: [], amount: null, credit: null, region: null, cadastral_code: null, missing: ['Manca la categoria INPS.'], assumptions: [], sources: [] }
    const PERSON_TAX = {
      ...TAX,
      taxable_amount: null,
      substitute_tax: null,
      contributions: null,
      due_total: null,
      person: {
        income: { lines: [], professional_income: '65789.47', taxable_income: null, loss: false, assumptions: [], sources: [] },
        inps: empty,
        irpef: empty,
        missing_parameters: ['inps_category'],
        due_total: null,
      },
    }
    vi.mocked(api.GET).mockImplementation(
      byPath({ ...RESPONSE, tax: PERSON_TAX, projected_tax: null, actual_net: null, projected_net: null }, PERSON_TAX) as never,
    )
    renderTab()
    const irpef = await screen.findByRole('group', { name: 'IRPEF stimata' })
    expect(irpef).toHaveTextContent('non calcolabile')
    expect(irpef).not.toHaveTextContent('0,00')
    expect(screen.getByRole('group', { name: 'Totale da saldare' })).toHaveTextContent('non calcolabile')
  })

  it('shows cash alone, and no link out, when the estimate is not available', async () => {
    vi.mocked(api.GET).mockImplementation(
      byPath({
        ...RESPONSE,
        tax: null,
        projected_tax: null,
        actual_net: null,
        projected_net: null,
      }) as never,
    )
    renderTab()
    await screen.findByRole('group', { name: 'Ricavi incassati' })
    expect(screen.queryByRole('group', { name: 'Totale da saldare' })).toBeNull()
    expect(screen.getByText(/serve un profilo fiscale del regime forfettario, ordinario o di una società di capitali/)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /stima fiscale/i })).toBeNull()
  })

  it('carries the whole «Stima fiscale» card, after the figures it explains', async () => {
    vi.mocked(api.GET).mockImplementation(byPath(RESPONSE) as never)
    const { container } = renderTab()

    // Every row of it, which is what «la scheda completa» means: the figures above answer
    // "how much do I owe", the card answers "out of what, at which rates".
    //
    // Two dependent round trips, not one: `EconomicTab`'s own `useEconomicOverview` gates
    // everything below it (`EconomicTab.tsx`), and `FiscalPanel` only mounts -- starting
    // its own `useFiscalEstimate` fetch -- once that resolves, so this text needs both
    // queries' fetch-then-render cycles to finish in sequence, twice the chain most of
    // this file's other `findBy*` calls wait on. That also means the whole card (this
    // title, every row below, the note) and everything above it (the charts, the cards)
    // are already committed by the time this resolves, which is why the synchronous
    // `getByText`/`getByRole` calls that follow stay safe without their own `findBy*`.
    // What is not enough is the default one-second `asyncUtilTimeout`: on a loaded full
    // `pnpm --filter web test` run the doubled chain lost that race (REB-416), the same
    // shape `vite.config.ts`'s own `testTimeout: 20_000` comment already describes for
    // the suite as a whole. Matching that headroom here.
    expect(
      await screen.findByText('Stima fiscale 2026', {}, { timeout: 10_000 }),
    ).toBeInTheDocument()
    for (const label of [
      'Coefficiente di redditività',
      'Imponibile',
      'Aliquota imposta sostitutiva',
      'Imposta sostitutiva',
      'Aliquota INPS',
      'Contributi INPS',
      'Reddito netto stimato',
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument()
    }
    // The revenue row, addressed by the hint only it carries: its own label shares an
    // element with that hint, and its figure is also the «Ricavi incassati» card's. The hint is
    // the basis the server computed the figure on.
    expect(screen.getByText(/Incassato nel 2026, secondo il principio di cassa/)).toBeInTheDocument()
    expect(screen.getByText('67,00 %')).toBeInTheDocument()
    expect(screen.getByText('691,06 €')).toBeInTheDocument()
    expect(screen.getByText('16.514,54 €')).toBeInTheDocument()
    // The server's own caveat, above the figures it qualifies, as on the screen this card
    // came from -- its own sentence, not just that some note rendered.
    expect(screen.getByRole('note')).toHaveTextContent(TAX.warning)

    // Position asserted by document order: charts, then the cards, then this card, after
    // the figures it explains. The order of the first two is upstream's (2026-09-09: the
    // shape of the year is read before its exact numbers); what this test owns is the
    // last step: the card answering "at which rates" never comes before the figures it
    // explains. This does not assert adjacency -- `EconomicTab.tsx` puts «Concentrazione
    // clienti» between the last figure and this card -- only that it is not pushed above
    // them, which is the one thing document order can prove.
    const chart = screen.getByRole('figure', { name: 'Andamento economico 2026' })
    const cards = screen.getByRole('group', { name: 'Ricavi incassati' })
    const card = screen.getByText('Stima fiscale 2026')
    expect(chart.compareDocumentPosition(cards) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(cards.compareDocumentPosition(card) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    const last = screen.getByRole('group', { name: 'Totale netto ricavi con proiezione' })
    expect(last.compareDocumentPosition(card) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(container.textContent).not.toMatch(/Apri la stima fiscale/)
  })

  it('asks the estimate for the year the period names', async () => {
    vi.mocked(api.GET).mockImplementation(byPath(RESPONSE, { ...TAX, year: 2025 }) as never)
    renderTab({ from: '2025-03-01', to: '2025-03-31' })

    // Same doubled chain as the card test above (REB-416): the default one-second
    // asyncUtilTimeout is not enough here either, observed failing on a loaded full run.
    await screen.findByText('Stima fiscale 2025', {}, { timeout: 10_000 })
    expect(api.GET).toHaveBeenCalledWith('/api/analytics/fiscal', {
      params: { query: { year: 2025 } },
    })
  })

  /** REB-755: the room left under the ceilings sits with the figures it bounds. */
  it('shows the room left under the forfettario ceilings, between the figures and the concentration', async () => {
    const threshold = {
      id: 'revenue_ceiling',
      label: 'Soglia di ricavi e compensi del regime forfettario',
      threshold: '85000.00',
      consequence: 'leaves_next_year',
      revenue: '20628.62',
      remaining: '64371.38',
      exceeded: false,
      alert_level: null,
      usage: 0.2427,
    }
    vi.mocked(api.GET).mockImplementation(byPath(RESPONSE, TAX, [threshold]) as never)
    renderTab()

    const room = await screen.findByRole('group', { name: threshold.label })
    expect(within(room).getByText('restano 64.371,38 €')).toBeInTheDocument()
    const concentration = screen.getByRole('heading', { name: 'Concentrazione clienti' })
    expect(
      room.compareDocumentPosition(concentration) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
    expect(api.GET).toHaveBeenCalledWith('/api/analytics/ceilings', {
      params: { query: { year: 2026 } },
    })
  })

  it('draws no ceiling panel for an azienda the thresholds do not apply to', async () => {
    vi.mocked(api.GET).mockImplementation(byPath(RESPONSE) as never)
    renderTab()
    await screen.findByRole('group', { name: 'Ricavi incassati' })

    await waitFor(() => expect(api.GET).toHaveBeenCalledWith('/api/analytics/ceilings', expect.anything()))
    expect(screen.queryByText(/soglie del forfettario/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Simula' })).not.toBeInTheDocument()
  })

  it('draws the two monthly charts with a legend and a table view each', async () => {
    vi.mocked(api.GET).mockImplementation(byPath(RESPONSE) as never)
    renderTab()
    const trend = await screen.findByRole('figure', { name: 'Andamento economico 2026' })
    expect(within(trend).getByRole('list', { name: 'Legenda' })).toHaveTextContent(
      'Ricavi incassatiCosti passivi',
    )
    const projection = screen.getByRole('figure', { name: 'Proiezione economica 2026' })
    expect(within(projection).getByRole('list', { name: 'Legenda' })).toHaveTextContent(
      'Da incassare',
    )
    // The tallest month is labelled directly; the table carries every value.
    expect(within(trend).getAllByTestId('segment').length).toBeGreaterThan(0)
    expect(within(trend).getByRole('table')).toHaveTextContent('6.300,00 €')
  })

  it('shows how old the figures are', async () => {
    vi.mocked(api.GET).mockImplementation(byPath(RESPONSE) as never)
    renderTab()
    expect(await screen.findByText(/Aggiornato/)).toBeInTheDocument()
  })

  it('ranks each customer by its own share of the year\'s whole revenue, distinct from the exposure table on the receivables page', async () => {
    vi.mocked(api.GET).mockImplementation(byPath(RESPONSE) as never)
    renderTab()

    const heading = await screen.findByRole('heading', { name: 'Concentrazione clienti' })
    const table = heading.closest('section')?.querySelector('table')
    expect(table).not.toBeNull()
    const rows = within(table as HTMLTableElement).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(2)
    // Ranked, largest first -- the server's own order, never re-sorted here.
    expect(rows[0]).toHaveTextContent('Grande S.r.l.')
    expect(rows[0]).toHaveTextContent('6.000,00 €')
    expect(rows[0]).toHaveTextContent('66,7%')
    expect(rows[1]).toHaveTextContent('Piccolo S.r.l.')
    expect(rows[1]).toHaveTextContent('33,3%')
    // A different figure from `by_customer`'s receivables exposure, in words as well as
    // in data: this table answers a different question and says so.
    expect(screen.getByText(/Non è l’esposizione residua di «Da incassare»/)).toBeInTheDocument()
  })

  it('says so, rather than rendering an empty table, when nobody was invoiced this year', async () => {
    vi.mocked(api.GET).mockImplementation(
      byPath({ ...RESPONSE, customer_concentration: [] }) as never,
    )
    renderTab()
    await screen.findByRole('group', { name: 'Ricavi incassati' })
    const heading = screen.getByRole('heading', { name: 'Concentrazione clienti' })
    expect(screen.getByText('Nessuna fattura emessa nell’anno.')).toBeInTheDocument()
    expect(heading.closest('section')?.querySelector('table')).toBeNull()
  })

  it('renders an error banner and no figures when the request fails', async () => {
    vi.mocked(api.GET).mockResolvedValue(
      failed({ type: 'x', title: 'Errore', status: 500, detail: 'boom', code: 'domain_error' }, 500),
    )
    renderTab()
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Ricavi incassati' })).toBeNull()
  })
})

// -- the sidebar's azienda (REB-632, spec 2026-10-03 §1.9, §5) ---------------------------

import { LegalEntityContext, type LegalEntityRecord, type LegalEntityValue } from '@/lib/legalEntity'

function legalEntityRecord(id: string, name: string): LegalEntityRecord {
  return {
    id,
    name,
    is_default: id === 'a-1',
    active: true,
    legal_name: name,
    vat_number: null,
    tax_code: null,
    address: null,
    postal_code: null,
    city: null,
    province: null,
    country: 'IT',
    certified_email: null,
    sdi_code: null,
    phone: null,
    email: null,
    website: null,
    logo_key: null,
    signature_key: null,
    email_signature: null,
    tax_regime: null,
    legal_representative: null,
    fiscal_year_end_month: 12,
    fiscal_year_end_day: 31,
    registration_date: null,
    place: { status: 'unknown' },
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  }
}

const HUMANCRAFT = legalEntityRecord('a-1', 'humancraft')
const REBASE_LTD = legalEntityRecord('a-2', 'rebase ltd')

function twoLegalEntities(selected: string | null): LegalEntityValue {
  return {
    legal_entities: [HUMANCRAFT, REBASE_LTD],
    selected,
    select: vi.fn(),
    several: true, scoped: false, pinned: false, loaded: true,
    byId: (id) => [HUMANCRAFT, REBASE_LTD].find((a) => a.id === id),
  }
}

const CONCENTRATION_BY_LEGAL_ENTITY = [
  { ...CONCENTRATION[0], legal_entity_id: 'a-1', share: 1 },
  { ...CONCENTRATION[1], legal_entity_id: 'a-2', share: 1 },
]

function renderWithLegalEntities(value: LegalEntityValue) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <LegalEntityContext value={value}>
        <EconomicTab period={PERIOD} base="accrual" onBaseChange={vi.fn()} />
      </LegalEntityContext>
    </QueryClientProvider>,
  )
}

type GetCall = [string, { params?: { query?: Record<string, unknown> } } | undefined]

function queriesSentTo(path: string): Record<string, unknown>[] {
  return (vi.mocked(api.GET).mock.calls as unknown as GetCall[])
    .filter(([called]) => called === path)
    .map(([, init]) => init?.params?.query ?? {})
}

describe('EconomicTab, with several aziende', () => {
  it('in «tutte» asks the estimate once per azienda and names each share’s azienda', async () => {
    vi.mocked(api.GET).mockImplementation((path: string, init?: unknown) => {
      if (path === '/api/analytics/ceilings') return Promise.resolve(ok(NO_CEILINGS))
      if (path === '/api/contracts') return Promise.resolve(ok({ items: [], next_cursor: null }))
      if (path === '/api/analytics/fiscal') {
        const query = (init as GetCall[1])?.params?.query ?? {}
        // The forfettario has an estimate; the foreign company has no profile at all.
        return Promise.resolve(
          query.legal_entity_id === 'a-1' ? ok(TAX) : failed({ code: 'not_found' }, 404),
        )
      }
      return Promise.resolve(
        ok({
          ...RESPONSE,
          legal_entity_id: null,
          tax: null,
          projected_tax: null,
          actual_net: null,
          projected_net: null,
          customer_concentration: CONCENTRATION_BY_LEGAL_ENTITY,
        }),
      )
    })
    renderWithLegalEntities(twoLegalEntities(null))

    // The cards say why there is no space-wide estimate, never «non disponibile».
    expect(await screen.findByText(/Con più aziende la stima è una per azienda/)).toBeInTheDocument()
    expect(screen.queryByText(/Non disponibile qui/)).not.toBeInTheDocument()
    // One estimate per azienda, by id; the overview itself was asked for every azienda.
    await waitFor(() =>
      expect(queriesSentTo('/api/analytics/fiscal')).toEqual(
        expect.arrayContaining([
          expect.objectContaining({ legal_entity_id: 'a-1' }),
          expect.objectContaining({ legal_entity_id: 'a-2' }),
        ]),
      ),
    )
    expect(queriesSentTo('/api/analytics/overview')[0]).not.toHaveProperty('legal_entity_id')
    // The one card that exists carries its azienda's name; the other draws nothing.
    expect(
      await screen.findByRole('heading', { name: 'Stima fiscale 2026 · humancraft' }),
    ).toBeInTheDocument()
    expect(
      screen.queryByRole('heading', { name: 'Stima fiscale 2026 · rebase ltd' }),
    ).not.toBeInTheDocument()
    expect(screen.queryByText(/Profilo fiscale non configurato/)).not.toBeInTheDocument()
    // The concentration table is split by azienda, each share of its own revenue.
    const section = screen.getByRole('heading', { name: 'Concentrazione clienti' }).closest('section')
    expect(section).not.toBeNull()
    expect(within(section as HTMLElement).getByRole('heading', { name: 'humancraft' })).toBeInTheDocument()
    expect(within(section as HTMLElement).getByRole('heading', { name: 'rebase ltd' })).toBeInTheDocument()
    expect(within(section as HTMLElement).getAllByText('100,0%')).toHaveLength(2)
  })

  it('for a non-admin in «tutte» draws no card and no banner, only the note', async () => {
    vi.mocked(api.GET).mockImplementation((path: string) =>
      Promise.resolve(
        path === '/api/analytics/ceilings'
          ? ok(NO_CEILINGS)
          : path === '/api/contracts'
          ? ok({ items: [], next_cursor: null })
          : path === '/api/analytics/fiscal'
          ? failed({ code: 'permission_denied', detail: 'solo un admin' }, 403)
          : ok({
              ...RESPONSE,
              legal_entity_id: null,
              tax: null,
              projected_tax: null,
              actual_net: null,
              projected_net: null,
              customer_concentration: CONCENTRATION_BY_LEGAL_ENTITY,
            }),
      ),
    )
    renderWithLegalEntities(twoLegalEntities(null))
    expect(await screen.findByText(/Con più aziende la stima è una per azienda/)).toBeInTheDocument()
    await waitFor(() => expect(queriesSentTo('/api/analytics/fiscal')).toHaveLength(2))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /^Stima fiscale \d{4}/ })).not.toBeInTheDocument()
  })

  it('with one azienda selected sends it on the overview and the estimate alike', async () => {
    vi.mocked(api.GET).mockImplementation(byPath({ ...RESPONSE, legal_entity_id: 'a-1' }) as never)
    renderWithLegalEntities(twoLegalEntities('a-1'))
    expect(await screen.findByRole('heading', { name: 'Stima fiscale 2026' })).toBeInTheDocument()
    expect(queriesSentTo('/api/analytics/overview')[0]).toMatchObject({ legal_entity_id: 'a-1' })
    expect(queriesSentTo('/api/analytics/fiscal')).toEqual([{ year: 2026, legal_entity_id: 'a-1' }])
    // One legal entity on screen: no per-legal-entity headings anywhere.
    expect(screen.queryByRole('heading', { name: 'humancraft' })).not.toBeInTheDocument()
  })
})

describe('EconomicTab, the way back to «Cassa»', () => {
  function renderWithModules(off: Parameters<typeof seedModules>[1]) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    seedModules(client, off)
    return render(
      <QueryClientProvider client={client}>
        <EconomicTab period={PERIOD} base="accrual" onBaseChange={vi.fn()} />
      </QueryClientProvider>,
    )
  }

  it('says how the two views differ and links to «Cassa» when the module is on', async () => {
    vi.mocked(api.GET).mockImplementation(byPath(RESPONSE) as never)
    renderWithModules([])
    const link = await screen.findByRole('link', { name: 'Apri Cassa' })
    expect(link).toHaveAttribute('href', '/app/cash')
  })

  it('draws no link to a page that is not there with the module off', async () => {
    vi.mocked(api.GET).mockImplementation(byPath(RESPONSE) as never)
    renderWithModules(['cash'])
    await screen.findByText(/Vista economica/)
    expect(screen.queryByRole('link', { name: 'Apri Cassa' })).not.toBeInTheDocument()
  })
})
