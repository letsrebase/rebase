/**
 * The matching panel of the account page (REB-1042, brief «The matching panel»): the proposal line
 * of a row, the panel with the best proposal checked, «Abbina» held back until the server's
 * preview says it can be confirmed, the partial step, the undo, the search and the phone sheet.
 * Every figure is the server's string; nothing here sums.
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

type Movement = components['schemas']['MovementRead']
type Item = components['schemas']['MatchItemRead']
type Match = components['schemas']['MovementMatchRead']
type Detail = components['schemas']['MovementDetail']
type Preview = components['schemas']['MatchPreview']

function item(id: string, number: string, amount: string, over: Partial<Item> = {}): Item {
  return {
    due_item_id: id,
    direction: 'out',
    source_kind: 'purchase',
    source_id: null,
    organization_id: 'org-1',
    counterparty_name: 'Beta Forniture spa',
    description: null,
    document_number: number,
    due_date: '2026-09-30',
    amount,
    remaining: amount,
    allocated: amount,
    adopts_payment: false,
    credit: false,
    ...over,
  }
}

const A = item('di-1', '2026/35', '1464.00')
const B = item('di-2', '2026/36', '2196.00')
const C = item('di-3', '2026/40', '3660.00')

function proposal(items: Item[], over: Partial<Match> = {}): Match {
  return {
    id: 'mt-1',
    state: 'proposed',
    reasons: ['document_number', 'known_iban'],
    note: null,
    proposed_by_type: 'system',
    created_at: '2026-11-01T00:00:00Z',
    confirmed_at: null,
    confirmed_by_type: null,
    closed_at: null,
    closed_by_type: null,
    close_reason: null,
    is_current: true,
    items,
    ...over,
  }
}

function movement(over: Partial<Movement> = {}): Movement {
  return {
    id: 'mv-1',
    bank_account_id: 'acc-1',
    booking_date: '2026-09-28',
    value_date: null,
    amount: '-3660.00',
    currency: 'EUR',
    counterparty_name: 'Beta Forniture spa',
    counterparty_iban: 'IT60X0542811101000000654321',
    description: 'BONIFICO A VOSTRO FAVORE FT 2026/35 E 2026/36',
    bank_reference: null,
    end_to_end_id: 'E2E-778',
    state: 'unmatched',
    ignored_reason: null,
    unmatched_days: 3,
    is_overdue_unmatched: false,
    statement_ids: ['st-1'],
    match: null,
    ...over,
  }
}

const ACCOUNT: components['schemas']['BankAccountRead'] = {
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
  senders: [],
  archived_at: null,
  balance: '18420.50',
  balance_as_of: '2026-09-30',
  stale_days: 1,
  is_stale: false,
  unmatched_count: 1,
  below_zero: false,
  created_at: '2026-01-01T00:00:00Z',
}

const world = {
  movements: [] as Movement[],
  detail: null as Detail | null,
  detailError: false,
  openItems: [] as Item[],
  preview: (ids: string[]): Preview => ({
    items: ids.map((id) => [A, B, C].find((entry) => entry.due_item_id === id)!),
    reasons: [],
    allocated: '3660.00',
    difference: '0.00',
    partial: false,
    can_confirm: true,
  }),
}

function page<T>(items: T[]) {
  return { items, next_cursor: null, total_count: items.length }
}

function install() {
  vi.mocked(api.GET).mockImplementation((async (path: string) => {
    if (path === '/api/bank-accounts') return { data: [ACCOUNT], response: new Response() }
    if (path === '/api/bank-accounts/{account_id}/coverage')
      return { data: { grace_days: 7, months: [] }, response: new Response() }
    if (path === '/api/bank-accounts/{account_id}/statements') return { data: page([]), response: new Response() }
    if (path === '/api/bank-accounts/{account_id}/movements')
      return { data: { ...page(world.movements), unmatched_count: 1 }, response: new Response() }
    if (path === '/api/bank-movements/{movement_id}') {
      if (world.detailError)
        throw { type: 'about:blank', title: 'Errore', status: 500, detail: 'Il server non risponde.', code: 'unknown' }
      return { data: world.detail, response: new Response() }
    }
    if (path === '/api/bank-movements/{movement_id}/open-items') return { data: world.openItems, response: new Response() }
    throw new Error(`unexpected GET ${path}`)
  }) as never)
  vi.mocked(api.POST).mockImplementation((async (path: string, init?: { body?: { due_item_ids?: string[] } }) => {
    if (path === '/api/bank-movements/{movement_id}/match-preview')
      return { data: world.preview(init?.body?.due_item_ids ?? []), response: new Response() }
    throw new Error(`unexpected POST ${path}`)
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

/** The one-proposal world: the movement carries the importer's proposal of two items. */
function proposed() {
  const live = proposal([A, B])
  world.movements = [movement({ match: live })]
  world.detail = { movement: movement({ match: live }), candidates: [], history: [] }
}

