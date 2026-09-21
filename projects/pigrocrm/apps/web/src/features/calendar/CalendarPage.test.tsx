import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { CalendarPage } from './CalendarPage'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PATCH: vi.fn(), DELETE: vi.fn() } }
})

vi.mock('@/lib/auth', () => ({
  useCanWrite: () => true,
  useAuth: () => ({
    user: { id: '33333333-3333-7333-8333-333333333333', ruolo: 'admin' },
    isLoading: false,
    login: vi.fn(),
    logout: vi.fn(),
  }),
}))

vi.mock('@rebase/ui/sonner', () => ({ toast: { info: vi.fn(), success: vi.fn(), error: vi.fn() } }))

// `Link` needs a router; what these tests are about is what the day shows, so the link
// is rendered as an anchor and its target asserted as a prop would be.
vi.mock('@tanstack/react-router', () => ({
  Link: ({ children, params }: { children: React.ReactNode; params?: { invoiceId?: string } }) => (
    <a href={`/app/invoices/${params?.invoiceId ?? ''}`}>{children}</a>
  ),
}))

const DEAL = 'aaaaaaaa-aaaa-7aaa-8aaa-aaaaaaaaaaaa'

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

const attivita = {
  id: 'att-1',
  titolo: 'Sollecitare Rossi',
  note: null,
  scadenza: '2026-09-18',
  stato: 'aperta',
  completata_il: null,
  assegnata_a: null,
  customer_id: null,
  person_id: null,
  deal_id: null,
  invoice_id: null,
  origine: 'manuale',
  regola: null,
  custom_fields: {},
  created_at: '2026-09-01T00:00:00Z',
  updated_at: '2026-09-01T00:00:00Z',
}

const month = {
  mese: '2026-09',
  da: '2026-09-01',
  a: '2026-09-30',
  oggi: '2026-09-09',
  ore_totali: '8.00',
  giorni: [
    {
      giorno: '2026-09-14',
      ore: '8.00',
      per_deal: [
        { deal_id: DEAL, deal_nome: 'Progetto Alfa', cliente: 'ACME S.r.l.', ore: '8.00' },
      ],
      attivita: [],
      fatture: [],
    },
    {
      giorno: '2026-09-18',
      ore: '0.00',
      per_deal: [],
      attivita: [attivita],
      fatture: [
        {
          id: 'inv-1',
          numero: '3/2026',
          cliente: 'ACME S.r.l.',
          totale: '1000.00',
          data_scadenza: '2026-09-18',
          in_ritardo: false,
        },
      ],
    },
  ],
  attivita_senza_scadenza: [{ ...attivita, id: 'att-2', titolo: 'Chiedere il codice SDI', scadenza: null }],
}

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) }) as never
}

function routeGet(overrides: Record<string, () => ReturnType<typeof ok>> = {}) {
  vi.mocked(api.GET).mockImplementation(((path: string) => {
    const response = overrides[path]
    if (response) return response()
    if (path === '/api/calendar') return ok(month)
    if (path === '/api/deals') return ok({ items: [deal], next_cursor: null })
    throw new Error(`unexpected GET ${path}`)
  }) as never)
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
})

function renderPage(onMonthChange = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <CalendarPage mese="2026-09" onMonthChange={onMonthChange} />
    </QueryClientProvider>,
  )
  return onMonthChange
}

