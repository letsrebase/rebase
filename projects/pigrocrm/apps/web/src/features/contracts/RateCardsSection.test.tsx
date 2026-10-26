import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { RateCardsSection } from './RateCardsSection'

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

const CLOSED = {
  id: 'r1',
  contract_id: 'c1',
  valido_da: '2026-01-01',
  valido_a: '2026-06-30',
  tipo: 'giornaliero',
  importo: '400.00',
  unita: 'giorno',
  frazioni_ammesse: ['1.00', '0.50'],
  ore_minime: null,
  periodo_erogazione: null,
}
const OPEN = {
  ...CLOSED,
  id: 'r2',
  valido_da: '2026-07-01',
  valido_a: null,
  importo: '450.00',
  frazioni_ammesse: ['1.00'],
}

function renderSection() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <RateCardsSection contractId="c1" />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(2026, 9, 26))
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  vi.mocked(api.PATCH).mockReset()
  vi.mocked(api.GET).mockImplementation((() => ok([CLOSED, OPEN])) as never)
})
afterEach(() => vi.useRealTimers())

describe('RateCardsSection', () => {
  it('lists the cards by validity and says where each stands today', async () => {
    renderSection()
    expect(await screen.findByText('dal 01/01/2026 al 30/06/2026')).toBeInTheDocument()
    expect(screen.getByText('Chiusa')).toBeInTheDocument()
    expect(screen.getByText('dal 01/07/2026')).toBeInTheDocument()
    expect(screen.getByText('In vigore')).toBeInTheDocument()
    expect(screen.getByText(/frazioni 1 · 0,5/)).toBeInTheDocument()
  })

  it('offers to close the card in force the day before a new rate and sends that choice', async () => {
    vi.mocked(api.POST).mockImplementation((() => ok({ ...OPEN, id: 'r3' })) as never)
    renderSection()
    await userEvent.click(await screen.findByRole('button', { name: 'Nuova tariffa' }))
    const dialog = screen.getByRole('dialog')
    await userEvent.type(screen.getByLabelText('Valida dal'), '2027-01-01')
    expect(screen.getByLabelText(/Chiudi la tariffa in vigore dal 01\/07\/2026/)).toBeChecked()
    await userEvent.type(screen.getByLabelText('Importo (€)'), '480.00')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    expect(api.POST).toHaveBeenCalledWith(
      '/api/contracts/{contract_id}/rate-cards',
      expect.objectContaining({
        params: { path: { contract_id: 'c1' }, query: { chiudi_precedente: true } },
        body: expect.objectContaining({ valido_da: '2027-01-01', importo: '480.00', valido_a: null }),
      }),
    )
    await vi.waitFor(() => expect(dialog).not.toBeInTheDocument())
  })

  it('shows the API’s refusal under the dates when the person declines to close the old rate', async () => {
    vi.mocked(api.POST).mockImplementation((() =>
      failed(
        {
          type: 'about:blank',
          title: 'Conflict',
          status: 409,
          code: 'conflict',
          detail: 'rate_card: il periodo di validità si sovrappone a un\'altra scheda tariffaria di questo contratto',
        },
        409,
      )) as never)
    renderSection()
    await userEvent.click(await screen.findByRole('button', { name: 'Nuova tariffa' }))
    await userEvent.type(screen.getByLabelText('Valida dal'), '2027-01-01')
    await userEvent.click(screen.getByLabelText(/Chiudi la tariffa in vigore/))
    await userEvent.type(screen.getByLabelText('Importo (€)'), '480.00')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    expect(await screen.findByText(/si sovrappone a un'altra scheda tariffaria/)).toBeInTheDocument()
    expect(api.POST).toHaveBeenCalledWith(
      '/api/contracts/{contract_id}/rate-cards',
      expect.objectContaining({
        params: { path: { contract_id: 'c1' }, query: { chiudi_precedente: false } },
      }),
    )
  })

  it('does not offer to close anything when no card is in force on the new start', async () => {
    renderSection()
    await userEvent.click(await screen.findByRole('button', { name: 'Nuova tariffa' }))
    await userEvent.type(screen.getByLabelText('Valida dal'), '2025-01-01')
    expect(screen.queryByLabelText(/Chiudi la tariffa in vigore/)).not.toBeInTheDocument()
  })

  it('edits a card through PATCH, with the conditional fields cleared for the type', async () => {
    vi.mocked(api.PATCH).mockImplementation((() => ok(OPEN)) as never)
    renderSection()
    await userEvent.click(
      await screen.findByRole('button', { name: 'Modifica la tariffa dal 01/07/2026' }),
    )
    const importo = screen.getByLabelText('Importo (€)')
    await userEvent.clear(importo)
    await userEvent.type(importo, '475.00')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    expect(api.PATCH).toHaveBeenCalledWith(
      '/api/contracts/{contract_id}/rate-cards/{rate_card_id}',
      expect.objectContaining({
        params: { path: { contract_id: 'c1', rate_card_id: 'r2' } },
        body: expect.objectContaining({
          importo: '475.00',
          valido_a: null,
          ore_minime: null,
          periodo_erogazione: null,
        }),
      }),
    )
  })
})
