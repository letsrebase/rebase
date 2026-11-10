/**
 * The account dialog (REB-1025): a new account, an edit, the server's refusals under their
 * fields, and «Archivia il conto» with «I movimenti restano».
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { AccountDialog } from './AccountDialog'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PATCH: vi.fn() } }
})

type Account = components['schemas']['BankAccountRead']

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
  balance: '10000.00',
  balance_as_of: '2026-01-01',
  stale_days: null,
  is_stale: false,
  unmatched_count: 0,
  created_at: '2026-01-01T00:00:00Z',
}

function renderDialog(account?: Account) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const onOpenChange = vi.fn()
  render(
    <QueryClientProvider client={client}>
      <AccountDialog open onOpenChange={onOpenChange} account={account} />
    </QueryClientProvider>,
  )
  return onOpenChange
}

describe('AccountDialog', () => {
  beforeEach(() => {
    vi.mocked(api.POST).mockReset()
    vi.mocked(api.PATCH).mockReset()
  })

  it('makes a new account from its two columns of fields', async () => {
    vi.mocked(api.POST).mockResolvedValue({ data: ACCOUNT, response: new Response() } as never)
    const user = userEvent.setup()
    const onOpenChange = renderDialog()
    expect(screen.getByRole('heading', { name: 'Aggiungi un conto' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Archivia il conto' })).toBeNull()
    await user.type(screen.getByLabelText('Nome del conto'), 'Conto operativo')
    await user.type(screen.getByLabelText('Banca'), 'Banco BPM')
    await user.type(screen.getByLabelText('IBAN'), 'IT60 X054 2811 1010 0000 0123 456')
    await user.type(screen.getByLabelText('Saldo iniziale'), '10.000,00')
    await user.type(screen.getByLabelText('Alla data del'), '2026-01-01')
    await user.type(screen.getByLabelText('Indirizzi da cui arrivano'), 'estratti@bancobpm.it')
    await user.click(screen.getByRole('button', { name: 'Aggiungi il conto' }))
    await waitFor(() => expect(api.POST).toHaveBeenCalled())
    expect(vi.mocked(api.POST).mock.calls[0]![0]).toBe('/api/bank-accounts')
    expect(vi.mocked(api.POST).mock.calls[0]![1]).toEqual({
      body: {
        name: 'Conto operativo',
        bank: 'Banco BPM',
        iban: 'IT60X0542811101000000123456',
        currency: 'EUR',
        opening_balance: '10000.00',
        opening_balance_date: '2026-01-01',
        arrival: 'upload',
        cadence: 'monthly',
        senders: ['estratti@bancobpm.it'],
      },
    })
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false))
  })

  it('asks for the opening balance and its date together', async () => {
    const user = userEvent.setup()
    renderDialog()
    await user.type(screen.getByLabelText('Saldo iniziale'), '100')
    await user.click(screen.getByRole('button', { name: 'Aggiungi il conto' }))
    expect(await screen.findByText('Il saldo iniziale e la sua data vanno indicati insieme.')).toBeTruthy()
    expect(api.POST).not.toHaveBeenCalled()
  })

  it('puts the server’s refusal under its field', async () => {
    vi.mocked(api.POST).mockResolvedValue({
      error: {
        type: 'about:blank',
        title: 'Dati non validi',
        status: 422,
        detail: 'IBAN non valido',
        code: 'validation_failed',
        entity: 'bank_account',
        field: 'iban',
        reason: 'IBAN non valido',
      },
      response: new Response(null, { status: 422 }),
    } as never)
    const user = userEvent.setup()
    renderDialog()
    await user.type(screen.getByLabelText('IBAN'), 'IT00')
    await user.click(screen.getByRole('button', { name: 'Aggiungi il conto' }))
    expect(await screen.findByText('IBAN non valido')).toBeTruthy()
    expect(screen.getByLabelText('IBAN').getAttribute('aria-invalid')).toBe('true')
  })

  it('edits an account and sends what the server lets change, not an IBAN that did not', async () => {
    vi.mocked(api.PATCH).mockResolvedValue({ data: ACCOUNT, response: new Response() } as never)
    const user = userEvent.setup()
    renderDialog(ACCOUNT)
    expect(screen.getByRole('heading', { name: 'Modifica il conto' })).toBeTruthy()
    expect((screen.getByLabelText('IBAN') as HTMLInputElement).value).toBe('IT60 X054 2811 1010 0000 0123 456')
    await user.clear(screen.getByLabelText('Nome del conto'))
    await user.type(screen.getByLabelText('Nome del conto'), 'Conto principale')
    await user.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(api.PATCH).toHaveBeenCalled())
    const body = (vi.mocked(api.PATCH).mock.calls[0]![1] as { body: Record<string, unknown> }).body
    expect(body.name).toBe('Conto principale')
    expect(body).not.toHaveProperty('iban')
    expect(body).not.toHaveProperty('currency')
    expect(body.opening_balance).toBe('10000.00')
  })

  it('archives after a second look, saying the movements stay', async () => {
    vi.mocked(api.POST).mockResolvedValue({ data: { ...ACCOUNT, archived_at: '2026-11-10T00:00:00Z' }, response: new Response() } as never)
    const user = userEvent.setup()
    const onOpenChange = renderDialog(ACCOUNT)
    expect(screen.getByText('I movimenti restano.')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Archivia il conto' }))
    expect(screen.getByText('Archiviare questo conto? I movimenti restano.')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Archivia il conto' }))
    await waitFor(() =>
      expect(api.POST).toHaveBeenCalledWith(
        '/api/bank-accounts/{account_id}/archive',
        expect.objectContaining({ params: { path: { account_id: 'acc-1' } } }),
      ),
    )
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false))
  })
})
