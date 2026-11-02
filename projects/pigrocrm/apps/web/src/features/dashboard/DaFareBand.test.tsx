import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import type { Attivita } from '@/features/calendar/queries'
import { useOverdueCount } from '@/features/attivita/queries'
import { BAND_ROWS, DaFareBand } from './DaFareBand'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PATCH: vi.fn(), DELETE: vi.fn() } }
})

const ME = '33333333-3333-7333-8333-333333333333'

vi.mock('@/lib/auth', () => ({
  useCanWrite: () => true,
  useIsAdmin: () => true,
  useAuth: () => ({ user: { id: ME, nome: 'Giulia Demo', ruolo: 'admin' }, isLoading: false }),
}))

vi.mock('@rebase/ui/sonner', () => ({ toast: { info: vi.fn(), success: vi.fn(), error: vi.fn() } }))

vi.mock('@tanstack/react-router', () => ({
  Link: ({
    children,
    to,
    search,
  }: {
    children: React.ReactNode
    to: string
    search?: Record<string, string>
  }) => (
    <a href={to} data-search={JSON.stringify(search ?? {})}>
      {children}
    </a>
  ),
}))

function row(over: Partial<Attivita>): Attivita {
  return {
    id: 'a',
    titolo: 'x',
    note: null,
    scadenza: null,
    stato: 'aperta',
    completata_il: null,
    assegnata_a: ME,
    customer_id: null,
    person_id: null,
    deal_id: null,
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

interface Gruppo {
  totale: number
  items: Attivita[]
}

const gruppo = (items: Attivita[], totale = items.length): Gruppo => ({ totale, items })

/** The server's panel, held in a variable so completing a row can take it out. */
let panel: {
  oggi: string
  in_ritardo: Gruppo
  scadono_oggi: Gruppo
  prossimi_sette_giorni: Gruppo
  senza_scadenza: Gruppo
}

function serve() {
  vi.mocked(api.GET).mockImplementation(((path: string) => {
    if (path === '/api/activities/oggi') return ok(panel)
    if (path === '/api/users') return ok([{ id: ME, nome: 'Giulia Demo', attivo: true }])
    throw new Error(`unexpected GET ${path}`)
  }) as never)
  vi.mocked(api.POST).mockImplementation(((path: string, options: { params: { path: { attivita_id: string } } }) => {
    if (path === '/api/activities/{attivita_id}/complete') {
      const id = options.params.path.attivita_id
      panel = {
        ...panel,
        in_ritardo: gruppo(panel.in_ritardo.items.filter((item) => item.id !== id)),
        scadono_oggi: gruppo(panel.scadono_oggi.items.filter((item) => item.id !== id)),
      }
      return ok(row({ id, stato: 'completata' }))
    }
    throw new Error(`unexpected POST ${path}`)
  }) as never)
}

/** The sidebar reads the same panel for its overdue count; it must move with the band. */
function Count() {
  return <span data-testid="count">{useOverdueCount() ?? 'nessuno'}</span>
}

function renderBand() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <Count />
      <DaFareBand />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  panel = {
    oggi: '2026-10-26',
    in_ritardo: gruppo([
      row({ id: 'late-1', titolo: 'Sollecitare Rossi', scadenza: '2026-10-20' }),
      row({ id: 'late-2', titolo: 'Inviare il preventivo', scadenza: '2026-10-25' }),
    ]),
    scadono_oggi: gruppo([row({ id: 'today', titolo: 'Telefonare a Bianchi', scadenza: '2026-10-26' })]),
    prossimi_sette_giorni: gruppo([row({ id: 'soon', titolo: 'Preventivo Acme', scadenza: '2026-10-28' })]),
    senza_scadenza: gruppo([row({ id: 'undated', titolo: 'Chiedere il codice SDI' })]),
  }
  serve()
})

