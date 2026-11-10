import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { ok, refused, viewport } from '@/features/inbox/testing'
import { EngagementPage } from './EngagementPage'
import { engagement } from './listFixtures'
import type { DaysReport, Engagement, EngagementPayment, MonthlyReport, ReportDay, ReportMonth } from './labels'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PATCH: vi.fn() }, fetchWithRefresh: vi.fn() }
})

let admin = true

vi.mock('@/lib/auth', () => ({
  useIsAdmin: () => admin,
  useAuth: () => ({ user: { id: 'u1', role: admin ? 'admin' : 'readonly' }, isLoading: false }),
}))

vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, children, ...rest }: { to: string; children: React.ReactNode; className?: string }) => (
    <a href={to} {...rest}>
      {children}
    </a>
  ),
  useParams: () => ({ engagementId: 'e1' }),
  useNavigate: () => vi.fn(),
}))

vi.mock('@/components/PhoneBar', () => ({ PHONE_BAR_HEIGHT: '4rem' }))
vi.mock('@rebase/ui/sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
vi.mock('./EngagementFormDialog', () => ({
  EngagementFormDialog: ({ open }: { open: boolean }) => (open ? <p>Modulo di modifica</p> : null),
}))

function monthly(overrides: Partial<MonthlyReport>): MonthlyReport {
  return {
    id: 'm1',
    engagement_id: 'e1',
    month: '2026-10-01',
    label: '2026-10',
    state: 'requested',
    effective_state: 'requested',
    counts_as_approved: false,
    received_on: '2026-11-05',
    tacit_on: '2026-11-16',
    decided_on: null,
    decided_by_name: null,
    objection: null,
    held_dates: [],
    created_at: '2026-11-05T09:00:00Z',
    updated_at: '2026-11-05T09:00:00Z',
    ...overrides,
  }
}

function month(overrides: Partial<ReportMonth>): ReportMonth {
  return {
    month: '2026-10-01',
    label: '2026-10',
    monthly_report: null,
    quantity: '0',
    held_quantity: '0',
    payable_quantity: '0',
    payable_amount: null,
    ...overrides,
  }
}

function day(date: string, description: string, overrides: Partial<ReportDay> = {}): ReportDay {
  return {
    date,
    quantity: '1',
    source: 'work_unit',
    status: null,
    description,
    counts: true,
    in_term: true,
    held: false,
    payable: false,
    ...overrides,
  }
}

function report(overrides: Partial<DaysReport> = {}): DaysReport {
  return {
    engagement_id: 'e1',
    unit: 'day',
    fee_unit: 'day',
    date_from: '2026-08-01',
    date_to: '2026-11-10',
    available: true,
    note: null,
    days: [
      day('2026-10-05', 'Migrazione del database'),
      day('2026-10-06', 'Revisione del codice'),
      day('2026-10-07', 'Riunione con il cliente'),
    ],
    months: [
      month({ month: '2026-08-01', label: '2026-08', quantity: '3' }),
      month({
        month: '2026-09-01',
        label: '2026-09',
        quantity: '18',
        payable_quantity: '18',
        payable_amount: '8100.00',
        monthly_report: monthly({
          month: '2026-09-01',
          label: '2026-09',
          state: 'approved',
          effective_state: 'approved',
          counts_as_approved: true,
          decided_on: '2026-10-08',
          decided_by_name: 'Anna Bianchi',
        }),
      }),
      month({ month: '2026-10-01', quantity: '3', monthly_report: monthly({}) }),
    ],
    total_quantity: '24',
    payable_quantity: '18',
    held_quantity: '0',
    payable_amount: '8100.00',
    ...overrides,
  }
}

function payment(overrides: Partial<EngagementPayment>): EngagementPayment {
  return {
    due_item_id: 'd1',
    month: null,
    state: 'forecast',
    amount: '1800.00',
    settled: null,
    remaining: null,
    currency: 'EUR',
    due_date: null,
    due_date_estimated: false,
    waiting_for_invoice: null,
    description: null,
    ...overrides,
  }
}

const PAYMENTS: EngagementPayment[] = [
  payment({
    due_item_id: 'd1',
    state: 'expected',
    amount: '8100.00',
    due_date: '2026-11-05',
    description: 'Fattura 12/2026',
  }),
  payment({
    due_item_id: 'd2',
    month: '2026-10-01',
    amount: '1350.00',
    due_date: '2026-12-30',
    due_date_estimated: true,
    waiting_for_invoice: '2026/71',
  }),
  payment({ due_item_id: 'd3', month: '2026-12-01', amount: '1800.00', due_date: '2027-01-30' }),
  payment({ due_item_id: 'd4', month: '2027-01-01', amount: '1800.00', due_date: '2027-02-28' }),
]

const ACTIVE = () =>
  engagement({
    state: 'active',
    starts_on: '2026-08-01',
    letter_number: '2026-014',
    letter_document_id: 'doc1',
    waiting: [{ code: 'report_undecided', month: '2026-10-01', tacit_on: '2026-11-16' }],
  })

interface Setup {
  engagement?: Engagement
  report?: unknown
  payments?: EngagementPayment[]
}

function answering(setup: Setup = {}) {
  vi.mocked(api.GET).mockImplementation(((path: string) => {
    if (path === '/api/engagements/{engagement_id}') return Promise.resolve(ok(setup.engagement ?? ACTIVE()))
    if (path === '/api/engagements/{engagement_id}/report') {
      return Promise.resolve(setup.report ?? ok(report()))
    }
    if (path === '/api/engagements/{engagement_id}/payments') {
      return Promise.resolve(ok({ engagement_id: 'e1', items: setup.payments ?? PAYMENTS }))
    }
    return Promise.resolve(ok({}))
  }) as never)
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, retryDelay: 0 } } })
  render(
    <QueryClientProvider client={client}>
      <EngagementPage />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  admin = true
  viewport(1280)
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(2026, 10, 10, 12))
})

