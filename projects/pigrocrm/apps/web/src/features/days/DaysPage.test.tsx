import { screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { DaysPage } from './DaysPage'
import { CONTRACT_ID, contract, ok, row, renderWithQuery } from './testing'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn() } }
})
vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, params, children }: { to: string; params?: Record<string, string>; children: React.ReactNode }) => (
    <a href={params ? to.replace(/\$(\w+)/g, (_, key: string) => params[key] ?? '') : to}>{children}</a>
  ),
}))
vi.mock('@/lib/auth', () => ({ useCanWrite: () => true }))

function serving(items: unknown[], troncato = false) {
  vi.mocked(api.GET).mockImplementation(((path: string) => {
    if (path === '/api/contracts') return Promise.resolve(ok({ items: [contract()], next_cursor: null }))
    if (path === '/api/work-units') return Promise.resolve(ok({ items, troncato }))
    return Promise.resolve(ok(undefined))
  }) as never)
}

beforeEach(() => vi.mocked(api.GET).mockReset())

/** The query the register sent to `GET /api/work-units`. */
function listQuery(): Record<string, string | undefined> {
  const calls = vi.mocked(api.GET).mock.calls as unknown as [string, { params: { query: Record<string, string | undefined> } }][]
  const call = calls.find(([path]) => path === '/api/work-units')
  if (call === undefined) throw new Error('the register never asked for the days')
  return call[1].params.query
}

describe('the days register', () => {
  it('lists a contract’s days with their state, each a link to its page', async () => {
    serving([
      row({ id: 'd1', data: '2026-03-03', stato: 'lavorato_senza_approvazione', descrizione: 'Workshop' }),
      row({ id: 'd2', data: '2026-03-02', stato: 'contestato', quantita: '1.00', descrizione: 'Revisione' }),
    ])
    renderWithQuery(<DaysPage search={{ contract: CONTRACT_ID }} onSearchChange={vi.fn()} />)

    expect(await screen.findByRole('link', { name: /Workshop/ }, { timeout: 5000 })).toHaveAttribute('href', '/app/days/d1')
    expect(screen.getByText('Lavorata senza approvazione')).toBeInTheDocument()
    expect(screen.getByText('Contestata')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Registra giornata/ })).toHaveAttribute('href', '/app/days/new')
    // The contract narrows the list and no month was asked for: the whole ledger of that
    // contract is requested, with no window.
    expect(listQuery()).toMatchObject({ contract_id: CONTRACT_ID, da: undefined, a: undefined })
  })

  it('opens on the current month of every contract when nothing is chosen', async () => {
    serving([])
    renderWithQuery(<DaysPage search={{}} onSearchChange={vi.fn()} />)
    expect(await screen.findByText('Nessuna giornata')).toBeInTheDocument()
    expect(listQuery().contract_id).toBeUndefined()
    expect(listQuery().da).toMatch(/^\d{4}-\d{2}-01$/)
  })

  it('says when the API cut the list', async () => {
    serving([row()], true)
    renderWithQuery(<DaysPage search={{ contract: CONTRACT_ID }} onSearchChange={vi.fn()} />)
    expect(await screen.findByText(/Ci sono più giornate di quelle mostrate/)).toBeInTheDocument()
  })
})
