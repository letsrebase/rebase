import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import { useState } from 'react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { TodayView } from './TodayView'
import { useOverdueCount } from './queries'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PATCH: vi.fn(), DELETE: vi.fn() } }
})

const ME = '33333333-3333-7333-8333-333333333333'

vi.mock('@/lib/auth', () => ({
  useCanWrite: () => true,
  useIsAdmin: () => true,
  useAuth: () => ({ user: { id: ME, nome: 'Lorenzo', role: 'admin' }, isLoading: false }),
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
    title: 'x',
    note: null,
    due_date: null,
    status: 'open',
    completed_at: null,
    assigned_to: ME,
    customer_id: null,
    person_id: null,
    deal_id: null,
    invoice_id: null,
    origin: 'manual',
    rule: null,
    custom_fields: {},
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z',
    ...over,
  }
}

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) }) as never
}

const group = (items: unknown[], total = items.length) => ({ total, items })

/** The server's panel, held in a variable so completing a row can take it out. */
let panel: {
  today: string
  overdue: ReturnType<typeof group>
  today_due: ReturnType<typeof group>
  next_seven_days: ReturnType<typeof group>
  without_due_date: ReturnType<typeof group>
}

function serve() {
  vi.mocked(api.GET).mockImplementation(((path: string) => {
    if (path === '/api/activities/today') return ok(panel)
    if (path === '/api/users') return ok([{ id: ME, nome: 'Lorenzo', active: true }])
    throw new Error(`unexpected GET ${path}`)
  }) as never)
  vi.mocked(api.POST).mockImplementation(((path: string) => {
    if (path === '/api/activities/{task_id}/complete') {
      panel = { ...panel, overdue: group([]) }
      return ok(row({ status: 'completed' }))
    }
    throw new Error(`unexpected POST ${path}`)
  }) as never)
}

function Count() {
  return <span data-testid="count">{useOverdueCount() ?? 'none'}</span>
}

/** The page owns the choice of person; this holds it the way `TaskPage` does. */
function Held({ initial }: { initial: string }) {
  const [assignee, setAssignee] = useState(initial)
  return <TodayView tabs={null} assignee={assignee} onAssigneeChange={setAssignee} />
}

function renderPage(initialAssignee = 'io') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <Count />
      <Held initial={initialAssignee} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  panel = {
    today: '2026-10-26',
    overdue: group([row({ id: 'late', title: 'Sollecitare Rossi', due_date: '2026-10-25' })]),
    today_due: group([row({ id: 'today', title: 'Telefonare a Bianchi', due_date: '2026-10-26' })]),
    next_seven_days: group([row({ id: 'soon', title: 'Preventivo Acme', due_date: '2026-10-28' })]),
    without_due_date: group([row({ id: 'undated', title: 'Chiedere il codice SDI' })]),
  }
  serve()
})

describe('TodayView', () => {
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

  it('is the Attività page under its own title, and opens on whoever the address names', async () => {
    renderPage('all')

    expect(screen.getByRole('heading', { level: 1, name: 'Attività' })).toBeInTheDocument()
    await screen.findByText('Sollecitare Rossi')
    expect(screen.getByRole('combobox', { name: 'Filtra per assegnatario' })).toHaveTextContent(
      'Di chiunque',
    )
    // «Di chiunque» is the whole space: no person, no «and nobody's».
    expect(vi.mocked(api.GET)).toHaveBeenCalledWith('/api/activities/today', {
      params: { query: { assigned_to: undefined, include_unassigned: undefined, limit: 100 } },
    })
  })

  it('asks for the signed-in user\'s own attività, and for everybody\'s on request', async () => {
    renderPage()
    await screen.findByText('Sollecitare Rossi')

    const calls = () =>
      (vi.mocked(api.GET).mock.calls as unknown as [string, { params: { query: Record<string, unknown> } }][])
        .filter(([path]) => path === '/api/activities/today')
        .map(([, options]) => options.params.query)
    expect(calls()).toContainEqual({
      assigned_to: ME,
      include_unassigned: true,
      limit: 100,
    })

    await userEvent.click(screen.getByRole('combobox', { name: 'Filtra per assegnatario' }))
    await userEvent.click(await screen.findByRole('option', { name: 'Di chiunque' }))

    await waitFor(() => expect(calls()).toContainEqual({
        assigned_to: undefined,
        include_unassigned: undefined,
        limit: 100,
      }))
  })

  it('takes an overdue attività out of the section and out of the count when it is completed', async () => {
    renderPage()
    expect(await screen.findByText('Sollecitare Rossi')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByTestId('count')).toHaveTextContent('1'))

    const late = screen.getByRole('heading', { name: /In ritardo/ }).closest('section')!
    await userEvent.click(within(late).getByRole('button', { name: 'Fatto' }))

    await waitFor(() => expect(screen.queryByText('Sollecitare Rossi')).not.toBeInTheDocument())
    expect(screen.getByText('Niente in ritardo.')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByTestId('count')).toHaveTextContent('none'))
  })

  it('says how many are left out when a section holds more than it shows', async () => {
    panel.overdue = group([row({ id: 'late', title: 'Sollecitare Rossi' })], 120)
    renderPage()

    expect(await screen.findByText(/Ne sono mostrate 1 su 120/)).toBeInTheDocument()
    // The rows left out are listed for the person this page is showing, not for the
    // Tasks page's own default.
    expect(screen.getByRole('link', { name: 'scheda Tutte' })).toHaveAttribute(
      'data-search',
      JSON.stringify({ view: 'all', assigned: 'io' }),
    )

    await userEvent.click(screen.getByRole('combobox', { name: 'Filtra per assegnatario' }))
    await userEvent.click(await screen.findByRole('option', { name: 'Di chiunque' }))
    await waitFor(() =>
      expect(screen.getByRole('link', { name: 'scheda Tutte' })).toHaveAttribute(
        'data-search',
        JSON.stringify({ view: 'all', assigned: 'all' }),
      ),
    )
  })
})