afterEach(() => {
  vi.useRealTimers()
})

describe('the page of an engagement', () => {
  it('answers a non-administrator with one sentence and asks for nothing', async () => {
    admin = false
    answering()
    renderPage()
    expect(
      screen.getByText('Gli incarichi dei freelance li vedono solo gli amministratori.'),
    ).toBeInTheDocument()
    expect(api.GET).not.toHaveBeenCalled()
    expect(screen.queryByText(/€/)).not.toBeInTheDocument()
  })

  it('says it is reading, then draws the header, the figures and the months of the report', async () => {
    answering()
    renderPage()
    expect(screen.getByRole('status', { name: "Leggo l'incarico…" })).toBeInTheDocument()
    expect(await screen.findByRole('heading', { level: 1, name: 'Giulia Martini · Backend developer' })).toBeInTheDocument()
    expect(screen.getByText('Alfa Logistica S.r.l. · lettera n. 2026-014')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Modifica' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: "Chiudi l'incarico…" })).toBeInTheDocument()

    expect(await screen.findByRole('group', { name: 'Giorni lavorati' })).toHaveTextContent('24')
    expect(screen.getByRole('group', { name: 'Pagabili' })).toHaveTextContent('18')
    expect(screen.getByRole('group', { name: 'Pagabili' })).toHaveTextContent('8.100,00 €')
    expect(screen.getByRole('group', { name: 'Da approvare' })).toHaveTextContent('1')

    const table = await screen.findByRole('table')
    for (const name of ['Mese', 'Giorni', 'Rendiconto', 'Importo', 'Pagamento al freelance']) {
      expect(within(table).getByRole('columnheader', { name })).toBeInTheDocument()
    }
    expect(within(table).getByText('Non ricevuto')).toBeInTheDocument()
    expect(within(table).getByText('Approvato')).toBeInTheDocument()
    expect(within(table).getByText('da Anna Bianchi il 08/10/2026')).toBeInTheDocument()
    expect(within(table).getByText('Ricevuto')).toBeInTheDocument()
    expect(within(table).getByText('Nessuna obiezione entro il 16/11/2026')).toBeInTheDocument()
    // A forecast reads «circa» with its reason; the months after the report are one counted row.
    expect(within(table).getByText('circa 30/12/2026')).toBeInTheDocument()
    expect(within(table).getByText("in attesa dell'incasso di Fattura 2026/71")).toBeInTheDocument()
    expect(within(table).getByText('Da dicembre 2026 a gennaio 2027: 2 mesi previsti')).toBeInTheDocument()
    // A month with no approved «Rendiconto» has nothing payable yet.
    expect(within(table).getAllByText('Pagabile: -').length).toBeGreaterThan(0)
  })

  it('reads a payment that is past its date as «Scaduto da N giorni», counting the server date', async () => {
    answering({
      payments: [
        payment({
          due_item_id: 'd1',
          month: '2026-09-01',
          state: 'expected',
          amount: '8100.00',
          due_date: '2026-11-05',
          description: 'Fattura 12/2026',
        }),
      ],
    })
    renderPage()
    expect(await screen.findByText('Scaduto da 5 giorni')).toBeInTheDocument()
    expect(screen.getByText('05/11/2026 · Fattura 12/2026')).toBeInTheDocument()
  })

  it('approves a received month with one click and an empty body', async () => {
    answering()
    vi.mocked(api.POST).mockResolvedValue(ok(monthly({ effective_state: 'approved' })) as never)
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Apri ottobre 2026' }))
    const panel = screen.getByRole('complementary', { name: 'Mese aperto' })
    expect(within(panel).getByText('Migrazione del database')).toBeInTheDocument()
    expect(within(panel).getByText('Ritenuta e netto: da definire')).toBeInTheDocument()
    await user.click(within(panel).getByRole('button', { name: 'Approva il mese' }))
    expect(api.POST).toHaveBeenCalledTimes(1)
    expect(api.POST).toHaveBeenCalledWith(
      '/api/engagements/{engagement_id}/monthly-reports/{month}/approval',
      { params: { path: { engagement_id: 'e1', month: '2026-10' } }, body: {} },
    )
  })

  it('holds the ticked days with the reason and approves the rest, the button counting the days', async () => {
    answering()
    vi.mocked(api.POST).mockResolvedValue(ok(monthly({ effective_state: 'approved' })) as never)
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Apri ottobre 2026' }))
    await user.click(screen.getByRole('button', { name: 'Contesta dei giorni…' }))
    const dialog = await screen.findByRole('dialog')
    const hold = within(dialog).getByRole('button', { name: 'Trattieni 0 giorni e approva il resto' })
    expect(hold).toBeDisabled()

    await user.click(within(dialog).getByRole('checkbox', { name: /05\/10\/2026/ }))
    expect(within(dialog).getByRole('button', { name: 'Trattieni 1 giorno e approva il resto' })).toBeDisabled()
    await user.click(within(dialog).getByRole('checkbox', { name: /07\/10\/2026/ }))
    await user.type(within(dialog).getByLabelText('Motivo'), 'Il cliente non li riconosce')
    const two = within(dialog).getByRole('button', { name: 'Trattieni 2 giorni e approva il resto' })
    expect(two).toBeEnabled()
    await user.click(two)

    expect(api.POST).toHaveBeenCalledWith(
      '/api/engagements/{engagement_id}/monthly-reports/{month}/approval',
      {
        params: { path: { engagement_id: 'e1', month: '2026-10' } },
        body: { held_dates: ['2026-10-05', '2026-10-07'], objection: 'Il cliente non li riconosce' },
      },
    )
  })

  it('takes «Contesta» away once the tacit date has passed, and shows the held days with the reason', async () => {
    const decided = report({
      months: [
        month({
          monthly_report: monthly({
            effective_state: 'tacit',
            held_dates: ['2026-10-06'],
            objection: 'Giornata non lavorata',
          }),
        }),
      ],
    })
    answering({ report: ok(decided) })
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Apri ottobre 2026' }))
    const panel = screen.getByRole('complementary', { name: 'Mese aperto' })
    expect(within(panel).queryByRole('button', { name: 'Contesta dei giorni…' })).not.toBeInTheDocument()
    expect(within(panel).queryByRole('button', { name: 'Approva il mese' })).not.toBeInTheDocument()
    expect(within(panel).getByText('Giornata non lavorata')).toBeInTheDocument()
  })

  it('records the arrival of a finished month on the typed date', async () => {
    answering()
    vi.mocked(api.POST).mockResolvedValue(ok(monthly({ month: '2026-08-01' })) as never)
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Apri agosto 2026' }))
    const panel = screen.getByRole('complementary', { name: 'Mese aperto' })
    await user.click(within(panel).getByRole('button', { name: "Registra l'arrivo" }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByLabelText('Arrivato il')).toHaveValue('2026-11-10')
    await user.click(within(dialog).getByRole('button', { name: "Registra l'arrivo" }))
    expect(api.POST).toHaveBeenCalledWith('/api/engagements/{engagement_id}/monthly-reports', {
      params: { path: { engagement_id: 'e1' } },
      body: { month: '2026-08', received_on: '2026-11-10' },
    })
  })

  it('reads «Nascosto» for a hidden client and draws no amount', async () => {
    answering({
      engagement: { ...ACTIVE(), redacted: true, role: null, fee: null, client_name: null },
      report: ok(report({ payable_amount: null, months: report().months.map((m) => ({ ...m, payable_amount: null })) })),
      payments: PAYMENTS.map((item) => ({ ...item, amount: null })),
    })
    renderPage()
    expect(await screen.findByRole('heading', { level: 1, name: 'Giulia Martini · Nascosto' })).toBeInTheDocument()
    expect(screen.getByText('Nascosto · lettera n. 2026-014')).toBeInTheDocument()
    await screen.findByRole('table')
    expect(screen.getAllByText('Nascosto').length).toBeGreaterThan(3)
    expect(screen.queryByText(/\d,\d\d €/)).not.toBeInTheDocument()
  })

  it('keeps the rest of the page when the days are refused outside the space that holds them', async () => {
    answering({
      report: refused(409, { code: 'conflict', detail: 'I giorni si leggono solo nello spazio PIGROCRM_LEADS_SPACE.' }),
    })
    renderPage()
    expect(await screen.findByText('I giorni si leggono solo nello spazio PIGROCRM_LEADS_SPACE.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Modifica' })).toBeInTheDocument()
    expect(await screen.findByRole('table')).toBeInTheDocument()
    expect(screen.getByRole('group', { name: 'Giorni lavorati' })).toHaveTextContent('-')
  })

  it('errors only the table when the days cannot be read', async () => {
    answering({ report: refused(500, { code: 'http_error', detail: 'Non riesco a leggere i giorni.' }) })
    renderPage()
    expect(await screen.findByText('Non riesco a leggere i giorni.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Riprova' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: 'Lettera e firma' })).toBeInTheDocument()
  })

  it('closes the engagement on the typed end date', async () => {
    answering()
    vi.mocked(api.POST).mockResolvedValue(ok({}) as never)
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: "Chiudi l'incarico…" }))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: "Chiudi l'incarico" }))
    expect(api.POST).toHaveBeenCalledWith('/api/engagements/{engagement_id}/state', {
      params: { path: { engagement_id: 'e1' } },
      body: { state: 'concluded', ended_on: '2026-11-10' },
    })
  })

  it('offers «Annulla l\'incarico…» before the start date', async () => {
    answering({ engagement: engagement({ state: 'awaiting_signature', starts_on: '2026-12-01' }) })
    renderPage()
    expect(await screen.findByRole('button', { name: "Annulla l'incarico…" })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: "Chiudi l'incarico…" })).not.toBeInTheDocument()
  })
})

