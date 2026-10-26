import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { toIsoDate } from '@/lib/dates'
import { AttivitaPage } from './AttivitaPage'
import { toFilters } from './filters'

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

// Only what the page draws from the router: the link of a linked record.
vi.mock('@tanstack/react-router', () => ({
  Link: ({ children }: { children: React.ReactNode }) => <a href="/x">{children}</a>,
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

type Query = Record<string, unknown>

/** The `query` of every `GET /api/activities` the page has made, oldest first. */
function activityQueries(): Query[] {
  return (vi.mocked(api.GET).mock.calls as unknown as [string, { params: { query: Query } }][])
    .filter(([path]) => path === '/api/activities')
    .map(([, options]) => options.params.query)
}

function serve(items: unknown[]) {
  vi.mocked(api.GET).mockImplementation(((path: string) => {
    if (path === '/api/activities') return ok({ items, next_cursor: null })
    if (path === '/api/users') return ok([{ id: ME, nome: 'Lorenzo', attivo: true }])
    throw new Error(`unexpected GET ${path}`)
  }) as never)
}

function renderPage(openId?: string, onDismissOpen?: () => void) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <AttivitaPage openId={openId} onDismissOpen={onDismissOpen} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  vi.mocked(api.POST).mockImplementation((() => ok(row({}))) as never)
})

describe('toFilters', () => {
  const base = {
    stato: null,
    scadenza: null,
    assignee: 'tutte',
    meId: ME,
    link: null,
    archiviate: false,
  } as const

  it('sends nothing for «all», except the order', () => {
    expect(toFilters(base)).toEqual({ sort: 'scadenza', dir: 'asc', limit: 50 })
  })

  it('turns «mine» into the caller, and another person into their id', () => {
    expect(toFilters({ ...base, assignee: 'io' }).assegnata_a).toBe(ME)
    expect(toFilters({ ...base, assignee: 'u-2' }).assegnata_a).toBe('u-2')
  })

  it('reads «scadute» as due before today: yesterday is the inclusive bound', () => {
    const yesterday = new Date()
    yesterday.setDate(yesterday.getDate() - 1)
    expect(toFilters({ ...base, scadenza: 'scadute' }).scade_entro).toBe(toIsoDate(yesterday))
  })

  it('keeps «scadute» to the open ones whatever the state chips say', () => {
    expect(toFilters({ ...base, scadenza: 'scadute', stato: null }).stato).toBe('aperta')
  })

  it('names the linked record by the column of its kind', () => {
    expect(toFilters({ ...base, link: { kind: 'deal', id: 'd-1' } })).toMatchObject({
      deal_id: 'd-1',
    })
  })
})

describe('AttivitaPage', () => {
  it('opens on the open ones assigned to me, and says so in its controls', async () => {
    serve([row({ id: '1', titolo: 'Preparare le proposte', scadenza: '2026-10-30' })])
    renderPage()

    expect(await screen.findByText('Preparare le proposte')).toBeInTheDocument()
    expect(activityQueries()[0]).toMatchObject({ stato: 'aperta', assegnata_a: ME })
    expect(screen.getByRole('button', { name: 'Aperte' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('combobox', { name: 'Filtra per assegnatario' })).toHaveTextContent(
      'Assegnate a me',
    )
    expect(screen.getByText('30/10/2026')).toBeInTheDocument()
  })

  it('filters «scadute» on the server and completes a row from the row', async () => {
    serve([row({ id: '1', titolo: 'In ritardo', scadenza: '2026-10-01' })])
    renderPage()
    await screen.findByText('In ritardo')

    await userEvent.click(screen.getByRole('button', { name: 'Scadute' }))
    await waitFor(() =>
      expect(activityQueries().some((query) => query.scade_entro !== undefined)).toBe(true),
    )
    const scadute = activityQueries().find((query) => query.scade_entro !== undefined)
    expect(scadute).toMatchObject({ stato: 'aperta', assegnata_a: ME })

    const table = await screen.findByRole('table')
    await userEvent.click(within(table).getByRole('button', { name: 'Fatto' }))
    await waitFor(() => expect(api.POST).toHaveBeenCalled())
    expect(vi.mocked(api.POST).mock.calls[0]?.[0]).toBe('/api/activities/{attivita_id}/complete')
  })

  it('lists the archived ones on their own and offers to restore, never to archive', async () => {
    serve([row({ id: '9', titolo: 'Creata per errore' })])
    renderPage()
    await screen.findByText('Creata per errore')

    await userEvent.click(screen.getByRole('button', { name: 'Archiviate' }))
    await waitFor(() =>
      expect(activityQueries().some((query) => query.archiviate === true)).toBe(true),
    )
    await screen.findByText('Creata per errore')
    expect(screen.queryByRole('button', { name: 'Fatto' })).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Azioni su Creata per errore' }))
    expect(screen.queryByRole('menuitem', { name: 'Archivia' })).not.toBeInTheDocument()
    await userEvent.click(await screen.findByRole('menuitem', { name: 'Ripristina' }))
    await waitFor(() => expect(api.POST).toHaveBeenCalled())
    expect(vi.mocked(api.POST).mock.calls[0]?.[0]).toBe('/api/activities/{attivita_id}/restore')
  })

  it('opens the attività the global search sent it for, even when no filter shows it', async () => {
    const found = row({ id: 'found', titolo: 'Da cercare', stato: 'completata', assegnata_a: null })
    vi.mocked(api.GET).mockImplementation(((path: string) => {
      if (path === '/api/activities') return ok({ items: [], next_cursor: null })
      if (path === '/api/activities/{attivita_id}') return ok(found)
      if (path === '/api/users') return ok([])
      throw new Error(`unexpected GET ${path}`)
    }) as never)
    const onDismissOpen = vi.fn()
    renderPage('found', onDismissOpen)

    const dialog = await screen.findByRole('dialog', { name: 'Modifica attività' })
    expect(within(dialog).getByLabelText('Titolo')).toHaveValue('Da cercare')
    // Closed, so the dialog offers the way back to open.
    expect(within(dialog).getByRole('button', { name: 'Riapri' })).toBeInTheDocument()

    // Closing asks the route to take `?apri=` off the URL, so picking the same hit again
    // is a new navigation and not a no-op.
    await userEvent.click(within(dialog).getByRole('button', { name: 'Annulla' }))
    await waitFor(() => expect(onDismissOpen).toHaveBeenCalled())
  })
})
