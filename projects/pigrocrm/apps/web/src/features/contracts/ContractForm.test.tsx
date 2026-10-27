import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { ContractForm } from './ContractForm'
import type { Contract } from './queries'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PATCH: vi.fn() } }
})
vi.mock('@rebase/ui/sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) }) as never
}
function failed(error: unknown, status: number) {
  return Promise.resolve({ error, response: new Response(null, { status }) }) as never
}

const CONTRACT = {
  id: 'c1',
  customer_id: 'cust-1',
  azienda_id: 'a1',
  titolo: 'Sviluppo piattaforma',
  inizio: '2026-01-01',
  fine: '2026-12-31',
  tipo_rinnovo: 'nessuno',
  preavviso_rinnovo_giorni: null,
  preavviso_disdetta_giorni: 30,
  giorni_pagamento: null,
  pagamento_fine_mese: null,
  cadenza_fatturazione: 'mensile',
  divisa: 'EUR',
  requires_prior_approval: false,
  applies_social_charge: false,
  tetto_quota_cliente: '50.00',
  politica_spese: { kind: 'non_rimborsabile' },
  stato: 'attivo',
  contratto_precedente_id: null,
  note: null,
  custom_fields: {},
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
} as Contract

function renderForm(props: Partial<React.ComponentProps<typeof ContractForm>> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ContractForm open onOpenChange={vi.fn()} {...props} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  vi.mocked(api.PATCH).mockReset()
  vi.mocked(api.GET).mockImplementation((() =>
    ok({ entity_type: 'contract', native_fields: [], custom_fields: [] })) as never)
})

describe('ContractForm, the cap on the customer’s share', () => {
  it('says what the cap is for, and is blank on a new contract', async () => {
    renderForm({ customerId: 'cust-1' })

    const box = await screen.findByLabelText('Tetto sulla quota del cliente (%)')
    expect(box).toHaveValue('')
    expect(
      screen.getByText(/La quota massima del cliente sui ricavi fatturati dell’azienda/),
    ).toBeInTheDocument()
  })

  it('creates a contract with the typed cap', async () => {
    vi.mocked(api.POST).mockImplementation((() => ok({ ...CONTRACT, id: 'new' })) as never)
    renderForm({ customerId: 'cust-1' })

    await userEvent.type(await screen.findByLabelText('Titolo'), 'Manutenzione')
    await userEvent.type(screen.getByLabelText('Inizio'), '2026-11-01')
    await userEvent.type(screen.getByLabelText('Tetto sulla quota del cliente (%)'), '50')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    expect(api.POST).toHaveBeenCalledWith(
      '/api/contracts',
      expect.objectContaining({
        body: expect.objectContaining({
          customer_id: 'cust-1',
          titolo: 'Manutenzione',
          tetto_quota_cliente: '50',
        }),
      }),
    )
  })

  it('clears the stored cap with null when the box is emptied on edit', async () => {
    vi.mocked(api.PATCH).mockImplementation((() => ok({ ...CONTRACT, tetto_quota_cliente: null })) as never)
    renderForm({ contract: CONTRACT })

    const box = await screen.findByLabelText('Tetto sulla quota del cliente (%)')
    expect(box).toHaveValue('50.00')
    await userEvent.clear(box)
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    expect(api.PATCH).toHaveBeenCalledWith(
      '/api/contracts/{contract_id}',
      expect.objectContaining({
        params: { path: { contract_id: 'c1' } },
        body: { tetto_quota_cliente: null },
      }),
    )
  })

  it('shows the API’s refusal under the cap field and keeps the dialog open', async () => {
    vi.mocked(api.PATCH).mockImplementation((() =>
      failed(
        {
          type: 'about:blank',
          title: 'Unprocessable',
          status: 422,
          code: 'validation_failed',
          field: 'tetto_quota_cliente',
          detail: 'tetto_quota_cliente: deve essere maggiore di 0 e minore di 100',
        },
        422,
      )) as never)
    renderForm({ contract: CONTRACT })

    const box = await screen.findByLabelText('Tetto sulla quota del cliente (%)')
    await userEvent.clear(box)
    await userEvent.type(box, '100')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    const message = await screen.findByText(/deve essere maggiore di 0 e minore di 100/)
    expect(message).toBeInTheDocument()
    expect(box).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })
})
