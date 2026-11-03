import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from '@tanstack/react-router'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { invoiceLabel, periodParam } from '@/lib/report'
import { AdminActuals } from './MatchReport'

function answer(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

const DEAL_URL = 'https://pigro.letsrebase.com/ada/app/deal/6f1c2d3e-0000-4000-8000-000000000009'

/** The match as `GET /api/hub/matches/{id}` answers it, the fields the page reads. */
const MATCH = {
  id: 'm1',
  freelancer_id: 'f1',
  company_name: 'ACME Srl',
  requested_role: 'Backend developer',
  status: 'active',
  letter: { id: 'd1', number: '2026-001' },
  expected_days: 40,
  letter_start_date: '2026-09-28',
  letter_end_date: null,
  pigro_status: 'linked',
  pigro_url: DEAL_URL,
}

const INVOICE_12 = { number: '12/2026', kind: 'invoice' }
const PROFORMA_4 = { number: '4/2026', kind: 'proforma' }

/** A day of eight hours, all of them on `invoice` when there is one. */
const day = (data: string, invoice?: { number: string; kind: string }, descriptions = ['Sviluppo']) => ({
  data,
  hours: '8.00',
  descriptions,
  invoices: invoice ? [invoiceLabel(invoice)] : [],
  hours_by_invoice: invoice ? [{ ...invoice, hours: '8.00' }] : [],
})

/** A recorded report of the whole engagement: twelve days of eight hours, invoice
 *  12/2026 across September and October (on 2 October only 3 of the day's 8 hours, the
 *  rest not billed yet), a proforma in October, November not billed. */
const REPORT = {
  match_id: 'm1',
  pigro_url: DEAL_URL,
  pigro_status: 'linked',
  expected_days: 40,
  expected_hours: '320.00',
  hours_total: '96.00',
  equivalent_days: '12.00',
  progress: '30.00',
  invoiced_hours: '43.00',
  hours_uninvoiced: '53.00',
  by_day: [
    day('2026-09-29', INVOICE_12, ['Setup']),
    day('2026-09-30', INVOICE_12),
    day('2026-10-01', INVOICE_12),
    {
      data: '2026-10-02',
      hours: '8.00',
      descriptions: ['Sviluppo API', 'Riunione'],
      invoices: ['12/2026'],
      hours_by_invoice: [{ ...INVOICE_12, hours: '3.00' }],
    },
    day('2026-10-05', PROFORMA_4),
    day('2026-10-06', PROFORMA_4),
    day('2026-10-26'),
    day('2026-10-27'),
    day('2026-11-02'),
    day('2026-11-03'),
    day('2026-11-04'),
    day('2026-11-05'),
  ],
  by_week: [
    { week: '2026-W40', da: '2026-09-28', a: '2026-10-04', hours: '32.00' },
    { week: '2026-W41', da: '2026-10-05', a: '2026-10-11', hours: '16.00' },
    { week: '2026-W44', da: '2026-10-26', a: '2026-11-01', hours: '16.00' },
    { week: '2026-W45', da: '2026-11-02', a: '2026-11-08', hours: '32.00' },
  ],
  by_month: [
    { month: '2026-09', hours: '16.00' },
    { month: '2026-10', hours: '48.00' },
    { month: '2026-11', hours: '32.00' },
  ],
  invoices: [
    { number: '4/2026', kind: 'proforma', data: '2026-10-31', status: 'confirmed', payment_status: 'from_unpaid', hours: '16.00' },
    { number: '12/2026', kind: 'invoice', data: '2026-10-02', status: 'issued', payment_status: 'paid', hours: '27.00' },
  ],
}

/** The page's own route under a pathless `signedIn`, as `router.tsx` nests it, with the
 *  same `validateSearch`. */
function mount(path: string) {
  const root = createRootRoute({ component: () => <Outlet /> })
  const signedIn = createRoute({ getParentRoute: () => root, id: 'signedIn', component: () => <Outlet /> })
  const report = createRoute({
    getParentRoute: () => signedIn,
    path: '/admin/matches/$id/report',
    component: AdminActuals,
    validateSearch: (search: Record<string, unknown>): { month?: string } => ({ month: periodParam(search.month) }),
  })
  const router = createRouter({
    routeTree: root.addChildren([signedIn.addChildren([report])]),
    history: createMemoryHistory({ initialEntries: [path] }),
  })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return router
}

/** The hub's API: the match, and the report or its refusal. */
function serve(report: Response | (() => Promise<Response>), match: unknown = MATCH) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input)
    if (url.startsWith('/api/hub/matches/m1/report')) return typeof report === 'function' ? report() : report.clone()
    if (url === '/api/hub/matches/m1') return answer(200, match)
    return answer(404, { detail: 'Non trovato.' })
  })
}