function previews() {
  return vi.mocked(api.POST).mock.calls.filter((call) => call[0] === '/api/bank-movements/{movement_id}/match-preview')
}

describe('matching a movement', () => {
  beforeEach(() => {
    vi.mocked(api.GET).mockReset()
    vi.mocked(api.POST).mockReset()
    session.role = 'admin'
    setViewport(true)
    world.detailError = false
    world.openItems = []
    world.preview = (ids) => ({
      items: ids.map((id) => [A, B, C].find((entry) => entry.due_item_id === id)!),
      reasons: [],
      allocated: '3660.00',
      difference: '0.00',
      partial: false,
      can_confirm: true,
    })
    proposed()
    install()
  })

  it('says what is proposed under the description, and what the assistant proposed', async () => {
    renderPage()
    expect(await screen.findByText('proposta: Fattura 2026/35 e un’altra')).toBeTruthy()
  })

  it('names the assistant when it made the proposal and hides a stale one', async () => {
    world.movements = [movement({ match: proposal([A], { proposed_by_type: 'mcp' }) })]
    const view = renderPage()
    expect(await screen.findByText('proposta dall’assistente: Fattura 2026/35')).toBeTruthy()
    view.unmount()
    world.movements = [movement({ match: proposal([A], { is_current: false }) })]
    renderPage()
    await screen.findByText('BONIFICO A VOSTRO FAVORE FT 2026/35 E 2026/36')
    expect(screen.queryByText(/proposta:/)).toBeNull()
  })

  it('names the item by its description when it has no document number', async () => {
    const bare = item('di-9', '', '50.00', { document_number: null, description: 'Canone hosting' })
    world.movements = [movement({ match: proposal([bare, A, B]) })]
    renderPage()
    expect(await screen.findByText('proposta: Canone hosting e altre 2')).toBeTruthy()
  })

  it('opens the panel with the best proposal checked, its reasons and the server\'s sum', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Abbina' }))
    const panel = await screen.findByRole('complementary', { name: 'Abbinamento del movimento' })
    expect(await within(panel).findByText('La proposta migliore: 2 voci')).toBeTruthy()
    expect(within(panel).getByText('ID end-to-end: E2E-778')).toBeTruthy()
    expect(within(panel).getByRole('checkbox', { name: 'Scegli Fattura 2026/35' }).getAttribute('aria-checked')).toBe('true')
    expect(within(panel).getByRole('checkbox', { name: 'Scegli Fattura 2026/36' }).getAttribute('aria-checked')).toBe('true')
    expect(within(panel).getByText('numeri nella causale')).toBeTruthy()
    expect(within(panel).getByText('IBAN noto')).toBeTruthy()
    expect(await within(panel).findByText(/Selezionato 3\.660,00 € di 3\.660,00 €: nessuna differenza/)).toBeTruthy()
    expect(previews()[0]![1]).toEqual(
      expect.objectContaining({ body: { due_item_ids: ['di-1', 'di-2'] } }),
    )
    await user.click(within(panel).getByRole('button', { name: 'Chiudi' }))
    expect(screen.queryByRole('complementary', { name: 'Abbinamento del movimento' })).toBeNull()
  })

  it('keeps «Abbina» disabled while the server says the selection cannot be confirmed', async () => {
    world.preview = (ids) => ({
      items: ids.map((id) => [A, B, C].find((entry) => entry.due_item_id === id)!),
      reasons: [],
      allocated: '1464.00',
      difference: '2196.00',
      partial: false,
      can_confirm: false,
    })
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Abbina' }))
    const panel = await screen.findByRole('complementary', { name: 'Abbinamento del movimento' })
    expect(await within(panel).findByText(/differenza 2\.196,00 €/)).toBeTruthy()
    expect((within(panel).getByRole('button', { name: 'Abbina' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('confirms the selection and shows the matched state with the undo', async () => {
    const confirmed = movement({ state: 'matched', match: proposal([A, B], { state: 'confirmed', confirmed_at: '2026-11-10T08:00:00Z' }) })
    vi.mocked(api.POST).mockImplementation((async (path: string, init?: { body?: { due_item_ids?: string[] } }) => {
      if (path === '/api/bank-movements/{movement_id}/match-preview')
        return { data: world.preview(init?.body?.due_item_ids ?? []), response: new Response() }
      world.detail = { movement: confirmed, candidates: [], history: [] }
      return { data: world.detail, response: new Response() }
    }) as never)
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Abbina' }))
    const panel = await screen.findByRole('complementary', { name: 'Abbinamento del movimento' })
    await within(panel).findByText(/nessuna differenza/)
    await user.click(within(panel).getByRole('button', { name: 'Abbina' }))
    await waitFor(() => {
      expect(api.POST).toHaveBeenCalledWith(
        '/api/bank-movements/{movement_id}/match',
        expect.objectContaining({ body: { due_item_ids: ['di-1', 'di-2'], accept_partial: false } }),
      )
    })
    expect(await within(panel).findByText('Ha saldato 2 voci')).toBeTruthy()
    expect(within(panel).getByRole('button', { name: 'Annulla l’abbinamento' })).toBeTruthy()
  })

  it('asks for a second, deliberate step before settling an item only in part', async () => {
    world.preview = (ids) => ({
      items: ids.map((id) => ({ ...[A, B, C].find((entry) => entry.due_item_id === id)!, allocated: '1000.00' })),
      reasons: [],
      allocated: '3660.00',
      difference: '0.00',
      partial: true,
      can_confirm: true,
    })
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Abbina' }))
    const panel = await screen.findByRole('complementary', { name: 'Abbinamento del movimento' })
    expect(await within(panel).findByText(/Salda solo una parte di una voce/)).toBeTruthy()
    await user.click(within(panel).getByRole('button', { name: 'Abbina' }))
    expect(api.POST).not.toHaveBeenCalledWith('/api/bank-movements/{movement_id}/match', expect.anything())
    await user.click(within(panel).getByRole('button', { name: 'Conferma il pagamento parziale' }))
    await waitFor(() => {
      expect(api.POST).toHaveBeenCalledWith(
        '/api/bank-movements/{movement_id}/match',
        expect.objectContaining({ body: { due_item_ids: ['di-1', 'di-2'], accept_partial: true } }),
      )
    })
  })

  it('says a refused confirmation in the server\'s sentence', async () => {
    vi.mocked(api.POST).mockImplementation((async (path: string, init?: { body?: { due_item_ids?: string[] } }) => {
      if (path === '/api/bank-movements/{movement_id}/match-preview')
        return { data: world.preview(init?.body?.due_item_ids ?? []), response: new Response() }
      return {
        error: { type: 'about:blank', title: 'Errore', status: 422, detail: 'Il mese è chiuso.', code: 'unprocessable' },
        response: new Response(null, { status: 422 }),
      }
    }) as never)
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Abbina' }))
    const panel = await screen.findByRole('complementary', { name: 'Abbinamento del movimento' })
    await within(panel).findByText(/nessuna differenza/)
    await user.click(within(panel).getByRole('button', { name: 'Abbina' }))
    expect(await within(panel).findByText('Il mese è chiuso.')).toBeTruthy()
  })

  it('offers the other candidates unchecked; picking one replaces the selection', async () => {
    const live = proposal([A, B])
    world.detail = {
      movement: movement({ match: live }),
      candidates: [{ items: [C], reasons: ['exact_amount'], allocated: '3660.00', difference: '0.00', partial: false }],
      history: [],
    }
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Abbina' }))
    const panel = await screen.findByRole('complementary', { name: 'Abbinamento del movimento' })
    const other = await within(panel).findByRole('checkbox', { name: 'Scegli Fattura 2026/40' })
    expect(other.getAttribute('aria-checked')).toBe('false')
    expect(within(panel).getByText('importo totale coincide')).toBeTruthy()
    await user.click(other)
    expect(other.getAttribute('aria-checked')).toBe('true')
    expect(within(panel).getByRole('checkbox', { name: 'Scegli Fattura 2026/35' }).getAttribute('aria-checked')).toBe('false')
    await waitFor(() => expect(previews().at(-1)![1]).toEqual(expect.objectContaining({ body: { due_item_ids: ['di-3'] } })))
  })

  it('searches the open items and previews the changed selection', async () => {
    world.movements = [movement()]
    world.detail = { movement: movement(), candidates: [], history: [] }
    world.openItems = [C]
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Abbina' }))
    const panel = await screen.findByRole('complementary', { name: 'Abbinamento del movimento' })
    expect(await within(panel).findByText('Nessuna voce aperta corrisponde.')).toBeTruthy()
    expect((within(panel).getByRole('button', { name: 'Abbina' }) as HTMLButtonElement).disabled).toBe(true)
    await user.click(within(panel).getByRole('button', { name: 'Cerca un’altra voce' }))
    await user.click(await within(panel).findByRole('checkbox', { name: 'Scegli Fattura 2026/40' }))
    await waitFor(() => expect(previews().at(-1)![1]).toEqual(expect.objectContaining({ body: { due_item_ids: ['di-3'] } })))
    expect(await within(panel).findByText(/nessuna differenza/)).toBeTruthy()
    expect(within(panel).getByRole('button', { name: 'Abbina' }).hasAttribute('disabled')).toBe(false)
  })

  it('draws the loading sentence in place of the proposals and the banner alone on an error', async () => {
    const user = userEvent.setup()
    vi.mocked(api.GET).mockImplementation((async (path: string) => {
      if (path === '/api/bank-movements/{movement_id}') return new Promise(() => {})
      if (path === '/api/bank-accounts') return { data: [ACCOUNT], response: new Response() }
      if (path === '/api/bank-accounts/{account_id}/coverage')
        return { data: { grace_days: 7, months: [] }, response: new Response() }
      if (path === '/api/bank-accounts/{account_id}/statements') return { data: page([]), response: new Response() }
      return { data: { ...page(world.movements), unmatched_count: 1 }, response: new Response() }
    }) as never)
    const view = renderPage()
    await user.click(await screen.findByRole('button', { name: 'Abbina' }))
    expect(await screen.findByRole('status', { name: 'Cerco le voci aperte…' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Cerca un’altra voce' })).toBeNull()
    view.unmount()

    world.detailError = true
    install()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Abbina' }))
    expect(await screen.findByText(/Il server non risponde\./)).toBeTruthy()
    const panel = screen.getByRole('complementary', { name: 'Abbinamento del movimento' })
    expect(within(panel).queryByRole('button', { name: 'Abbina' })).toBeNull()
  })

  it('a matched row shows its documents and undoes after a confirmation with a reason', async () => {
    const confirmed = movement({ state: 'matched', match: proposal([A], { state: 'confirmed' }) })
    world.movements = [confirmed]
    vi.mocked(api.POST).mockResolvedValue({
      data: { movement: movement(), candidates: [], history: [] },
      response: new Response(),
    } as never)
    const user = userEvent.setup()
    renderPage()
    expect(await screen.findByText('abbinato a Fattura 2026/35')).toBeTruthy()
    expect(screen.getByText('Abbinato')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Annulla l’abbinamento' }))
    const dialog = await screen.findByRole('dialog')
    expect(api.POST).not.toHaveBeenCalled()
    await user.type(within(dialog).getByLabelText('Motivo (facoltativo)'), 'sbagliata')
    await user.click(within(dialog).getByRole('button', { name: 'Annulla l’abbinamento' }))
    await waitFor(() => {
      expect(api.POST).toHaveBeenCalledWith(
        '/api/bank-movements/{movement_id}/unmatch',
        expect.objectContaining({ body: { reason: 'sbagliata' } }),
      )
    })
  })

  it('shows no matching button to a role that may not confirm', async () => {
    session.role = 'collaborator'
    renderPage()
    expect(screen.queryByRole('button', { name: 'Abbina' })).toBeNull()
  })

  describe('on a phone', () => {
    beforeEach(() => setViewport(false))

    it('opens a sheet with the same blocks and a bar holding «Abbina» and «Scegli altre voci»', async () => {
      const user = userEvent.setup()
      renderPage()
      await user.click(await screen.findByRole('button', { name: 'Abbina' }))
      const sheet = await screen.findByRole('dialog', { name: 'Abbinamento del movimento' })
      expect(await within(sheet).findByText('La proposta migliore: 2 voci')).toBeTruthy()
      expect(await within(sheet).findByText(/nessuna differenza/)).toBeTruthy()
      expect(within(sheet).getByRole('button', { name: 'Scegli altre voci' })).toBeTruthy()
      expect((within(sheet).getByRole('button', { name: 'Abbina' }) as HTMLButtonElement).disabled).toBe(false)
      await user.click(within(sheet).getByRole('button', { name: 'Scegli altre voci' }))
      expect(await within(sheet).findByLabelText('Cerca tra le voci aperte')).toBeTruthy()
    })
  })
})