describe('DaFareBand', () => {
  it('says the real counts in its heading and lists the late ones first, then today\'s', async () => {
    renderBand()

    expect(
      await screen.findByRole('heading', { level: 2, name: 'Da fare: 2 in ritardo, 1 oggi' }),
    ).toBeInTheDocument()
    const titles = screen.getAllByRole('listitem').map((item) => item.textContent ?? '')
    expect(titles[0]).toContain('Sollecitare Rossi')
    expect(titles[1]).toContain('Inviare il preventivo')
    expect(titles[2]).toContain('Telefonare a Bianchi')
    // The next seven days and the undated are the Attività page's, not the Home's.
    expect(screen.queryByText('Preventivo Acme')).not.toBeInTheDocument()
    expect(screen.queryByText('Chiedere il codice SDI')).not.toBeInTheDocument()
  })

  it('draws every row as the row the Attività page uses: Fatto, Modifica, and the day of the late ones', async () => {
    renderBand()
    await screen.findByText('Sollecitare Rossi')

    expect(screen.getAllByRole('button', { name: 'Fatto' })).toHaveLength(3)
    expect(screen.getAllByRole('button', { name: 'Modifica' })).toHaveLength(3)
    expect(screen.getByText('Entro il 20/10/2026')).toBeInTheDocument()
    // Today's row does not repeat the day.
    expect(screen.queryByText('Entro il 26/10/2026')).not.toBeInTheDocument()
  })

  it('asks for mine and nobody\'s, a few rows per section', async () => {
    renderBand()
    await screen.findByText('Sollecitare Rossi')

    expect(api.GET).toHaveBeenCalledWith('/api/activities/oggi', {
      params: { query: { assegnata_a: ME, anche_non_assegnate: true, limit: BAND_ROWS } },
    })
  })

  it('names only the section that has something', async () => {
    panel.in_ritardo = gruppo([])
    renderBand()
    expect(
      await screen.findByRole('heading', { level: 2, name: 'Da fare: 1 oggi' }),
    ).toBeInTheDocument()
  })

  it('links to the whole list', async () => {
    renderBand()
    const link = await screen.findByRole('link', { name: 'Tutte le attività' })
    expect(link).toHaveAttribute('href', '/app/activities')
    expect(link).toHaveAttribute('data-search', JSON.stringify({ vista: 'tutte' }))
    // Nothing is left out here, so there is nothing to count.
    expect(screen.queryByText(/e altre/)).not.toBeInTheDocument()
  })

  it('stops at five rows and says how many more there are, counting what the server did not send', async () => {
    panel.in_ritardo = gruppo(
      Array.from({ length: 5 }, (_, index) =>
        row({ id: `late-${index}`, titolo: `In ritardo ${index}`, scadenza: '2026-10-20' }),
      ),
      12,
    )
    renderBand()

    expect(
      await screen.findByRole('heading', { level: 2, name: 'Da fare: 12 in ritardo, 1 oggi' }),
    ).toBeInTheDocument()
    expect(screen.getAllByRole('listitem')).toHaveLength(5)
    // 12 late and 1 today, 5 shown.
    expect(screen.getByText('e altre 8')).toBeInTheDocument()
    expect(screen.queryByText('Telefonare a Bianchi')).not.toBeInTheDocument()
  })

  it('is one line, and not a card, when nothing is late or due today', async () => {
    panel.in_ritardo = gruppo([])
    panel.scadono_oggi = gruppo([])
    renderBand()

    const line = await screen.findByText('Niente in ritardo né in scadenza oggi.')
    expect(line.tagName).toBe('P')
    expect(screen.queryByRole('heading')).not.toBeInTheDocument()
    expect(screen.queryByRole('list')).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Tutte le attività' })).not.toBeInTheDocument()
  })

  it('takes a row out, and the sidebar count with it, when Fatto is pressed', async () => {
    renderBand()
    await screen.findByText('Sollecitare Rossi')
    await waitFor(() => expect(screen.getByTestId('count')).toHaveTextContent('2'))

    const item = screen.getByText('Sollecitare Rossi').closest('li')!
    await userEvent.click(within(item).getByRole('button', { name: 'Fatto' }))

    await waitFor(() => expect(screen.queryByText('Sollecitare Rossi')).not.toBeInTheDocument())
    expect(api.POST).toHaveBeenCalledWith('/api/activities/{attivita_id}/complete', {
      params: { path: { attivita_id: 'late-1' } },
    })
    // The counts in the heading follow the refetch.
    expect(
      await screen.findByRole('heading', { level: 2, name: 'Da fare: 1 in ritardo, 1 oggi' }),
    ).toBeInTheDocument()
    await waitFor(() => expect(screen.getByTestId('count')).toHaveTextContent('1'))
  })

  it('turns into the one line when the last row is done', async () => {
    panel.in_ritardo = gruppo([])
    renderBand()
    const item = (await screen.findByText('Telefonare a Bianchi')).closest('li')!
    await userEvent.click(within(item).getByRole('button', { name: 'Fatto' }))

    expect(await screen.findByText('Niente in ritardo né in scadenza oggi.')).toBeInTheDocument()
  })

  it('says so when the panel cannot be read, instead of claiming there is nothing to do', async () => {
    vi.mocked(api.GET).mockImplementation((() =>
      Promise.resolve({
        error: { detail: 'Il database non risponde.' },
        response: new Response(null, { status: 500 }),
      })) as never)
    renderBand()

    expect(await screen.findByRole('alert')).toHaveTextContent('Il database non risponde.')
    expect(screen.queryByText('Niente in ritardo né in scadenza oggi.')).not.toBeInTheDocument()
  })
})
