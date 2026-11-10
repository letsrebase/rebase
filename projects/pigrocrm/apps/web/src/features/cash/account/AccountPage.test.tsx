/**
 * The account page (REB-1025, brief «The account page» and «A statement's movements»): one test
 * per state of its two tables. Every figure is the server's string; nothing here sums.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { AccountPage } from './AccountPage'

const session = vi.hoisted(() => ({ role: 'admin' as 'admin' | 'collaborator' | 'readonly' }))
vi.mock('@/lib/auth', async () => {
  const { can } = await import('@/lib/permissions')
  return {
    useAuth: () => ({ user: { id: 'u-1', role: session.role } }),
    useCan: (action: Parameters<typeof can>[1]) => can(session.role, action),
  }
})
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PATCH: vi.fn() } }
})
vi.mock('@tanstack/react-router', () => ({
  useParams: () => ({ accountId: 'acc-1' }),
  Link: ({ to, children }: { to: string; children: React.ReactNode }) => <a href={to}>{children}</a>,
}))

type Account = components['schemas']['BankAccountRead']
type Statement = components['schemas']['StatementRead']
type Movement = components['schemas']['MovementRead']

const ACCOUNT: Account = {
  id: 'acc-1',
  legal_entity_id: 'le-1',
  legal_entity_name: 'Alfa Servizi srl',
  name: 'Conto operativo',
  bank: 'Banco BPM',
  iban: 'IT60X0542811101000000123456',
  currency: 'EUR',
  opening_balance: '10000.00',
  opening_balance_date: '2026-01-01',
  arrival: 'upload',
  cadence: 'monthly',
  senders: ['estratti@bancobpm.it'],
  archived_at: null,
  balance: '18420.50',
  balance_as_of: '2026-09-30',
  stale_days: 41,
  is_stale: true,
  unmatched_count: 3,
  created_at: '2026-01-01T00:00:00Z',
}

const MONTHS = Array.from({ length: 12 }, (_, index) => {
  const month = String(index + 1).padStart(2, '0')
  const state = index === 9 ? 'missing' : index > 9 ? 'pending' : 'covered'
  return { month: `2026-${month}`, period_from: `2026-${month}-01`, period_to: `2026-${month}-28`, state } as const
})

const IMPORTED: Statement = {
  id: 'st-1',
  bank_account_id: 'acc-1',
  period_from: '2026-09-01',
  period_to: '2026-09-30',
  format: 'camt053',
  state: 'imported',
  opening_balance: '10000.00',
  closing_balance: '18420.50',
  movement_count: 41,
  check: { ok: false, difference: '50.00', kind: 'balance' },
  created_at: '2026-10-02T08:00:00Z',
}
const REVERTED: Statement = { ...IMPORTED, id: 'st-2', period_from: '2026-08-01', period_to: '2026-08-31', state: 'reverted', check: null }

const MOVEMENTS: Movement[] = [
  {
    id: 'mv-1',
    bank_account_id: 'acc-1',
    booking_date: '2026-09-28',
    value_date: null,
    amount: '-1464.00',
    currency: 'EUR',
    counterparty_name: 'Beta Forniture spa',
    description: 'BONIFICO A VOSTRO FAVORE FT 2026/35',
    bank_reference: null,
    state: 'unmatched',
    ignored_reason: null,
    unmatched_days: 42,
    is_overdue_unmatched: true,
    statement_ids: ['st-1'],
  },
  {
    id: 'mv-2',
    bank_account_id: 'acc-1',
    booking_date: '2026-11-08',
    value_date: null,
    amount: '250.00',
    currency: 'EUR',
    counterparty_name: null,
    description: 'COMMISSIONI',
    bank_reference: null,
    state: 'unmatched',
    ignored_reason: null,
    unmatched_days: 2,
    is_overdue_unmatched: false,
    statement_ids: ['st-1'],
  },
  {
    id: 'mv-3',
    bank_account_id: 'acc-1',
    booking_date: '2026-09-20',
    value_date: null,
    amount: '-80.00',
    currency: 'EUR',
    counterparty_name: null,
    description: 'PRELIEVO',
    bank_reference: null,
    state: 'ignored',
    ignored_reason: 'uso personale',
    unmatched_days: null,
    is_overdue_unmatched: false,
    statement_ids: ['st-1'],
  },
]

interface World {
  accounts: Account[]
  statements: Statement[]
  movements: Movement[]
  unmatched: number
}

const world: World = { accounts: [], statements: [], movements: [], unmatched: 0 }

function page<T>(items: T[]) {
  return { items, next_cursor: null, total_count: items.length }
}

function install() {
  vi.mocked(api.GET).mockImplementation((async (path: string, init?: { params?: { query?: Record<string, unknown> } }) => {
    if (path === '/api/bank-accounts') return { data: world.accounts, response: new Response() }
    if (path === '/api/bank-accounts/{account_id}/coverage')
      return { data: { grace_days: 7, months: MONTHS }, response: new Response() }
    if (path === '/api/bank-accounts/{account_id}/statements')
      return { data: page(world.statements), response: new Response() }
    if (path === '/api/bank-accounts/{account_id}/movements') {
      const query = init?.params?.query ?? {}
      const rows = world.movements.filter(
        (movement) =>
          (query.state !== 'unmatched' || movement.state === 'unmatched') &&
          (query.statement_id === undefined || movement.statement_ids.includes(String(query.statement_id))),
      )
      return { data: { ...page(rows), unmatched_count: world.unmatched }, response: new Response() }
    }
    throw new Error(`unexpected GET ${path}`)
  }) as never)
}

function setViewport(wide: boolean) {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: wide,
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
  })) as unknown as typeof window.matchMedia
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <AccountPage />
    </QueryClientProvider>,
  )
}

describe('AccountPage', () => {
  beforeEach(() => {
    vi.mocked(api.GET).mockReset()
    vi.mocked(api.POST).mockReset()
    session.role = 'admin'
    setViewport(true)
    world.accounts = [ACCOUNT]
    world.statements = [IMPORTED, REVERTED]
    world.movements = MOVEMENTS
    world.unmatched = 3
    install()
  })

  it('draws the header, the balance with its stale words, the IBAN in groups and how statements arrive', async () => {
    renderPage()
    expect(await screen.findByRole('heading', { level: 1, name: 'Conto operativo' })).toBeTruthy()
    expect(screen.getByText('Cassa · Banco BPM · Alfa Servizi srl')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Modifica il conto' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Carica estratto conto' })).toBeTruthy()
    expect(screen.getByText(/18\.420,50/)).toBeTruthy()
    expect(screen.getByText('al 30/09/2026, da 41 giorni')).toBeTruthy()
    expect(screen.getByText('IT60 X054 2811 1010 0000 0123 456')).toBeTruthy()
    expect(screen.getByText('Mensile')).toBeTruthy()
    expect(screen.getByText('estratti@bancobpm.it')).toBeTruthy()
  })

  it('draws the coverage strip with the missing month dashed and named', async () => {
    renderPage()
    const strip = await screen.findByRole('region', { name: 'Estratti conto degli ultimi 12 mesi' })
    const cells = within(strip).getAllByRole('listitem')
    expect(cells).toHaveLength(12)
    const missing = cells.filter((cell) => cell.getAttribute('data-state') === 'missing')
    expect(missing).toHaveLength(1)
    expect(within(missing[0]!).getByText('manca')).toBeTruthy()
    expect(missing[0]!.className).toContain('border-dashed')
  })

  it('lists the unmatched movements first, verbatim, signed, with the old one saying how long it waits', async () => {
    renderPage()
    const table = await screen.findByRole('table')
    expect(within(table).getByText('BONIFICO A VOSTRO FAVORE FT 2026/35')).toBeTruthy()
    expect(within(table).getByText('Beta Forniture spa')).toBeTruthy()
    expect(within(table).getByText(/-1\.464,00/)).toBeTruthy()
    expect(within(table).getByText('28/09/2026')).toBeTruthy()
    const old = within(table).getByText('Da abbinare da 42 giorni')
    expect(old.closest('[data-tone]')?.getAttribute('data-tone')).toBe('danger')
    const young = within(table).getByText('Da abbinare')
    expect(young.closest('[data-tone]')?.getAttribute('data-tone')).toBe('gold')
    expect(within(table).queryByText('uso personale')).toBeNull()
    expect(screen.getByRole('button', { name: 'Da abbinare (3)', pressed: true })).toBeTruthy()
  })

  it('«Tutti» adds the ignored movement with its reason and a muted pill', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('table')
    await user.click(screen.getByRole('button', { name: 'Tutti' }))
    const table = await screen.findByText('PRELIEVO')
    expect(table).toBeTruthy()
    expect(screen.getByText('uso personale')).toBeTruthy()
    expect(screen.getByText('Ignorato').closest('[data-tone]')?.getAttribute('data-tone')).toBe('muted')
  })

  it('offers 50 at a time with «Mostra altri» when the server says there are more', async () => {
    vi.mocked(api.GET).mockImplementation((async (path: string, init?: { params?: { query?: { cursor?: string } } }) => {
      if (path === '/api/bank-accounts') return { data: world.accounts, response: new Response() }
      if (path === '/api/bank-accounts/{account_id}/coverage')
        return { data: { grace_days: 7, months: MONTHS }, response: new Response() }
      if (path === '/api/bank-accounts/{account_id}/statements')
        return { data: page(world.statements), response: new Response() }
      const second = init?.params?.query?.cursor === 'next'
      return {
        data: {
          items: [second ? MOVEMENTS[1] : MOVEMENTS[0]],
          next_cursor: second ? null : 'next',
          total_count: 2,
          unmatched_count: 3,
        },
        response: new Response(),
      }
    }) as never)
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('BONIFICO A VOSTRO FAVORE FT 2026/35')
    expect(screen.getByText('Mostrati 1 di 2 movimenti.')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Mostra altri' }))
    expect(await screen.findByText('COMMISSIONI')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Mostra altri' })).toBeNull()
  })

  it('says «Nessun movimento da abbinare.» when the queue is clear', async () => {
    world.movements = []
    world.unmatched = 0
    renderPage()
    expect(await screen.findByText('Nessun movimento da abbinare.')).toBeTruthy()
  })

  it('a new account with no statement reads its opening balance and asks for the first one', async () => {
    world.statements = []
    world.movements = []
    world.unmatched = 0
    world.accounts = [{ ...ACCOUNT, balance: '10000.00', balance_as_of: '2026-01-01', is_stale: false, stale_days: null, unmatched_count: 0 }]
    renderPage()
    expect(await screen.findByText('Carica il primo estratto conto per vedere i movimenti.')).toBeTruthy()
    expect(screen.getByText('al 01/01/2026')).toBeTruthy()
    expect(screen.getAllByRole('button', { name: 'Carica estratto conto' }).length).toBeGreaterThan(1)
  })

  it('says «Leggo il conto…» while the account loads', () => {
    vi.mocked(api.GET).mockReturnValue(new Promise(() => {}) as never)
    renderPage()
    expect(screen.getByRole('status', { name: 'Leggo il conto…' })).toBeTruthy()
  })

  it('shows the error banner alone when the account cannot be read', async () => {
    vi.mocked(api.GET).mockRejectedValue({
      type: 'about:blank',
      title: 'Errore',
      status: 500,
      detail: 'Il server non risponde.',
      code: 'unknown',
    })
    renderPage()
    expect(await screen.findByRole('alert')).toHaveProperty('textContent', expect.stringContaining('Il server non risponde.'))
    expect(screen.queryByText('Movimenti')).toBeNull()
    expect(screen.queryByText('Saldo')).toBeNull()
  })

  it('gives a collaborator one sentence and asks for nothing', () => {
    session.role = 'collaborator'
    renderPage()
    expect(screen.getByRole('note').textContent).toMatch(/riservati all’«amministratore»/)
    expect(api.GET).not.toHaveBeenCalled()
  })

  it('an archived account says so and offers no upload', async () => {
    world.accounts = [{ ...ACCOUNT, archived_at: '2026-10-01T00:00:00Z' }]
    renderPage()
    expect(await screen.findByText('Questo conto è archiviato: i movimenti restano.')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Carica estratto conto' })).toBeNull()
  })

  describe('the statements tab', () => {
    async function openStatements() {
      const user = userEvent.setup()
      renderPage()
      await screen.findByRole('table')
      await user.click(screen.getByRole('tab', { name: 'Estratti conto' }))
      return user
    }

    it('names each statement, its state, the check the server ran and the months that are missing', async () => {
      await openStatements()
      const table = await screen.findByText('Importato').then((cell) => cell.closest('table') as HTMLElement)
      expect(within(table).getByText('01/09/2026 – 30/09/2026')).toBeTruthy()
      expect(within(table).getAllByText('CAMT.053 (XML)')).toHaveLength(2)
      const imported = within(table).getByText('Importato')
      expect(imported.closest('[data-tone]')?.getAttribute('data-tone')).toBe('ink')
      const reverted = within(table).getByText('Annullato')
      expect(reverted.closest('[data-tone]')?.getAttribute('data-tone')).toBe('muted')
      expect(within(table).getByText(/Il saldo dichiarato non torna: differenza 50,00/)).toBeTruthy()
      expect(within(table).getByText('Ottobre 2026')).toBeTruthy()
      const missing = within(table).getByText('Manca')
      expect(missing.closest('[data-tone]')?.getAttribute('data-tone')).toBe('danger')
      expect(within(table).getByRole('button', { name: 'Carica' })).toBeTruthy()
      expect(within(table).getByText('Rimuove i suoi movimenti e gli abbinamenti automatici')).toBeTruthy()
    })

    it('a statement row filters the movements to that statement', async () => {
      const user = await openStatements()
      await user.click(await screen.findByRole('button', { name: 'Vedi i movimenti' }))
      await waitFor(() => {
        expect(api.GET).toHaveBeenCalledWith(
          '/api/bank-accounts/{account_id}/movements',
          expect.objectContaining({
            params: expect.objectContaining({ query: expect.objectContaining({ statement_id: 'st-1', state: 'all' }) }),
          }),
        )
      })
      expect(await screen.findByText('PRELIEVO')).toBeTruthy()
    })

    it('reverts an import after a second look', async () => {
      vi.mocked(api.POST).mockResolvedValue({ data: { ...IMPORTED, state: 'reverted' }, response: new Response() } as never)
      const user = await openStatements()
      await user.click(await screen.findByRole('button', { name: 'Annulla l’importazione' }))
      const dialog = await screen.findByRole('dialog')
      expect(within(dialog).getByText(/Rimuove i suoi movimenti e gli abbinamenti automatici/)).toBeTruthy()
      await user.click(within(dialog).getByRole('button', { name: 'Annulla l’importazione' }))
      await waitFor(() => {
        expect(api.POST).toHaveBeenCalledWith(
          '/api/bank-statements/{statement_id}/revert',
          expect.objectContaining({ params: { path: { statement_id: 'st-1' } } }),
        )
      })
    })

    it('says a blocked revert in the server\'s own sentence', async () => {
      vi.mocked(api.POST).mockResolvedValue({
        error: {
          type: 'about:blank',
          title: 'Conflitto',
          status: 409,
          detail: 'Un movimento è già abbinato.',
          code: 'conflict',
        },
        response: new Response(null, { status: 409 }),
      } as never)
      const user = await openStatements()
      await user.click(await screen.findByRole('button', { name: 'Annulla l’importazione' }))
      const dialog = await screen.findByRole('dialog')
      await user.click(within(dialog).getByRole('button', { name: 'Annulla l’importazione' }))
      expect(await within(dialog).findByText('Un movimento è già abbinato.')).toBeTruthy()
    })
  })

  describe('on a phone', () => {
    beforeEach(() => setViewport(false))

    it('draws one column: the balance, the strip, a segmented control, movements as cards and a sticky upload', async () => {
      renderPage()
      expect(await screen.findByText('BONIFICO A VOSTRO FAVORE FT 2026/35')).toBeTruthy()
      expect(screen.queryByRole('table')).toBeNull()
      expect(screen.getByRole('button', { name: 'Da abbinare', pressed: true })).toBeTruthy()
      expect(screen.getByRole('button', { name: 'Tutti', pressed: false })).toBeTruthy()
      expect(screen.getByRole('button', { name: 'Estratti', pressed: false })).toBeTruthy()
      expect(screen.getAllByRole('button', { name: 'Carica estratto conto' })).toHaveLength(1)
      expect(screen.getByText('Manca: Ottobre 2026')).toBeTruthy()
    })

    it('«Estratti» lists the statements and the missing month as cards', async () => {
      const user = userEvent.setup()
      renderPage()
      await screen.findByText('BONIFICO A VOSTRO FAVORE FT 2026/35')
      await user.click(screen.getByRole('button', { name: 'Estratti' }))
      expect(await screen.findByText('Ottobre 2026')).toBeTruthy()
      expect(screen.getByText('Importato')).toBeTruthy()
      expect(screen.queryByRole('table')).toBeNull()
    })
  })
})
