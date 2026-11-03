import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { ConvertDealDialog } from './ConvertDealDialog'

const navigate = vi.fn()
vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return { ...actual, useNavigate: () => navigate }
})
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn() } }
})
vi.mock('@rebase/ui/sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))

function ok(data: unknown, status = 200) {
  return Promise.resolve({ data, response: new Response(null, { status }) }) as never
}
function failed(error: unknown, status: number) {
  return Promise.resolve({ error, response: new Response(null, { status }) }) as never
}

const PROPOSAL = {
  deal_id: 'd1',
  customer_id: 'c1',
  legal_entity_id: 'a1',
  title: 'Piattaforma',
  start: '2026-02-01',
  end: null,
  cards: [
    {
      valid_from: '2026-02-01',
      valid_to: '2026-02-01',
      type: 'one_off',
      amount: '20000.00',
      unit: 'flat',
      allowed_fractions: ['1'],
    },
    {
      valid_from: '2026-02-01',
      valid_to: '2027-01-31',
      type: 'daily',
      amount: '600.00',
      unit: 'day',
      allowed_fractions: ['1'],
      delivery_period: 'monthly',
      expected_quantity: '4.00',
    },
  ],
}

function renderDialog(onOpenChange = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <ConvertDealDialog dealId="d1" open onOpenChange={onOpenChange} />
    </QueryClientProvider>,
  )
  return onOpenChange
}

beforeEach(() => {
  navigate.mockReset()
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
})

describe('ConvertDealDialog', () => {
  it('opens on what the deal proposes and lists the cards that will be created', async () => {
    vi.mocked(api.GET).mockImplementation((() => ok(PROPOSAL)) as never)
    renderDialog()

    expect(await screen.findByLabelText('Titolo')).toHaveValue('Piattaforma')
    expect(screen.getByLabelText('Inizio')).toHaveValue('2026-02-01')
    expect(screen.getByLabelText('Fine')).toHaveValue('')
    expect(screen.getByText(/Nessuna riga indica una fine/)).toBeInTheDocument()
    expect(screen.getByText('Una tantum')).toBeInTheDocument()
    expect(screen.getByText(/Giornaliera · Mensile · 4.00 previsti/)).toBeInTheDocument()
    expect(api.GET).toHaveBeenCalledWith(
      '/api/deals/{deal_id}/contract-proposal',
      expect.objectContaining({ params: { path: { deal_id: 'd1' } } }),
    )
  })

  it('creates the contract with the confirmed fields and moves to it', async () => {
    vi.mocked(api.GET).mockImplementation((() => ok(PROPOSAL)) as never)
    vi.mocked(api.POST).mockImplementation((() => ok({ id: 'k1' }, 201)) as never)
    const onOpenChange = renderDialog()

    const end = await screen.findByLabelText('Fine')
    await userEvent.type(end, '2027-01-31')
    await userEvent.click(screen.getByRole('button', { name: 'Crea contratto' }))

    expect(api.POST).toHaveBeenCalledWith(
      '/api/deals/{deal_id}/contract',
      expect.objectContaining({
        params: { path: { deal_id: 'd1' } },
        body: {
          title: 'Piattaforma',
          start: '2026-02-01',
          end: '2027-01-31',
          renewal_type: 'none',
          renewal_notice_days: null,
          termination_notice_days: 30,
          billing_cadence: 'monthly',
          expense_policy: { kind: 'not_reimbursable' },
        },
      }),
    )
    await vi.waitFor(() =>
      expect(navigate).toHaveBeenCalledWith({
        to: '/app/contracts/$contractId',
        params: { contractId: 'k1' },
      }),
    )
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('shows a refusal under its field and stays open', async () => {
    vi.mocked(api.GET).mockImplementation((() => ok(PROPOSAL)) as never)
    vi.mocked(api.POST).mockImplementation((() =>
      failed(
        {
          type: 'about:blank',
          title: 'Unprocessable',
          status: 422,
          code: 'validation_failed',
          field: 'end',
          detail: 'fine non può precedere inizio',
        },
        422,
      )) as never)
    const onOpenChange = renderDialog()

    await screen.findByLabelText('Titolo')
    await userEvent.click(screen.getByRole('button', { name: 'Crea contratto' }))

    expect(await screen.findByText(/fine non può precedere inizio/)).toBeInTheDocument()
    expect(onOpenChange).not.toHaveBeenCalled()
    expect(navigate).not.toHaveBeenCalled()
  })

  it('prints the server’s refusal instead of the form when the deal cannot convert', async () => {
    vi.mocked(api.GET).mockImplementation((() =>
      failed(
        {
          type: 'about:blank',
          title: 'Conflict',
          status: 409,
          code: 'conflict',
          detail: 'il deal non ha righe di valore: aggiungile prima',
        },
        409,
      )) as never)
    renderDialog()

    expect(await screen.findByText(/il deal non ha righe di valore/)).toBeInTheDocument()
    expect(screen.queryByLabelText('Titolo')).not.toBeInTheDocument()
  })
})
