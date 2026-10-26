import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { ContractDetail } from './$contractId'

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return {
    ...actual,
    useParams: () => ({ contractId: 'c1' }),
    useNavigate: () => vi.fn(),
    Link: ({ children }: { children: React.ReactNode }) => <a href="/cliente">{children}</a>,
  }
})

const auth = vi.hoisted(() => ({ canWrite: true }))
vi.mock('@/lib/auth', () => ({ useCan: () => auth.canWrite }))

const mockGet = vi.spyOn(api, 'GET')

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) }) as never
}
function failed(error: unknown, status: number) {
  return Promise.resolve({ error, response: new Response(null, { status }) }) as never
}

const CONTRACT = {
  id: 'c1',
  customer_id: 'cust-1',
  titolo: 'Sviluppo piattaforma',
  inizio: '2026-01-01',
  fine: '2026-12-31',
  tipo_rinnovo: 'tacito',
  preavviso_rinnovo_giorni: 60,
  preavviso_disdetta_giorni: 30,
  giorni_pagamento: 30,
  pagamento_fine_mese: true,
  cadenza_fatturazione: 'mensile',
  divisa: 'EUR',
  requires_prior_approval: true,
  applies_social_charge: false,
  politica_spese: { kind: 'rimborsabile_con_tetto', importo_tetto: '500.00' },
  stato: 'bozza',
  note: null,
  custom_fields: {},
}

function mockApi(contract: ReturnType<typeof ok> | ReturnType<typeof failed>) {
  mockGet.mockImplementation(((path: string) => {
    if (path === '/api/contracts/{contract_id}') return contract
    if (path === '/api/customers/{customer_id}') return ok({ id: 'cust-1', ragione_sociale: 'ACME Srl' })
    if (path === '/api/schema/{entity_type}') {
      return ok({ entity_type: 'contract', native_fields: [], custom_fields: [] })
    }
    if (path.endsWith('/renewal-assumption')) {
      return failed({ status: 404, code: 'not_found', entity: 'renewal_assumption', detail: 'x' }, 404)
    }
    if (path.endsWith('/projection')) {
      return ok({
        finestra_irrevocabilita_fino_al: '2026-11-25',
        programmato: '0.00',
        da_rinnovo: '0.00',
        totale: '0.00',
      })
    }
    if (path.endsWith('/concentration')) {
      return ok({
        periodo_da: '2026-01-01',
        periodo_a: '2026-12-31',
        ricavi_cliente: '0.00',
        ricavi_totali: '0.00',
        quota: 0,
      })
    }
    return ok([])
  }) as never)
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ContractDetail />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  mockGet.mockReset()
  auth.canWrite = true
})

describe('ContractDetail', () => {
  it('states the terms and the billing rules', async () => {
    mockApi(ok(CONTRACT))
    renderPage()

    expect(await screen.findByRole('heading', { name: 'Sviluppo piattaforma' })).toBeInTheDocument()
    expect(screen.getByText('Bozza')).toBeInTheDocument()
    expect(screen.getByText('Rinnovo tacito')).toBeInTheDocument()
    expect(screen.getByText('60 giorni')).toBeInTheDocument()
    expect(screen.getByText('30 giorni fine mese')).toBeInTheDocument()
    expect(screen.getByText('Rimborsate fino a un tetto, tetto 500,00 €')).toBeInTheDocument()
  })

  it('offers Attiva, Stato and Modifica to a writer', async () => {
    mockApi(ok(CONTRACT))
    renderPage()
    expect(await screen.findByRole('button', { name: 'Attiva' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Stato' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Modifica' })).toBeInTheDocument()
  })

  it('offers no write control to someone who cannot write', async () => {
    auth.canWrite = false
    mockApi(ok(CONTRACT))
    renderPage()
    await screen.findByRole('heading', { name: 'Sviluppo piattaforma' })
    expect(screen.queryByRole('button', { name: 'Attiva' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Modifica' })).not.toBeInTheDocument()
  })

  it('says the contract does not exist on a 404 and does not blame the network', async () => {
    mockApi(failed({ status: 404, code: 'not_found', detail: 'contract x not found' }, 404))
    renderPage()
    expect(await screen.findByText('Contratto non trovato.')).toBeInTheDocument()
  })
})