describe('the letter and the months of a draft', () => {
  it('leads with the letter, reads no days and says when the months appear', async () => {
    answering({ engagement: engagement({ missing: ['days_per_week'], waiting: [{ code: 'letter_not_created' }, { code: 'missing_days_per_week' }] }) })
    const user = userEvent.setup()
    renderPage()
    expect(await screen.findByRole('heading', { level: 2, name: 'Lettera e firma' })).toBeInTheDocument()
    expect(screen.getByText("I mesi compaiono quando l'incarico parte.")).toBeInTheDocument()
    expect(screen.getByText('Mancano i giorni a settimana')).toBeInTheDocument()
    expect(screen.getByText(/450,00.*al giorno · 4 giorni a settimana/)).toBeInTheDocument()
    expect(screen.getByText('30 giorni dalla fattura')).toBeInTheDocument()
    expect(api.GET).not.toHaveBeenCalledWith('/api/engagements/{engagement_id}/report', expect.anything())
    expect(screen.queryByRole('group', { name: 'Pagabili' })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Completa' }))
    expect(screen.getByText('Modulo di modifica')).toBeInTheDocument()
  })

  it('previews the letter as text and lists the blanks', async () => {
    answering({ engagement: engagement() })
    vi.mocked(api.POST).mockResolvedValue(
      ok({
        kind: 'engagement_letter',
        letter_type: 'ordinary',
        template_id: 't1',
        text_version: '1',
        draft: false,
        number: null,
        blank: ['luogo'],
        markdown: '# Lettera <b>di incarico</b>',
      }) as never,
    )
    const user = userEvent.setup()
    renderPage()
    await user.type(await screen.findByLabelText('Attività'), 'Sviluppo API')
    await user.click(screen.getByRole('button', { name: 'Anteprima' }))
    expect(await screen.findByText('# Lettera <b>di incarico</b>')).toBeInTheDocument()
    expect(screen.getByText(/Resta in bianco nella lettera: luogo/)).toBeInTheDocument()
    expect(api.POST).toHaveBeenCalledWith('/api/engagements/{engagement_id}/letter/preview', {
      params: { path: { engagement_id: 'e1' } },
      body: { variables: { attivita: 'Sviluppo API' } },
    })
  })

  it('creates the letter from the typed free-text fields', async () => {
    answering({ engagement: engagement() })
    vi.mocked(api.POST).mockResolvedValue(ok({ blank: [], number: '2026-014' }) as never)
    const user = userEvent.setup()
    renderPage()
    await user.click(
      within(await screen.findByRole('region', { name: 'Lettera e firma' })).getByRole('button', { name: 'Crea la lettera' }),
    )
    expect(api.POST).toHaveBeenCalledWith('/api/engagements/{engagement_id}/letter', {
      params: { path: { engagement_id: 'e1' } },
      body: { variables: {} },
    })
  })

  it('sends a draft that has its letter for signature', async () => {
    answering({ engagement: engagement({ letter_document_id: 'doc1', letter_number: '2026-014' }) })
    vi.mocked(api.POST).mockResolvedValue(ok({}) as never)
    const user = userEvent.setup()
    renderPage()
    expect(await screen.findByRole('button', { name: 'Apri il PDF' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Crea la lettera' })).not.toBeInTheDocument()
    await user.click(within(screen.getByRole('region', { name: 'Lettera e firma' })).getByRole('button', { name: 'Invia per la firma' }))
    expect(api.POST).toHaveBeenCalledWith('/api/engagements/{engagement_id}/state', {
      params: { path: { engagement_id: 'e1' } },
      body: { state: 'awaiting_signature' },
    })
  })

  it('waits for the signature and records it by hand', async () => {
    answering({ engagement: engagement({ state: 'awaiting_signature', letter_document_id: 'doc1' }) })
    vi.mocked(api.POST).mockResolvedValue(ok({}) as never)
    const user = userEvent.setup()
    renderPage()
    expect(await screen.findByText(/La firma si registra a mano, per ora/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Segna come firmata' }))
    expect(api.POST).toHaveBeenCalledWith('/api/engagements/{engagement_id}/state', {
      params: { path: { engagement_id: 'e1' } },
      body: { state: 'active' },
    })
  })

  it('is read-only once the engagement is closed', async () => {
    answering({
      engagement: engagement({ state: 'concluded', ends_on: '2026-10-30', letter_document_id: 'doc1', waiting: [] }),
    })
    renderPage()
    expect(await screen.findByRole('button', { name: 'Apri il PDF' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Modifica' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /l'incarico…/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Invia per la firma' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Segna come firmata' })).not.toBeInTheDocument()
  })
})

describe('on a phone', () => {
  it('draws one card per month, no table, and a bar that decides the month', async () => {
    viewport(390)
    answering()
    const user = userEvent.setup()
    renderPage()
    expect(await screen.findByRole('button', { name: 'Apri ottobre 2026' })).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Decidi il rendiconto di ottobre' })).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Decidi il rendiconto di ottobre' }))
    const sheet = await screen.findByRole('dialog')
    expect(within(sheet).getByRole('button', { name: 'Approva il mese' })).toBeInTheDocument()
    expect(within(sheet).getByRole('button', { name: 'Contesta dei giorni…' })).toBeInTheDocument()
  })
})
