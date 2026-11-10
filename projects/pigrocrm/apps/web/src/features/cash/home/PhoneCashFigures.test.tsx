/**
 * The money figures of the phone Home (REB-1025, brief decision 6): one test per state of its
 * table. Every figure is the server's string; nothing here sums.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import type { CashSummary } from './queries'
import { PhoneCashFigures } from './PhoneCashFigures'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PUT: vi.fn(), DELETE: vi.fn() } }
})
vi.mock('@/lib/legalEntity', () => ({ useLegalEntity: () => legalEntity }))
vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, search, children, className }: { to: string; search?: Record<string, unknown>; children: React.ReactNode; className?: string }) => (
    <a href={search ? `${to}?${new URLSearchParams(Object.entries(search).map(([k, v]) => [k, String(v)]))}` : to} className={className}>
      {children}
    </a>
  ),
}))
const legalEntity = { selected: null as string | null }

function setPhone(phone: boolean) {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: phone ? false : true,
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
  })) as unknown as typeof window.matchMedia
}

const COMPANY: CashSummary = {
  calculated_at: '2026-11-10T09:00:00Z',
  today: '2026-11-10',
  cash_enabled: true,
  show_outgoing: true,
  balance_today: '18420.50',
  balance_as_of: '2026-11-09',
  receivable: { total: '123456.00', late_total: '3660.00', late_count: 3 },
  payable: { total: '9800.00', late_total: '1200.00', late_count: 1 },
  next_7_days: { in_total: '4100.00', out_total: '2500.00' },
  alert: {
    kind: 'below_zero',
    week_start: '2026-11-30',
    week_end: '2026-12-06',
  },
}

function renderFigures() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <PhoneCashFigures />
    </QueryClientProvider>,
  )
}

function answer(summary: CashSummary) {
  vi.mocked(api.GET).mockResolvedValue({ data: summary, response: new Response() } as never)
}

describe('PhoneCashFigures', () => {
  beforeEach(() => {
    vi.mocked(api.GET).mockReset()
    legalEntity.selected = null
    setPhone(true)
  })

  it('draws the band, the balance with its date, the paired cards and the next seven days for an admin', async () => {
    answer(COMPANY)
    renderFigures()
    expect(await screen.findByText('Saldo oggi')).toBeTruthy()
    expect(screen.getByText('Il saldo previsto scende sotto zero nella settimana dal 30/11 al 06/12/2026')).toBeTruthy()
    expect(screen.getByText(/al 09\/11\/2026/)).toBeTruthy()
    expect(screen.getByText('Da incassare')).toBeTruthy()
    expect(screen.getByText('Da pagare')).toBeTruthy()
    expect(screen.getByText(/123\.456,00/)).toBeTruthy()
    expect(screen.getByText('entrano')).toBeTruthy()
    expect(screen.getByText('escono')).toBeTruthy()
    expect(api.GET).toHaveBeenCalledWith('/api/cash/summary', { params: { query: {} } })
  })

  it('names the late part in words, receivables to the overdue invoices and payables to Cassa', async () => {
    answer(COMPANY)
    renderFigures()
    const receivable = await screen.findByText(/di cui scaduto 3\.660,00/)
    expect(receivable.closest('a')?.getAttribute('href')).toBe('/app/invoices?overdue=true')
    expect(screen.getByText(/di cui scaduto 1\.200,00/).closest('a')?.getAttribute('href')).toBe('/app/cash')
    expect(screen.getByText('Saldo oggi').closest('a')?.getAttribute('href')).toBe('/app/cash')
    expect(screen.getByText('Da pagare').closest('a')?.getAttribute('href')).toBe('/app/cash')
  })

  it('sends the legal entity when one is selected', async () => {
    legalEntity.selected = 'le-1'
    answer(COMPANY)
    renderFigures()
    await screen.findByText('Saldo oggi')
    expect(api.GET).toHaveBeenCalledWith('/api/cash/summary', { params: { query: { legal_entity_id: 'le-1' } } })
  })

  it('names a missing statement and a stale balance', async () => {
    answer({ ...COMPANY, alert: { kind: 'missing_statement', account_name: 'Conto operativo', period_from: '2026-10-01', period_to: '2026-10-31' } })
    renderFigures()
    expect(await screen.findByText('Manca l’estratto conto di ottobre per Conto operativo')).toBeTruthy()
  })

  it('says how long a balance has gone without news', async () => {
    answer({ ...COMPANY, alert: { kind: 'stale_balance', account_name: 'Conto USD', days: 41 } })
    renderFigures()
    expect(await screen.findByText('Il saldo di Conto USD non si aggiorna da 41 giorni')).toBeTruthy()
  })

  it('draws a solo space with entrano only and no payable', async () => {
    answer({
      ...COMPANY,
      show_outgoing: false,
      payable: null,
      alert: null,
      next_7_days: { in_total: '900.00', out_total: null },
    })
    renderFigures()
    expect(await screen.findByText('Saldo oggi')).toBeTruthy()
    expect(screen.getByText('Da incassare')).toBeTruthy()
    expect(screen.getByText(/Di cui scaduto 3\.660,00/)).toBeTruthy()
    expect(screen.getByText('entrano')).toBeTruthy()
    expect(screen.queryByText('Da pagare')).toBeNull()
    expect(screen.queryByText('escono')).toBeNull()
  })

  it('draws no balance and no band without an account or with the module off, the owed figures stay', async () => {
    answer({ ...COMPANY, cash_enabled: false, balance_today: null, balance_as_of: null, alert: null })
    renderFigures()
    expect(await screen.findByText('Da incassare')).toBeTruthy()
    expect(screen.getByText('Da pagare')).toBeTruthy()
    expect(screen.queryByText('Saldo oggi')).toBeNull()
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('draws no balance for a collaborator, whom the server answers without one', async () => {
    answer({ ...COMPANY, balance_today: null, balance_as_of: null, alert: null })
    renderFigures()
    expect(await screen.findByText('Da incassare')).toBeTruthy()
    expect(screen.queryByText('Saldo oggi')).toBeNull()
  })

  it('says nothing is late instead of a zero', async () => {
    answer({ ...COMPANY, receivable: { total: '500.00', late_total: '0.00', late_count: 0 } })
    renderFigures()
    await screen.findByText('Da incassare')
    expect(screen.getAllByText('Niente di scaduto')).toHaveLength(1)
  })

  it('waits with the loading state', () => {
    vi.mocked(api.GET).mockReturnValue(new Promise(() => {}) as never)
    renderFigures()
    expect(within(screen.getByRole('status')).getByText('Leggo le cifre della cassa…')).toBeTruthy()
    expect(screen.queryByText('Da incassare')).toBeNull()
  })

  it('shows the error banner alone when the read fails', async () => {
    vi.mocked(api.GET).mockResolvedValue({ error: { detail: 'Non riesco' }, response: new Response(null, { status: 500 }) } as never)
    renderFigures()
    await waitFor(() => expect(screen.getByRole('alert')).toBeTruthy())
    expect(screen.queryByText('Da incassare')).toBeNull()
  })

  it('draws nothing from 640 px and asks for nothing', () => {
    setPhone(false)
    renderFigures()
    expect(screen.queryByLabelText('Cassa oggi')).toBeNull()
    expect(api.GET).not.toHaveBeenCalled()
  })
})

describe('PhoneCashFigures, a negative balance', () => {
  it('draws it in the danger text with its sign', async () => {
    setPhone(true)
    legalEntity.selected = null
    answer({ ...COMPANY, balance_today: '-120.00' })
    renderFigures()
    const figure = await screen.findByText(/-120,00/)
    expect(figure.className).toContain('text-destructive')
  })
})
