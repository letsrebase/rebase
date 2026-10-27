import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { RenewalSection } from './RenewalSection'
import type { Contract } from './queries'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PUT: vi.fn(), PATCH: vi.fn() } }
})
vi.mock('@rebase/ui/sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))
vi.mock('@/lib/auth', () => ({ useCan: () => true }))

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) }) as never
}
function failed(error: unknown, status: number) {
  return Promise.resolve({ error, response: new Response(null, { status }) }) as never
}

const CONTRACT = {
  id: 'c1',
  inizio: '2026-01-01',
  fine: '2026-12-31',
  divisa: 'EUR',
} as Contract

const ASSUMPTION = {
  id: 'a1',
  contract_id: 'c1',
  probabilita: 70,
  volume_atteso: '12000.00',
  orizzonte_al: '2027-12-31',
  updated_at: '2026-10-26T10:00:00Z',
}

const NO_ASSUMPTION = {
  type: 'about:blank',
  title: 'Not Found',
  status: 404,
  code: 'not_found',
  entity: 'renewal_assumption',
  detail: 'renewal_assumption c1 not found',
}

function projection(da_rinnovo: string, totale: string) {
  return {
    contract_id: 'c1',
    come_di: '2026-10-26',
    da: '2026-10-26',
    a: '2027-01-01',
    finestra_irrevocabilita_fino_al: '2026-11-25',
    programmato: '1500.00',
    da_rinnovo,
    totale,
  }
}

const CONCENTRATION = {
  contract_id: 'c1',
  customer_id: 'cust-1',
  periodo_da: '2026-01-01',
  periodo_a: '2026-12-31',
  valute: [{ divisa: 'EUR', ricavi_cliente: '3000.00', ricavi_totali: '12000.00', quota: 0.25 }],
}

function renderSection() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <RenewalSection contract={CONTRACT} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(2026, 9, 26))
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.PUT).mockReset()
})
afterEach(() => vi.useRealTimers())