function region(name: string) {
  return screen.getByRole('region', { name })
}

/** The body rows of a section's table, each as its cells' text. */
function rows(name: string): string[][] {
  const table = within(region(name)).getByRole('table')
  return within(table)
    .getAllByRole('row')
    .slice(1)
    .map((row) => within(row).getAllByRole('cell').map((cell) => cell.textContent ?? ''))
}

function headers(name: string): string[] {
  return within(region(name))
    .getAllByRole('columnheader')
    .map((cell) => cell.textContent ?? '')
}

beforeEach(() => {
  // Only the clock: timers stay real for the router, the queries and user-event.
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-11-15T10:00:00Z'))
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('«Consuntivo» (REB-503)', () => {
  it('names the match, links its deal on Pigro and reads the whole engagement in one call', async () => {
    const spy = serve(answer(200, REPORT))
    mount('/admin/matches/m1/report')

    expect(await screen.findByText('96 ore, 12 giorni su 40 previsti (30%)')).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1, name: 'Consuntivo' })).toBeInTheDocument()
    expect(await screen.findByText('ACME Srl · Backend developer · lettera n. 2026-001')).toBeInTheDocument()
    const deal = screen.getByRole('link', { name: 'Apri il deal su Pigro' })
    expect(deal).toHaveAttribute('href', DEAL_URL)
    expect(deal).toHaveAttribute('target', '_blank')
    const reports = spy.mock.calls.map((call) => String(call[0])).filter((url) => url.includes('/report'))
    // No period in the request: the month is sliced here, from the whole engagement.
    expect(reports).toEqual(['/api/hub/matches/m1/report'])
  })

  it('opens on the current month: its days, its weeks, its total, and no invoice yet', async () => {
    serve(answer(200, REPORT))
    mount('/admin/matches/m1/report')

    expect(await screen.findByRole('combobox', { name: 'Periodo' })).toHaveTextContent('Novembre 2026')
    expect(headers('Per giorno')).toEqual(['Data', 'Ore', 'Descrizione', 'Fatture'])
    expect(rows('Per giorno')).toEqual([
      ['lun 2 nov 2026', '8', 'Sviluppo', 'da fatturare'],
      ['mar 3 nov 2026', '8', 'Sviluppo', 'da fatturare'],
      ['mer 4 nov 2026', '8', 'Sviluppo', 'da fatturare'],
      ['gio 5 nov 2026', '8', 'Sviluppo', 'da fatturare'],
    ])
    expect(rows('Per settimana')).toEqual([['2 nov 2026 – 8 nov 2026', '32']])
    expect(rows('Per mese')).toEqual([['Novembre 2026', '32']])
    expect(within(region('Fatture')).getByText('Nessuna fattura in questo periodo.')).toBeInTheDocument()
  })

  it('slices another month from the same report, and the URL keeps it', async () => {
    const spy = serve(answer(200, REPORT))
    const router = mount('/admin/matches/m1/report')
    const user = userEvent.setup()

    await user.click(await screen.findByRole('combobox', { name: 'Periodo' }))
    expect(screen.getAllByRole('option').map((option) => option.textContent)).toEqual([
      "Tutto l'incarico",
      'Novembre 2026',
      'Ottobre 2026',
      'Settembre 2026',
    ])
    await user.click(screen.getByRole('option', { name: 'Ottobre 2026' }))

    await waitFor(() => expect(router.state.location.search).toMatchObject({ month: '2026-10' }))
    expect(screen.getByRole('combobox', { name: 'Periodo' })).toHaveTextContent('Ottobre 2026')
    expect(rows('Per giorno')).toEqual([
      ['gio 1 ott 2026', '8', 'Sviluppo', '12/2026'],
      ['ven 2 ott 2026', '8', 'Sviluppo API · Riunione', '12/2026'],
      ['lun 5 ott 2026', '8', 'Sviluppo', 'proforma 4/2026'],
      ['mar 6 ott 2026', '8', 'Sviluppo', 'proforma 4/2026'],
      ['lun 26 ott 2026', '8', 'Sviluppo', 'da fatturare'],
      ['mar 27 ott 2026', '8', 'Sviluppo', 'da fatturare'],
    ])
    // Week 40 runs into September: October's share of it alone.
    expect(rows('Per settimana')).toEqual([
      ['28 set 2026 – 4 ott 2026', '16'],
      ['5 ott 2026 – 11 ott 2026', '16'],
      ['26 ott 2026 – 1 nov 2026', '16'],
    ])
    expect(rows('Per mese')).toEqual([['Ottobre 2026', '48']])
    expect(headers('Fatture')).toEqual(['Numero', 'Data', 'Stato', 'Incasso', 'Ore'])
    // 12/2026 holds 27 hours, 11 of them in October: 2 October gives it its own 3, not
    // the day's 8 (REB-505).
    expect(rows('Fatture')).toEqual([
      ['proforma 4/2026', '31 ott 2026', 'Confermata', 'Da incassare', '16'],
      ['12/2026', '2 ott 2026', 'Emessa', 'Incassato', '11'],
    ])
    // The progress line is the engagement's, whatever the month.
    expect(screen.getByText('96 ore, 12 giorni su 40 previsti (30%)')).toBeInTheDocument()
    expect(spy.mock.calls.filter((call) => String(call[0]).includes('/report'))).toHaveLength(1)
  })

  it('shows the other half of the two-month invoice in September', async () => {
    serve(answer(200, REPORT))
    mount('/admin/matches/m1/report?month=2026-09')

    expect(await screen.findByRole('combobox', { name: 'Periodo' })).toHaveTextContent('Settembre 2026')
    expect(rows('Per giorno').map((row) => row[0])).toEqual(['mar 29 set 2026', 'mer 30 set 2026'])
    expect(rows('Per giorno')[0]![2]).toBe('Setup')
    expect(rows('Fatture')).toEqual([['12/2026', '2 ott 2026', 'Emessa', 'Incassato', '16']])
  })

  it('reads «Tutto l’incarico» from the URL: every day, every month, every invoice with all its hours', async () => {
    serve(answer(200, REPORT))
    mount('/admin/matches/m1/report?month=tutto')

    expect(await screen.findByRole('combobox', { name: 'Periodo' })).toHaveTextContent("Tutto l'incarico")
    expect(rows('Per giorno')).toHaveLength(12)
    expect(rows('Per settimana')).toHaveLength(4)
    expect(rows('Per mese')).toEqual([
      ['Settembre 2026', '16'],
      ['Ottobre 2026', '48'],
      ['Novembre 2026', '32'],
    ])
    expect(rows('Fatture')).toEqual([
      ['proforma 4/2026', '31 ott 2026', 'Confermata', 'Da incassare', '16'],
      ['12/2026', '2 ott 2026', 'Emessa', 'Incassato', '27'],
    ])
  })

  it('says the hours and the days alone for a match with no estimate', async () => {
    serve(answer(200, { ...REPORT, expected_days: null, expected_hours: null, progress: null }))
    mount('/admin/matches/m1/report')
    expect(await screen.findByText('96 ore, 12 giorni')).toBeInTheDocument()
  })

  it('says a period has no hours when the deal has none yet', async () => {
    serve(
      answer(200, {
        ...REPORT,
        hours_total: '0.00',
        equivalent_days: '0.00',
        progress: '0.00',
        invoiced_hours: '0.00',
        hours_uninvoiced: '0.00',
        by_day: [],
        by_week: [],
        by_month: [],
        invoices: [],
      }),
    )
    mount('/admin/matches/m1/report')
    expect(await screen.findByText('0 ore, 0 giorni su 40 previsti (0%)')).toBeInTheDocument()
    expect(screen.getByText('Nessuna ora in questo periodo.')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Per settimana' })).toBeNull()
    expect(within(region('Fatture')).getByText('Nessuna fattura in questo periodo.')).toBeInTheDocument()
  })

  it('says it is loading while the CRM answers', async () => {
    serve(() => new Promise<Response>(() => {}))
    mount('/admin/matches/m1/report')
    expect(await screen.findByText('Caricamento…')).toBeInTheDocument()
  })

  it.each([
    [409, 'Pigro non ha ancora il deal: riprova o aspetta lo sweep.'],
    [502, 'Pigro non risponde.'],
    [503, 'Consuntivo non configurato su questo ambiente.'],
  ])('shows the API’s sentence for a %i as a paragraph, and no report', async (status, sentence) => {
    serve(answer(status, { detail: sentence }), { ...MATCH, pigro_status: 'to_link', pigro_url: null })
    mount('/admin/matches/m1/report')

    const paragraph = await screen.findByText(sentence)
    expect(paragraph.tagName).toBe('P')
    expect(screen.queryByRole('combobox', { name: 'Periodo' })).toBeNull()
    expect(screen.queryByRole('table')).toBeNull()
    expect(screen.queryByRole('link', { name: 'Apri il deal su Pigro' })).toBeNull()
    // The match is still named, so the admin knows which one refused.
    expect(await screen.findByText('ACME Srl · Backend developer · lettera n. 2026-001')).toBeInTheDocument()
  })

  it('says it cannot read the report when the refusal comes with no sentence', async () => {
    serve(answer(409, { detail: '' }), { ...MATCH, pigro_status: null, pigro_url: null })
    mount('/admin/matches/m1/report')

    const paragraph = await screen.findByText('Non riesco a leggere il consuntivo.')
    expect(paragraph.tagName).toBe('P')
    expect(screen.queryByRole('table')).toBeNull()
  })
})