describe('CalendarPage', () => {
  it('marks today, once, and by more than a colour', async () => {
    // `aria-current="date"` is what a screen reader reads; the pill is what an eye sees.
    // Exactly one cell carries it, or the grid would be saying today is two days.
    routeGet()
    renderPage()
    await screen.findByRole('grid')

    const marked = screen
      .getAllByRole('button')
      .filter((button) => button.getAttribute('aria-current') === 'date')
    expect(marked).toHaveLength(1)
    expect(marked[0]?.getAttribute('aria-label')).toContain('09/09/2026')
  })

  it('opens on the month it was given, with its controls', async () => {
    routeGet()
    renderPage()

    expect(await screen.findByRole('heading', { level: 1, name: 'Calendario' })).toBeInTheDocument()
    expect(screen.getByText('settembre 2026')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Questo mese' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Mese precedente' })).toBeInTheDocument()
  })

  it('asks the API for exactly one month, in one request', async () => {
    routeGet()
    renderPage()
    await screen.findByRole('grid')

    // `as unknown as` on the way out of `mock.calls`, the house pattern in every test
    // that reads an `api` call back (`CostsPanel.test.tsx`, `InvoiceActions.test.tsx`):
    // the mock is a bare `vi.fn()`, so its recorded arguments are typed `never` and a
    // plain cast is the one thing tsc refuses. Only `tsc --noEmit` sees this at all --
    // vitest transpiles without typechecking, so the suite was green while the build
    // was red.
    const calls = vi
      .mocked(api.GET)
      .mock.calls.filter((call) => (call as unknown as unknown[])[0] === '/api/calendar')
    expect(calls).toHaveLength(1)
    const first = calls[0] as unknown as [string, { params: { query: { mese: string } } }]
    expect(first[1].params.query.mese).toBe('2026-09')
  })

  it('changes month through the caller, so the URL stays the source of truth', async () => {
    routeGet()
    const onMonthChange = renderPage()
    await screen.findByRole('grid')

    await userEvent.click(screen.getByRole('button', { name: 'Mese precedente' }))
    expect(onMonthChange).toHaveBeenCalledWith('2026-08')

    await userEvent.click(screen.getByRole('button', { name: 'Mese successivo' }))
    expect(onMonthChange).toHaveBeenCalledWith('2026-10')
  })

  it('draws the hours on their day and the month total under the grid', async () => {
    routeGet()
    renderPage()
    await screen.findByRole('grid')

    expect(screen.getByRole('button', { name: '14/09/2026: 8.00 ore' })).toBeInTheDocument()
    expect(screen.getByText('8.00 h')).toBeInTheDocument()
    expect(screen.getByText(/Ore del mese/)).toBeInTheDocument()
  })

  it('shows an undated commitment under the grid and in no cell', async () => {
    // The decision of spec §3.2, seen from the interface: a date that does not exist
    // cannot be drawn in a cell, and putting it in one would say it is due that day.
    routeGet()
    renderPage()
    await screen.findByRole('grid')

    expect(screen.getByRole('heading', { level: 2, name: 'Senza scadenza' })).toBeInTheDocument()
    expect(screen.getByText('Chiedere il codice SDI')).toBeInTheDocument()
    // And not inside the grid.
    expect(screen.getByRole('grid').textContent).not.toContain('Chiedere il codice SDI')
  })

  it('opens a day and shows what is on it', async () => {
    routeGet()
    renderPage()
    await screen.findByRole('grid')

    await userEvent.click(screen.getByRole('button', { name: /^18\/09\/2026:/ }))

    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    expect(screen.getByText('Sollecitare Rossi')).toBeInTheDocument()
    // The invoice is a link to the invoice and carries no control of its own: its due
    // date belongs to the document.
    expect(screen.getByRole('link', { name: /Fattura 3\/2026/ })).toHaveAttribute(
      'href',
      '/app/invoices/inv-1',
    )
  })

  async function openDayAndPickDeal(cell: RegExp) {
    renderPage()
    await screen.findByRole('grid')
    await userEvent.click(screen.getByRole('button', { name: cell }))
    await screen.findByRole('dialog')
    await userEvent.click(screen.getByRole('combobox', { name: 'Deal' }))
    await userEvent.click(await screen.findByRole('option', { name: 'Progetto Alfa' }))
  }

  function postedBody() {
    const [path, options] = vi.mocked(api.POST).mock.calls[0] as unknown as [
      string,
      { body: Record<string, unknown> },
    ]
    return { path, body: options.body }
  }

  it('logs a full day through the ordinary time-entries endpoint', async () => {
    routeGet()
    vi.mocked(api.POST).mockImplementation((() => ok({ id: 'new', deal_id: DEAL })) as never)
    await openDayAndPickDeal(/^14\/09\/2026:/)

    await userEvent.click(screen.getByRole('button', { name: 'Giornata (8h)' }))

    await waitFor(() => expect(api.POST).toHaveBeenCalled())
    // The same endpoint the register uses, so the rate is frozen and a closed period
    // refuses: the calendar fills that form, it does not write the table.
    expect(postedBody()).toEqual({
      path: '/api/time-entries',
      body: expect.objectContaining({ deal_id: DEAL, data: '2026-09-14', ore: '8.00' }),
    })
  })

  it('logs the hours that were typed, not the preset', async () => {
    routeGet()
    vi.mocked(api.POST).mockImplementation((() => ok({ id: 'new', deal_id: DEAL })) as never)
    await openDayAndPickDeal(/^14\/09\/2026:/)

    const ore = screen.getByRole('textbox', { name: 'Ore' })
    await userEvent.clear(ore)
    await userEvent.type(ore, '3.5')
    await userEvent.click(screen.getByRole('button', { name: 'Registra 3.5 h' }))

    await waitFor(() => expect(api.POST).toHaveBeenCalled())
    expect(postedBody().body).toMatchObject({ ore: '3.5' })
  })

  it('accepts a comma, because that is what an Italian keyboard makes', async () => {
    routeGet()
    vi.mocked(api.POST).mockImplementation((() => ok({ id: 'new', deal_id: DEAL })) as never)
    await openDayAndPickDeal(/^14\/09\/2026:/)

    const ore = screen.getByRole('textbox', { name: 'Ore' })
    await userEvent.clear(ore)
    await userEvent.type(ore, '7,5')
    await userEvent.click(screen.getByRole('button', { name: 'Registra 7.5 h' }))

    await waitFor(() => expect(api.POST).toHaveBeenCalled())
    expect(postedBody().body).toMatchObject({ ore: '7.5' })
  })

  it('sends eight hours for «Giornata» even after something else was typed', async () => {
    // The defect this test exists for: `setOre('8.00')` followed by a submit that read
    // the *state* sent whatever was in the field before the click, so pressing
    // «Giornata» after typing 4 logged four hours and called it a day.
    routeGet()
    vi.mocked(api.POST).mockImplementation((() => ok({ id: 'new', deal_id: DEAL })) as never)
    await openDayAndPickDeal(/^14\/09\/2026:/)

    const ore = screen.getByRole('textbox', { name: 'Ore' })
    await userEvent.clear(ore)
    await userEvent.type(ore, '4')
    await userEvent.click(screen.getByRole('button', { name: 'Giornata (8h)' }))

    await waitFor(() => expect(api.POST).toHaveBeenCalled())
    expect(postedBody().body).toMatchObject({ ore: '8.00' })
  })

  it('refuses to send hours that are not hours, and says what one looks like', async () => {
    routeGet()
    await openDayAndPickDeal(/^14\/09\/2026:/)

    const ore = screen.getByRole('textbox', { name: 'Ore' })
    await userEvent.clear(ore)
    await userEvent.type(ore, 'otto')

    expect(screen.getByRole('button', { name: 'Registra — h' })).toBeDisabled()
    expect(screen.getByText(/da 0,25 a 24/)).toBeInTheDocument()
    expect(api.POST).not.toHaveBeenCalled()
  })

  it('creates a deadline on the day it was opened from', async () => {
    routeGet()
    vi.mocked(api.POST).mockImplementation((() => ok({ ...attivita, id: 'new' })) as never)
    renderPage()
    await screen.findByRole('grid')
    await userEvent.click(screen.getByRole('button', { name: /^18\/09\/2026:/ }))
    await screen.findByRole('dialog')

    await userEvent.type(
      screen.getByRole('textbox', { name: 'Titolo della scadenza' }),
      'Mandare il preventivo',
    )
    await userEvent.click(screen.getByRole('button', { name: 'Aggiungi' }))

    await waitFor(() => expect(api.POST).toHaveBeenCalled())
    const [path, options] = vi.mocked(api.POST).mock.calls[0] as unknown as [
      string,
      { body: Record<string, unknown> },
    ]
    expect(path).toBe('/api/activities')
    expect(options.body).toMatchObject({ titolo: 'Mandare il preventivo', scadenza: '2026-09-18' })
  })

  it('closes a commitment as done, and cancelling is a different button', async () => {
    routeGet()
    vi.mocked(api.POST).mockImplementation((() => ok(attivita)) as never)
    renderPage()
    await screen.findByRole('grid')
    await userEvent.click(screen.getByRole('button', { name: /^18\/09\/2026:/ }))
    await screen.findByRole('dialog')

    expect(screen.getByRole('button', { name: 'Annulla' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Fatto' }))

    await waitFor(() => expect(api.POST).toHaveBeenCalled())
    expect(vi.mocked(api.POST).mock.calls[0]?.[0]).toBe('/api/activities/{attivita_id}/complete')
  })

  it('says so when the month could not be read, instead of drawing an empty one', async () => {
    // An empty grid for a failed request would claim a month in which nothing happened,
    // which is exactly the claim this screen exists to make.
    vi.mocked(api.GET).mockImplementation(((path: string) => {
      if (path === '/api/calendar')
        return Promise.resolve({
          error: { detail: 'Servizio non disponibile' },
          response: new Response(null, { status: 503 }),
        }) as never
      return ok({ items: [], next_cursor: null })
    }) as never)
    renderPage()

    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(screen.queryByRole('grid')).not.toBeInTheDocument()
  })
})
