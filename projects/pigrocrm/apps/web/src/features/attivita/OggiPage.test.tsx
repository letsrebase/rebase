import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { OggiPage } from './OggiPage'
import { useOverdueCount } from './queries'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PATCH: vi.fn(), DELETE: vi.fn() } }
})

const ME = '33333333-3333-7333-8333-333333333333'

vi.mock('@/lib/auth', () => ({
  useCanWrite: () => true,
  useIsAdmin: () => true,
  useAuth: () => ({ user: { id: ME, nome: 'Lorenzo', ruolo: 'admin' }, isLoading: false }),
}))

vi.mock('@rebase/ui/sonner', () => ({ toast: { info: vi.fn(), success: vi.fn(), error: vi.fn() } }))

vi.mock('@tanstack/react-router', () => ({
  Link: ({ children, search }: { children: React.ReactNode; search?: Record<string, string> }) => (
    <a href="/x" data-search={JSON.stringify(search ?? {})}>
      {children}
    </a>
  ),
}))

function row(over: Record<string, unknown>) {
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

const gruppo = (items: unknown[], totale = items.length) => ({ totale, items })

/** The server's panel, held in a variable so completing a row can take it out. */
let panel: {
  oggi: string
  in_ritardo: ReturnType<typeof gruppo>
  scadono_oggi: ReturnType<typeof gruppo>
  prossimi_sette_giorni: ReturnType<typeof gruppo>
  senza_scadenza: ReturnType<typeof gruppo>
}

function serve() {
  vi.mocked(api.GET).mockImplementation(((path: string) => {
    if (path === '/api/activities/oggi') return ok(panel)
    if (path === '/api/users') return ok([{ id: ME, nome: 'Lorenzo', attivo: true }])
    throw new Error(`unexpected GET ${path}`)
  }) as never)
  vi.mocked(api.POST).mockImplementation(((path: string) => {
    if (path === '/api/activities/{attivita_id}/complete') {
      panel = { ...panel, in_ritardo: gruppo([]) }
      return ok(row({ stato: 'completata' }))
    }
    throw new Error(`unexpected POST ${path}`)
  }) as never)
}

function Count() {
  return <span data-testid="count">{useOverdueCount() ?? 'nessuno'}</span>
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <Count />
      <OggiPage />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  panel = {
    oggi: '2026-10-26',
    in_ritardo: gruppo([row({ id: 'late', titolo: 'Sollecitare Rossi', scadenza: '2026-10-25' })]),
    scadono_oggi: gruppo([row({ id: 'today', titolo: 'Telefonare a Bianchi', scadenza: '2026-10-26' })]),
    prossimi_sette_giorni: gruppo([row({ id: 'soon', titolo: 'Preventivo Acme', scadenza: '2026-10-28' })]),
    senza_scadenza: gruppo([row({ id: 'undated', titolo: 'Chiedere il codice SDI' })]),
  }
  serve()
})

describe('OggiPage', () => {
  it('draws the four sections in the spec order, each with its row and its count', async () => {
    renderPage()

    const headings = await screen.findAllByRole('heading', { level: 2 })
    expect(headings.map((h) => h.textContent)).toEqual([
      'In ritardo1',
      'Oggi1',
      'Prossimi sette giorni1',
      // The fourth folds, and is still a heading for a screen reader.
      'Senza scadenza1',
    ])

    const late = screen.getByRole('heading', { name: /In ritardo/ }).closest('section')!
    expect(within(late).getByText('Sollecitare Rossi')).toBeInTheDocument()
    expect(screen.getByText('Telefonare a Bianchi')).toBeInTheDocument()
    expect(screen.getByText('Preventivo Acme')).toBeInTheDocument()
    expect(screen.getByText('Chiedere il codice SDI')).toBeInTheDocument()
  })

  it('asks for the signed-in user\'s own attività, and for everybody\'s on request', async () => {
    renderPage()
    await screen.findByText('Sollecitare Rossi')

    const calls = () =>
      (vi.mocked(api.GET).mock.calls as unknown as [string, { params: { query: Record<string, unknown> } }][])
        .filter(([path]) => path === '/api/activities/oggi')
        .map(([, options]) => options.params.query)
    expect(calls()).toContainEqual({ assegnata_a: ME, limit: 100 })

    await userEvent.click(screen.getByRole('combobox', { name: 'Filtra per assegnatario' }))
    await userEvent.click(await screen.findByRole('option', { name: 'Di chiunque' }))

    await waitFor(() => expect(calls()).toContainEqual({ assegnata_a: undefined, limit: 100 }))
  })

  it('takes an overdue attività out of the section and out of the count when it is completed', async () => {
    renderPage()
    expect(await screen.findByText('Sollecitare Rossi')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByTestId('count')).toHaveTextContent('1'))

    const late = screen.getByRole('heading', { name: /In ritardo/ }).closest('section')!
    await userEvent.click(within(late).getByRole('button', { name: 'Fatto' }))

    await waitFor(() => expect(screen.queryByText('Sollecitare Rossi')).not.toBeInTheDocument())
    expect(screen.getByText('Niente in ritardo.')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByTestId('count')).toHaveTextContent('nessuno'))
  })

  it('says how many are left out when a section holds more than it shows', async () => {
    panel.in_ritardo = gruppo([row({ id: 'late', titolo: 'Sollecitare Rossi' })], 120)
    renderPage()

    expect(await screen.findByText(/Ne sono mostrate 1 su 120/)).toBeInTheDocument()
    // The rows left out are listed for the person this page is showing, not for the
    // Attività page's own default.
    expect(screen.getByRole('link', { name: 'pagina Attività' })).toHaveAttribute(
      'data-search',
      JSON.stringify({ assegnata: 'io' }),
    )

    await userEvent.click(screen.getByRole('combobox', { name: 'Filtra per assegnatario' }))
    await userEvent.click(await screen.findByRole('option', { name: 'Di chiunque' }))
    await waitFor(() =>
      expect(screen.getByRole('link', { name: 'pagina Attività' })).toHaveAttribute(
        'data-search',
        JSON.stringify({ assegnata: 'tutte' }),
      ),
    )
  })
})
