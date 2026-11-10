/**
 * «Note spese» (REB-1046, the approved brief of 2026-11-10): the administrator's tabs and acts, the
 * staff member's own list and the form that sends a note, the sentences of the brief's state table
 * and the rule that no pay figure is drawn for anyone. The server's answers are mocked at `api`
 * and typed against the generated paths.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { toast } from '@rebase/ui/sonner'
import { api, fetchWithRefresh } from '@/lib/api'
import { toIsoDate } from '@/lib/dates'
import { ExpenseNotesPage, type ExpenseNotesPageProps } from './ExpenseNotesPage'
import type { ExpenseNote, ExpenseNoteSummary } from './queries'

const session = vi.hoisted(() => ({ role: 'admin' as 'admin' | 'collaborator' | 'readonly' }))
vi.mock('@/lib/auth', () => ({
  useIsAdmin: () => session.role === 'admin',
  useAuth: () => ({ user: { id: 'u-1', role: session.role, name: 'Anna' } }),
}))
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn() }, fetchWithRefresh: vi.fn() }
})
vi.mock('@rebase/ui/sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
const view = vi.hoisted(() => ({ width: 1440 }))
vi.mock('@rebase/ui/use-media-query', () => ({
  useMediaQuery: (query: string) => view.width >= Number(/min-width:\s*(\d+)px/.exec(query)?.[1] ?? 0),
}))

const note = (id: string, overrides: Partial<ExpenseNote> = {}): ExpenseNote => ({
  id,
  staff_member_id: 'm-1',
  staff_member_name: 'Giulia Bianchi',
  legal_entity_id: 'le-1',
  kind: 'expense',
  status: 'submitted',
  date: '2026-11-02',
  description: 'Pranzo con il cliente',
  category_id: 'c-1',
  category_name: 'Pasti',
  amount: '42.50',
  kilometres: null,
  rate_per_km: null,
  route_from: null,
  route_to: null,
  inbox_item_id: 'i-1',
  receipt_name: 'scontrino-trattoria.jpg',
  submitted_by_id: 'u-9',
  submitted_by_type: 'user',
  decided_by_id: null,
  decided_by_type: null,
  decided_by_name: null,
  decided_at: null,
  rejection_reason: null,
  self_approved: false,
  purchase_id: null,
  due_item_id: null,
  reimbursement_method: null,
  reimbursement_date: null,
  payroll_month: null,
  reimbursed_on: null,
  own: false,
  can_decide: true,
  created_at: '2026-11-02T10:00:00Z',
  updated_at: '2026-11-02T10:00:00Z',
  ...overrides,
})

const summary = (overrides: Partial<ExpenseNoteSummary> = {}): ExpenseNoteSummary => ({
  staff_member_id: null,
  staff_member_name: null,
  staff_member_legal_entity_id: null,
  can_submit: false,
  to_approve: 2,
  to_reimburse: 1,
  closed: 3,
  awaiting_reimbursement: '1464.00',
  only_administrator: false,
  ...overrides,
})

const CATEGORIES = [
  { id: 'c-1', name: 'Pasti', position: 0, code: null, archived: false },
  { id: 'c-2', name: 'Taxi', position: 1, code: null, archived: false },
]

let served: {
  summary: ExpenseNoteSummary
  items: ExpenseNote[]
  total?: string
  next?: string | null
  listError?: string
}
let listQueries: Record<string, unknown>[]

function respond() {
  vi.mocked(api.GET).mockImplementation((async (
    path: string,
    init?: { params?: { query?: Record<string, unknown>; path?: Record<string, string> } },
  ) => {
    const ok = (data: unknown) => ({ data, response: new Response(null, { status: 200 }) })
    if (path === '/api/expense-notes/summary') return ok(served.summary)
    if (path === '/api/expense-notes') {
      listQueries.push(init?.params?.query ?? {})
      if (served.listError !== undefined) {
        return { error: { detail: served.listError }, response: new Response(null, { status: 500 }) }
      }
      const cursor = init?.params?.query?.cursor
      const items = cursor === undefined ? served.items : []
      return ok({
        items,
        next_cursor: cursor === undefined ? (served.next ?? null) : null,
        total_count: served.items.length,
        total_amount: served.total ?? '0.00',
      })
    }
    if (path === '/api/expense-notes/{note_id}') {
      const found = served.items.find((candidate) => candidate.id === init?.params?.path?.note_id)
      return found === undefined
        ? { error: { detail: 'La nota non c’è' }, response: new Response(null, { status: 404 }) }
        : ok(found)
    }
    if (path === '/api/cost-categories') return ok(CATEGORIES)
    if (path === '/api/staff') {
      return ok({
        items: [
          { id: 'm-1', name: 'Giulia Bianchi' },
          { id: 'm-2', name: 'Marco Rossi' },
        ],
        next_cursor: null,
      })
    }
    return ok({})
  }) as never)
}

const posted = () => vi.mocked(api.POST).mock.calls as unknown as [string, { params?: unknown; body?: unknown }][]

function renderPage(search: ExpenseNotesPageProps['search'] = {}) {
  const onSearchChange = vi.fn()
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const rendered = render(
    <QueryClientProvider client={client}>
      <ExpenseNotesPage search={search} onSearchChange={onSearchChange} />
    </QueryClientProvider>,
  )
  return { ...rendered, onSearchChange }
}

beforeEach(() => {
  session.role = 'admin'
  view.width = 1440
  listQueries = []
  served = { summary: summary(), items: [note('n-1'), note('n-2', { staff_member_id: 'm-2', staff_member_name: 'Marco Rossi', description: 'Taxi dalla stazione', category_name: 'Taxi', amount: '18.00' })], total: '60.50' }
  respond()
  vi.mocked(api.POST).mockResolvedValue({ data: {}, response: new Response(null, { status: 200 }) } as never)
})
afterEach(() => vi.clearAllMocks())

describe('an administrator\'s «Da approvare»', () => {
  it('draws the tabs with their counts and a table of «Nota», «Categoria», «Importo», «Stato»', async () => {
    renderPage()
    const table = await screen.findByRole('table')
    expect(within(table).getAllByRole('columnheader').map((header) => header.textContent)).toEqual([
      'Nota',
      'Categoria',
      'Importo',
      'Stato',
    ])
    const tabs = screen.getAllByRole('tab')
    expect(tabs.map((tab) => tab.textContent)).toEqual(['Da approvare2', 'Da rimborsare1', 'Chiuse3'])
    const first = within(table).getByText('Pranzo con il cliente').closest('tr')!
    // The description over the person and the date; the amount in the euro format of the product.
    expect(within(first).getByText('Giulia Bianchi, 02/11/2026')).toBeInTheDocument()
    expect(within(first).getByText('42,50 €')).toBeInTheDocument()
    expect(within(first).getByText('Pasti')).toBeInTheDocument()
    expect(within(first).getByText('Inviata')).toHaveAttribute('data-tone', 'gold')
    expect(listQueries[0]).toMatchObject({ bucket: 'to_approve', limit: 50 })
    // The lead figure is the server's, and so is the total of the list.
    expect(screen.getByText('In attesa di rimborso')).toBeInTheDocument()
    expect(screen.getByText('1.464,00 €')).toBeInTheDocument()
    expect(screen.getByText('60,50 €')).toBeInTheDocument()
  })

  it('shows «Nessuna nota da approvare.» when there is nothing, and nothing else asks for a person', async () => {
    served.items = []
    renderPage()
    expect(await screen.findByText('Nessuna nota da approvare.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Nuova nota spese' })).toBeNull()
  })

  it('says «Leggo le note spese…» while it reads', async () => {
    renderPage()
    expect(await screen.findByRole('status', { name: 'Leggo le note spese…' })).toBeInTheDocument()
    await screen.findByRole('table')
  })

  it('shows the banner alone, with no table and no figure under it, when the list fails', async () => {
    served.listError = 'Non riesco a leggere le note'
    renderPage()
    expect(await screen.findByText('Non riesco a leggere le note')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Riprova' })).toBeInTheDocument()
    expect(screen.queryByRole('table')).toBeNull()
    expect(screen.queryByText('60,50 €')).toBeNull()
  })

  it('opens a note from a row, by the URL', async () => {
    const user = userEvent.setup()
    const { onSearchChange } = renderPage({})
    await user.click(await screen.findByText('Pranzo con il cliente'))
    expect(onSearchChange).toHaveBeenCalledWith({ note: 'n-1' })
  })

  it('shows the note beside the table: the receipt first, the details, and «Approva» that sends the route and nothing else', async () => {
    const user = userEvent.setup()
    renderPageOpen('n-1')
    const panel = await screen.findByLabelText('Nota aperta')
    expect(await within(panel).findByLabelText('Ricevuta')).toHaveTextContent('scontrino-trattoria.jpg')
    expect(within(panel).getByText('Persona')).toBeInTheDocument()
    await user.click(within(panel).getByRole('button', { name: 'Approva' }))
    await waitFor(() =>
      expect(api.POST).toHaveBeenCalledWith('/api/expense-notes/{note_id}/approve', {
        params: { path: { note_id: 'n-1' } },
      }),
    )
    // Approving does not choose how to pay: only the one call.
    expect(posted()).toHaveLength(1)
    expect(toast.success).toHaveBeenCalledWith('Nota approvata')
  })

  it('asks a reason before «Rifiuta…» and sends it', async () => {
    const user = userEvent.setup()
    renderPageOpen('n-1')
    const panel = await screen.findByLabelText('Nota aperta')
    await user.click(await within(panel).findByRole('button', { name: 'Rifiuta…' }))
    const dialog = await screen.findByRole('dialog', { name: 'Rifiuta la nota' })
    const send = within(dialog).getByRole('button', { name: 'Rifiuta la nota' })
    expect(send).toBeDisabled()
    await user.type(within(dialog).getByLabelText('Motivo'), 'Manca lo scontrino')
    await user.click(send)
    await waitFor(() =>
      expect(api.POST).toHaveBeenCalledWith('/api/expense-notes/{note_id}/reject', {
        params: { path: { note_id: 'n-1' } },
        body: { reason: 'Manca lo scontrino' },
      }),
    )
  })

  it('shows the server\'s own sentence, in the panel, when an approval is refused', async () => {
    vi.mocked(api.POST).mockResolvedValue({
      error: { code: 'conflict', detail: 'La nota è già stata decisa' },
      response: new Response(null, { status: 409 }),
    } as never)
    const user = userEvent.setup()
    renderPageOpen('n-1')
    await user.click(await within(await screen.findByLabelText('Nota aperta')).findByRole('button', { name: 'Approva' }))
    expect(await screen.findByText('La nota è già stata decisa')).toBeInTheDocument()
  })

  it('moves between tabs by the URL', async () => {
    const user = userEvent.setup()
    const { onSearchChange } = renderPage()
    await user.click(await screen.findByRole('tab', { name: /Chiuse/ }))
    expect(onSearchChange).toHaveBeenCalledWith({ tab: 'closed', note: undefined }, { replace: true })
  })

  it('reads a page of 50 and offers «Mostra altri» with how many there are in all', async () => {
    served.next = 'n-2'
    renderPage()
    expect(await screen.findByRole('button', { name: 'Mostra altri (2 in tutto)' })).toBeInTheDocument()
  })

  it('ellipsises a long description in the cell and keeps it whole in the panel', async () => {
    const long = 'Cena di lavoro con tutto il team dopo la fiera, '.repeat(3).trim()
    served.items = [note('n-1', { description: long })]
    renderPageOpen('n-1')
    const panel = await screen.findByLabelText('Nota aperta')
    expect(await within(panel).findByText(long)).toBeInTheDocument()
    expect(screen.getAllByTitle(long).length).toBeGreaterThan(0)
  })
})

describe('an administrator\'s own note', () => {
  it('gets the sentence and no «Approva» when another administrator must decide', async () => {
    served.items = [note('n-1', { own: true, can_decide: false })]
    renderPageOpen('n-1')
    const panel = await screen.findByLabelText('Nota aperta')
    expect(await within(panel).findByText('Non puoi approvare una tua nota: la approva un altro amministratore.')).toBeInTheDocument()
    expect(within(panel).queryByRole('button', { name: 'Approva' })).toBeNull()
    expect(within(panel).queryByRole('button', { name: 'Rifiuta…' })).toBeNull()
  })

  it('gets the other sentence and «Approva» when they are the only administrator', async () => {
    served.summary = summary({ only_administrator: true })
    served.items = [note('n-1', { own: true, can_decide: true })]
    renderPageOpen('n-1')
    const panel = await screen.findByLabelText('Nota aperta')
    expect(
      await within(panel).findByText("Sei l'unico amministratore: la approvi tu e resta segnata come autoapprovata nell'esportazione."),
    ).toBeInTheDocument()
    expect(within(panel).getByRole('button', { name: 'Approva' })).toBeEnabled()
  })
})

describe('«Da rimborsare»', () => {
  const approved = (id: string, person: string, memberId: string, overrides: Partial<ExpenseNote> = {}) =>
    // As the server answers: `can_decide` is only ever true for a submitted note.
    note(id, { status: 'approved', can_decide: false, staff_member_id: memberId, staff_member_name: person, ...overrides })

  beforeEach(() => {
    served.items = [
      approved('n-3', 'Giulia Bianchi', 'm-1', { amount: '42.50' }),
      approved('n-4', 'Giulia Bianchi', 'm-1', { amount: '20.00', description: 'Parcheggio' }),
      approved('n-5', 'Marco Rossi', 'm-2', {
        reimbursement_method: 'transfer',
        reimbursement_date: '2026-11-10',
        description: 'Taxi',
      }),
    ]
  })

  it('groups the notes by person with their total, and shows the way chosen on a row', async () => {
    renderPage({ tab: 'to_reimburse' })
    const giulia = await screen.findByRole('region', { name: 'Note di Giulia Bianchi' })
    expect(within(giulia).getByText('2 note, 62,50 €')).toBeInTheDocument()
    expect(within(giulia).getAllByText('Da rimborsare').length).toBe(2)
    const marco = screen.getByRole('region', { name: 'Note di Marco Rossi' })
    expect(within(marco).getByText('Bonifico del 10/11/2026')).toBeInTheDocument()
    expect(within(marco).getByText('Approvata')).toHaveAttribute('data-tone', 'gold')
  })

  it('sends one bank transfer per note, in order, with the date and «Già pagato»', async () => {
    const user = userEvent.setup()
    renderPage({ tab: 'to_reimburse' })
    const giulia = await screen.findByRole('region', { name: 'Note di Giulia Bianchi' })
    await user.click(within(giulia).getByRole('button', { name: 'Rimborsa…' }))
    const dialog = await screen.findByRole('dialog', { name: 'Rimborsa Giulia Bianchi' })
    fireEvent.change(within(dialog).getByLabelText('Data del bonifico'), { target: { value: '2026-11-10' } })
    await user.click(within(dialog).getByLabelText('Già pagato'))
    await user.click(within(dialog).getByRole('button', { name: 'Rimborsa' }))
    await waitFor(() => expect(posted()).toHaveLength(2))
    expect(posted()).toEqual([
      [
        '/api/expense-notes/{note_id}/reimburse',
        { params: { path: { note_id: 'n-3' } }, body: { method: 'transfer', date: '2026-11-10', paid: true } },
      ],
      [
        '/api/expense-notes/{note_id}/reimburse',
        { params: { path: { note_id: 'n-4' } }, body: { method: 'transfer', date: '2026-11-10', paid: true } },
      ],
    ])
    expect(toast.success).toHaveBeenCalledWith('2 note rimborsate')
  })

  it('chooses the payslip of a month, and leaves the month to the server when none is typed', async () => {
    const user = userEvent.setup()
    renderPage({ tab: 'to_reimburse' })
    const giulia = await screen.findByRole('region', { name: 'Note di Giulia Bianchi' })
    await user.click(within(giulia).getByRole('button', { name: 'Rimborsa…' }))
    const dialog = await screen.findByRole('dialog', { name: 'Rimborsa Giulia Bianchi' })
    await user.click(within(dialog).getByRole('radio', { name: 'In busta paga' }))
    await user.click(within(dialog).getByRole('button', { name: 'Rimborsa' }))
    await waitFor(() => expect(posted()).toHaveLength(2))
    expect(posted()[0]![1].body).toEqual({ method: 'payslip', paid: false })

    vi.mocked(api.POST).mockClear()
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    await user.click(within(giulia).getByRole('button', { name: 'Rimborsa…' }))
    const again = await screen.findByRole('dialog', { name: 'Rimborsa Giulia Bianchi' })
    await user.click(within(again).getByRole('radio', { name: 'In busta paga' }))
    fireEvent.change(within(again).getByLabelText('Mese della busta paga'), { target: { value: '2026-11' } })
    expect(within(again).getByRole('radio', { name: 'In busta paga di novembre' })).toBeChecked()
    await user.click(within(again).getByRole('button', { name: 'Rimborsa' }))
    await waitFor(() => expect(posted()).toHaveLength(2))
    expect(posted()[0]![1].body).toEqual({ method: 'payslip', paid: false, payroll_month: '2026-11-01' })
  })

  it('stops at the first refusal, says it in the server\'s words and how many it did', async () => {
    vi.mocked(api.POST)
      .mockResolvedValueOnce({ data: {}, response: new Response(null, { status: 200 }) } as never)
      .mockResolvedValueOnce({
        error: { code: 'conflict', detail: 'la scadenza ha un pagamento: il modo non si cambia' },
        response: new Response(null, { status: 409 }),
      } as never)
    const user = userEvent.setup()
    renderPage({ tab: 'to_reimburse' })
    const giulia = await screen.findByRole('region', { name: 'Note di Giulia Bianchi' })
    await user.click(within(giulia).getByRole('button', { name: 'Rimborsa…' }))
    const dialog = await screen.findByRole('dialog', { name: 'Rimborsa Giulia Bianchi' })
    await user.click(within(dialog).getByRole('button', { name: 'Rimborsa' }))
    expect(await within(dialog).findByText('la scadenza ha un pagamento: il modo non si cambia')).toBeInTheDocument()
    expect(within(dialog).getByText('Fatto per 1 su 2.')).toBeInTheDocument()
    expect(posted()).toHaveLength(2)
  })

  it('offers no «Rimborsa…» for a person whose notes are the administrator\'s own, and says why', async () => {
    served.items = [approved('n-3', 'Anna', 'm-1', { own: true })]
    renderPage({ tab: 'to_reimburse' })
    const group = await screen.findByRole('region', { name: 'Note di Anna' })
    expect(within(group).queryByRole('button', { name: 'Rimborsa…' })).toBeNull()
    expect(within(group).getByText('Non puoi approvare una tua nota: la approva un altro amministratore.')).toBeInTheDocument()
  })

  it('offers it to the only administrator, even on their own notes', async () => {
    served.summary = summary({ only_administrator: true })
    served.items = [approved('n-3', 'Anna', 'm-1', { own: true })]
    renderPage({ tab: 'to_reimburse' })
    const group = await screen.findByRole('region', { name: 'Note di Anna' })
    expect(within(group).getByRole('button', { name: 'Rimborsa…' })).toBeInTheDocument()
  })

  it('reimburses one approved note from its panel', async () => {
    const user = userEvent.setup()
    renderPage({ tab: 'to_reimburse', note: 'n-3' })
    const panel = await screen.findByLabelText('Nota aperta')
    await user.click(await within(panel).findByRole('button', { name: 'Rimborsa…' }))
    const dialog = await screen.findByRole('dialog', { name: 'Rimborsa Giulia Bianchi' })
    await user.click(within(dialog).getByRole('button', { name: 'Rimborsa' }))
    await waitFor(() => expect(posted()).toHaveLength(1))
    expect(posted()[0]![0]).toBe('/api/expense-notes/{note_id}/reimburse')
    expect(posted()[0]![1].params).toEqual({ path: { note_id: 'n-3' } })
  })

  it('reads every page before it groups, so a person\'s total is complete', async () => {
    served.next = 'n-5'
    renderPage({ tab: 'to_reimburse' })
    await screen.findByRole('region', { name: 'Note di Giulia Bianchi' })
    await waitFor(() => expect(listQueries.length).toBeGreaterThanOrEqual(2))
    expect(listQueries[1]).toMatchObject({ cursor: 'n-5' })
  })
})

describe('«Chiuse»: the pills and their lines', () => {
  it('draws a rejected note with its reason on one line, a reimbursed one with its way', async () => {
    served.items = [
      note('n-1', { status: 'rejected', rejection_reason: 'Manca lo scontrino', decided_by_name: 'Anna', decided_at: '2026-11-04T09:00:00Z' }),
      note('n-2', {
        status: 'reimbursed',
        reimbursement_method: 'payslip',
        payroll_month: '2026-11-01',
        reimbursed_on: '2026-11-12',
        description: 'Taxi',
      }),
      note('n-3', { status: 'reimbursed', reimbursement_method: 'transfer', reimbursement_date: '2026-11-10', description: 'Treno' }),
    ]
    renderPage({ tab: 'closed' })
    const table = await screen.findByRole('table')
    const rejected = within(table).getByText('Pranzo con il cliente').closest('tr')!
    expect(within(rejected).getByText('Rifiutata')).toHaveAttribute('data-tone', 'danger')
    expect(within(rejected).getByText('Manca lo scontrino')).toHaveClass('truncate')
    const payslip = within(table).getByText('Taxi').closest('tr')!
    expect(within(payslip).getByText('Rimborsata')).toHaveAttribute('data-tone', 'ink')
    expect(within(payslip).getByText('In busta paga di novembre, il 12/11/2026')).toBeInTheDocument()
    expect(within(within(table).getByText('Treno').closest('tr')!).getByText('Bonifico del 10/11/2026')).toBeInTheDocument()
  })
})

function renderPageOpen(id: string) {
  return renderPage({ note: id })
}

describe('a staff member', () => {
  beforeEach(() => {
    session.role = 'collaborator'
    served.summary = summary({
      staff_member_id: 'm-1',
      staff_member_name: 'Giulia Bianchi',
      staff_member_legal_entity_id: 'le-1',
      can_submit: true,
      to_approve: 1,
      to_reimburse: 1,
      closed: 0,
    })
    served.items = [
      note('n-1'),
      note('n-2', { status: 'rejected', rejection_reason: 'Manca lo scontrino', description: 'Taxi', amount: '18.00' }),
    ]
  })

  it('reads their own notes with no tabs and no «Persona», and the lead figure «In attesa di rimborso»', async () => {
    renderPage()
    const table = await screen.findByRole('table')
    expect(screen.queryAllByRole('tab')).toEqual([])
    expect(screen.queryByRole('combobox', { name: 'Persona' })).toBeNull()
    expect(within(table).queryByText('Giulia Bianchi, 02/11/2026')).toBeNull()
    expect(within(table).getAllByText('02/11/2026').length).toBe(2)
    expect(screen.getByText('In attesa di rimborso')).toBeInTheDocument()
    expect(screen.getByText('1.464,00 €')).toBeInTheDocument()
    // What to wait for, and why a note was refused, on the staff member's own card.
    expect(within(table).getByText('Aspetta un amministratore')).toBeInTheDocument()
    expect(within(table).getByText('Manca lo scontrino')).toBeInTheDocument()
    expect(listQueries[0]).not.toHaveProperty('bucket')
  })

  it('filters by status in the URL', async () => {
    const user = userEvent.setup()
    const { onSearchChange } = renderPage()
    await user.click(await screen.findByRole('button', { name: 'Approvata' }))
    expect(onSearchChange).toHaveBeenCalledWith({ status: 'approved', note: undefined }, { replace: true })
  })

  it('opens a note with the receipt and no act at all', async () => {
    renderPageOpen('n-1')
    const panel = await screen.findByLabelText('Nota aperta')
    expect(await within(panel).findByLabelText('Ricevuta')).toBeInTheDocument()
    expect(within(panel).queryByRole('button', { name: /Approva|Rifiuta|Rimborsa/ })).toBeNull()
  })

  it('says what lives here and offers «Nuova nota spese» when there is nothing', async () => {
    served.items = []
    renderPage()
    expect(await screen.findByText('Non hai ancora inviato note spese.')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Nuova nota spese' }).length).toBe(2)
  })

  it('sends an expense with the photo stored first and its item linked, and the amount as typed', async () => {
    vi.mocked(fetchWithRefresh).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ items: [{ id: 'item-1' }] }),
    } as Response)
    const user = userEvent.setup()
    renderPage()
    await user.click((await screen.findAllByRole('button', { name: 'Nuova nota spese' }))[0]!)
    const dialog = await screen.findByRole('dialog', { name: 'Cosa vuoi farti rimborsare?' })
    // Two kinds are offered: the trip with lines is a later card.
    expect(within(dialog).getAllByRole('button').map((button) => button.textContent)).toEqual([
      expect.stringContaining('Una spesa'),
      expect.stringContaining('Chilometri'),
      'Chiudi',
    ])
    await user.click(within(dialog).getByRole('button', { name: /Una spesa/ }))
    const form = await screen.findByRole('dialog', { name: 'Una spesa' })
    await user.upload(
      within(form).getByLabelText('Scegli un file'),
      new File(['x'], 'scontrino.jpg', { type: 'image/jpeg' }),
    )
    await user.type(within(form).getByLabelText('Importo (€)'), '12,50')
    await user.type(within(form).getByLabelText('Di cosa si tratta'), 'Pranzo con il cliente')
    expect(within(form).queryByLabelText('Per chi?')).toBeNull()
    await user.click(within(form).getByRole('button', { name: 'Invia' }))
    await waitFor(() => expect(posted().some(([path]) => path === '/api/expense-notes')).toBe(true))
    expect(fetchWithRefresh).toHaveBeenCalledWith('/api/inbox/upload?read=false', expect.objectContaining({ method: 'POST' }))
    expect(posted().find(([path]) => path === '/api/expense-notes')![1]).toEqual({
      body: {
        date: toIsoDate(new Date()),
        category_id: 'c-1',
        kind: 'expense',
        description: 'Pranzo con il cliente',
        amount: '12.50',
        inbox_item_id: 'item-1',
      },
    })
    expect(toast.success).toHaveBeenCalledWith('Nota spese inviata')
  })

  it('refuses an amount that is not one before it asks the server', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click((await screen.findAllByRole('button', { name: 'Nuova nota spese' }))[0]!)
    await user.click(await screen.findByRole('button', { name: /Una spesa/ }))
    const form = await screen.findByRole('dialog', { name: 'Una spesa' })
    await user.type(within(form).getByLabelText('Importo (€)'), 'dodici')
    await user.click(within(form).getByRole('button', { name: 'Invia' }))
    expect(await within(form).findByText('Scrivi un importo, per esempio 12,50.')).toBeInTheDocument()
    expect(within(form).getByText('Scrivi di cosa si tratta.')).toBeInTheDocument()
    expect(api.POST).not.toHaveBeenCalled()
  })

  it('sends mileage as kilometres and the typed rate, with no amount, and the ACI line stands', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click((await screen.findAllByRole('button', { name: 'Nuova nota spese' }))[0]!)
    await user.click(await screen.findByRole('button', { name: /Chilometri/ }))
    const form = await screen.findByRole('dialog', { name: 'Chilometri' })
    expect(within(form).getByText('La tariffa la scrivi tu: la tabella ACI non è ancora verificata')).toBeInTheDocument()
    expect(within(form).queryByLabelText('Importo (€)')).toBeNull()
    await user.type(within(form).getByLabelText('Da'), 'Milano')
    await user.type(within(form).getByLabelText('A'), 'Torino')
    await user.type(within(form).getByLabelText('Chilometri'), '142')
    await user.type(within(form).getByLabelText('Tariffa (€ al km)'), '0,45')
    await user.click(within(form).getByRole('button', { name: 'Invia' }))
    await waitFor(() => expect(posted().some(([path]) => path === '/api/expense-notes')).toBe(true))
    const body = posted().find(([path]) => path === '/api/expense-notes')![1].body as Record<string, unknown>
    expect(body).toEqual({
      date: toIsoDate(new Date()),
      category_id: 'c-1',
      kind: 'mileage',
      description: 'Da Milano a Torino',
      kilometres: '142',
      rate_per_km: '0.45',
      route_from: 'Milano',
      route_to: 'Torino',
    })
    expect(body).not.toHaveProperty('amount')
    expect(fetchWithRefresh).not.toHaveBeenCalled()
  })

  it('keeps the form and says so when the server refuses, with the photo not sent twice', async () => {
    vi.mocked(fetchWithRefresh).mockResolvedValue({ ok: true, status: 200, json: async () => ({ items: [{ id: 'item-1' }] }) } as Response)
    vi.mocked(api.POST)
      .mockResolvedValueOnce({
        error: { code: 'validation_failed', detail: 'la persona non era in servizio', field: 'date' },
        response: new Response(null, { status: 422 }),
      } as never)
      .mockResolvedValueOnce({ data: {}, response: new Response(null, { status: 201 }) } as never)
    const user = userEvent.setup()
    renderPage()
    await user.click((await screen.findAllByRole('button', { name: 'Nuova nota spese' }))[0]!)
    await user.click(await screen.findByRole('button', { name: /Una spesa/ }))
    const form = await screen.findByRole('dialog', { name: 'Una spesa' })
    await user.upload(within(form).getByLabelText('Scegli un file'), new File(['x'], 'a.jpg', { type: 'image/jpeg' }))
    await user.type(within(form).getByLabelText('Importo (€)'), '5')
    await user.type(within(form).getByLabelText('Di cosa si tratta'), 'Caffè')
    await user.click(within(form).getByRole('button', { name: 'Invia' }))
    expect(await within(form).findByText('la persona non era in servizio')).toBeInTheDocument()
    await user.click(within(form).getByRole('button', { name: 'Invia' }))
    await waitFor(() => expect(posted()).toHaveLength(2))
    expect(fetchWithRefresh).toHaveBeenCalledTimes(1)
  })
})

describe('an administrator who writes a note for somebody', () => {
  it('is asked «Per chi?» and sends the person it names, and offers the button', async () => {
    served.summary = summary({ can_submit: true })
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Nuova nota spese' }))
    await user.click(await screen.findByRole('button', { name: /Chilometri/ }))
    const form = await screen.findByRole('dialog', { name: 'Chilometri' })
    expect(within(form).getByText('Per chi?')).toBeInTheDocument()
    await user.type(within(form).getByLabelText('Da'), 'A')
    await user.type(within(form).getByLabelText('A'), 'B')
    await user.type(within(form).getByLabelText('Chilometri'), '10')
    await user.type(within(form).getByLabelText('Tariffa (€ al km)'), '0,4')
    await user.click(within(form).getByRole('button', { name: 'Invia' }))
    // No staff member of their own and none chosen: the form says so and asks nothing.
    expect(await within(form).findByText('Scegli per chi è la nota.')).toBeInTheDocument()
    expect(api.POST).not.toHaveBeenCalled()
  })
})

describe('a member who cannot write, or is not linked', () => {
  it('reads their own notes and has no «Nuova nota spese» when readonly', async () => {
    session.role = 'readonly'
    served.summary = summary({ staff_member_id: 'm-1', staff_member_name: 'Giulia Bianchi', can_submit: false })
    renderPage()
    await screen.findByRole('table')
    expect(screen.queryByRole('button', { name: 'Nuova nota spese' })).toBeNull()
  })

  it('is told to ask for a link, with no figure and no list asked for', async () => {
    session.role = 'collaborator'
    served.summary = summary({ staff_member_id: null, awaiting_reimbursement: '0.00' })
    renderPage()
    expect(
      await screen.findByText("Non risulti tra le persone dell'azienda: chiedi a un amministratore di collegarti."),
    ).toBeInTheDocument()
    expect(document.body.textContent).not.toMatch(/€/)
    expect(listQueries).toEqual([])
  })

  it.each(['admin', 'collaborator', 'readonly'] as const)('draws no pay figure for %s', async (role) => {
    session.role = role
    served.summary =
      role === 'admin'
        ? summary()
        : summary({ staff_member_id: 'm-1', can_submit: role === 'collaborator' })
    renderPage()
    await screen.findByRole('table')
    expect(document.body.textContent).not.toMatch(/stipend|lordo|netto|ritenut|cedolin|F24|Costo di/i)
  })
})

describe('on a phone', () => {
  beforeEach(() => {
    view.width = 390
  })

  it('draws cards under a segmented control, with no table and no bulk action', async () => {
    const user = userEvent.setup()
    const { onSearchChange } = renderPage()
    const list = await screen.findByRole('list', { name: 'Note spese' })
    expect(screen.queryByRole('table')).toBeNull()
    expect(within(list).getAllByRole('listitem').length).toBe(2)
    expect(screen.queryByRole('checkbox')).toBeNull()
    const segments = within(screen.getByRole('group', { name: 'Elenco' })).getAllByRole('button')
    expect(segments.map((segment) => segment.getAttribute('aria-pressed'))).toEqual(['true', 'false', 'false'])
    await user.click(segments[1]!)
    expect(onSearchChange).toHaveBeenCalledWith({ tab: 'to_reimburse', note: undefined }, { replace: true })
  })

  it('opens a note full screen with the receipt and a sticky «Rifiuta…» and «Approva»', async () => {
    renderPageOpen('n-1')
    const sheet = await screen.findByRole('dialog', { name: 'Nota aperta' })
    expect(await within(sheet).findByLabelText('Ricevuta')).toBeInTheDocument()
    expect(within(sheet).getByRole('button', { name: 'Rifiuta…' })).toBeInTheDocument()
    expect(within(sheet).getByRole('button', { name: 'Approva' })).toBeInTheDocument()
  })

  it('opens the form full screen with a sticky «Invia»', async () => {
    session.role = 'collaborator'
    served.summary = summary({ staff_member_id: 'm-1', can_submit: true })
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Nuova nota spese' }))
    await user.click(await screen.findByRole('button', { name: /Una spesa/ }))
    const sheet = await screen.findByRole('dialog', { name: 'Una spesa' })
    expect(within(sheet).getByRole('button', { name: 'Invia' })).toBeInTheDocument()
    expect(within(sheet).getByRole('button', { name: 'Scatta una foto' })).toBeInTheDocument()
  })
})