describe('RenewalSection', () => {
  it('says nothing is recorded and shows the programmed revenue without a renewal share', async () => {
    vi.mocked(api.GET).mockImplementation(((path: string) => {
      if (path.endsWith('/renewal-assumption')) return failed(NO_ASSUMPTION, 404)
      if (path.endsWith('/projection')) return ok(projection('0.00', '1500.00'))
      return ok(CONCENTRATION)
    }) as never)
    renderSection()

    expect(await screen.findByText(/Nessuna assunzione registrata/)).toBeInTheDocument()
    expect(await screen.findByText('Oltre il 25/11/2026')).toBeInTheDocument()
    expect(screen.getAllByText('1.500,00 €').length).toBeGreaterThan(0)
    // The share is the API's number as a percentage, never recomputed from the two sums.
    expect(await screen.findByText('25%')).toBeInTheDocument()
  })

  it('shows the customer revenue in the currency of its invoices, not the contract\u2019s', async () => {
    vi.mocked(api.GET).mockImplementation(((path: string) => {
      if (path.endsWith('/renewal-assumption')) return ok(ASSUMPTION)
      if (path.endsWith('/projection')) return ok(projection('0.00', '1500.00'))
      return ok(CONCENTRATION)
    }) as never)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <RenewalSection contract={{ ...CONTRACT, divisa: 'USD' }} />
      </QueryClientProvider>,
    )

    expect(await screen.findByText('3.000,00 € su 12.000,00 € fatturati')).toBeInTheDocument()
    expect(screen.queryByText(/\$/)).not.toBeInTheDocument()
  })

  it('shows one figure per currency when the invoices are in two', async () => {
    vi.mocked(api.GET).mockImplementation(((path: string) => {
      if (path.endsWith('/renewal-assumption')) return ok(ASSUMPTION)
      if (path.endsWith('/projection')) return ok(projection('0.00', '1500.00'))
      return ok({
        ...CONCENTRATION,
        valute: [
          ...CONCENTRATION.valute,
          { divisa: 'USD', ricavi_cliente: '50.00', ricavi_totali: '50.00', quota: 1 },
        ],
      })
    }) as never)
    renderSection()

    expect(await screen.findByText(/\(EUR\)$/)).toBeInTheDocument()
    expect(screen.getByText(/\(USD\)$/)).toBeInTheDocument()
    expect(screen.getByText('3.000,00 € su 12.000,00 € fatturati')).toBeInTheDocument()
    expect(screen.getByText('50.00 USD su 50.00 USD fatturati')).toBeInTheDocument()
  })

  it('sets a 70% renewal and shows the revenue it programs', async () => {
    let saved = false
    vi.mocked(api.GET).mockImplementation(((path: string) => {
      if (path.endsWith('/renewal-assumption')) {
        return saved ? ok(ASSUMPTION) : failed(NO_ASSUMPTION, 404)
      }
      if (path.endsWith('/projection')) {
        return saved ? ok(projection('8400.00', '9900.00')) : ok(projection('0.00', '1500.00'))
      }
      return ok(CONCENTRATION)
    }) as never)
    vi.mocked(api.PUT).mockImplementation((() => {
      saved = true
      return ok(ASSUMPTION)
    }) as never)
    renderSection()

    await userEvent.type(await screen.findByLabelText('Probabilità di rinnovo (%)'), '70')
    await userEvent.type(screen.getByLabelText('Volume atteso (€)'), '12000,00')
    await userEvent.type(screen.getByLabelText('Orizzonte'), '2027-12-31')
    await userEvent.click(screen.getByRole('button', { name: 'Salva assunzione' }))

    expect(api.PUT).toHaveBeenCalledWith(
      '/api/contracts/{contract_id}/renewal-assumption',
      expect.objectContaining({
        params: { path: { contract_id: 'c1' } },
        body: { probabilita: 70, volume_atteso: '12000.00', orizzonte_al: '2027-12-31' },
      }),
    )
    expect(await screen.findByText('8.400,00 €')).toBeInTheDocument()
    expect(screen.getByText('9.900,00 €')).toBeInTheDocument()
  })

  it('says the year has not started rather than asking for a share it cannot give', async () => {
    vi.mocked(api.GET).mockImplementation(((path: string) => {
      if (path.endsWith('/renewal-assumption')) return ok(ASSUMPTION)
      return ok(projection('0.00', '0.00'))
    }) as never)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <RenewalSection contract={{ ...CONTRACT, inizio: '2027-02-01' }} />
      </QueryClientProvider>,
    )
    expect(await screen.findByText(/inizia il 01\/02\/2027/)).toBeInTheDocument()
    expect(api.GET).not.toHaveBeenCalledWith('/api/contracts/{contract_id}/concentration', expect.anything())
  })

  describe('the contract’s cap on the customer’s share', () => {
    function withCap(superata: boolean | null, soglia_da_contratto = true) {
      vi.mocked(api.GET).mockImplementation(((path: string) => {
        if (path.endsWith('/renewal-assumption')) return ok(ASSUMPTION)
        if (path.endsWith('/projection')) return ok(projection('0.00', '1500.00'))
        return ok({
          ...CONCENTRATION,
          soglia: 0.5,
          soglia_da_contratto,
          valute: [{ ...CONCENTRATION.valute[0], quota: superata ? 0.5 : 0.25, superata }],
        })
      }) as never)
    }

    it('shows the cap beside the share and says it is under it', async () => {
      withCap(false)
      renderSection()

      expect(await screen.findByText('Tetto 50%')).toBeInTheDocument()
      expect(screen.getByText('25%')).toBeInTheDocument()
      expect(screen.getByText('Sotto il tetto')).toBeInTheDocument()
      expect(screen.queryByText('Tetto raggiunto')).not.toBeInTheDocument()
    })

    it('says the cap is reached when the server says the share is over it', async () => {
      withCap(true)
      renderSection()

      expect(await screen.findByText('Tetto raggiunto')).toBeInTheDocument()
      expect(screen.getByText('Tetto 50%')).toBeInTheDocument()
      expect(screen.queryByText('Sotto il tetto')).not.toBeInTheDocument()
    })

    it('shows no cap and no pill when the threshold is not the contract’s own', async () => {
      withCap(false, false)
      renderSection()

      expect(await screen.findByText('25%')).toBeInTheDocument()
      expect(screen.queryByText(/^Tetto/)).not.toBeInTheDocument()
      expect(screen.queryByText('Sotto il tetto')).not.toBeInTheDocument()
    })
  })
})
