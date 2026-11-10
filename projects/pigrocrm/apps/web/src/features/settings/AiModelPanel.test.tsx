import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { ok, renderWithQuery } from '@/features/proposals/testing'
import { AiModelPanel } from './AiModelPanel'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), PUT: vi.fn(), POST: vi.fn() } }
})
vi.mock('@rebase/ui/sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const OFF = {
  enabled: false,
  provider: null,
  model: null,
  has_key: false,
  daily_cap_eur: null,
  input_eur_per_mtok: null,
  output_eur_per_mtok: null,
  missing: ['provider', 'model', 'api_key', 'daily_cap_eur', 'prices'],
  spent_today_eur: '0.000000',
  privacy_acknowledged_at: null,
  privacy_acknowledged_by_id: null,
  privacy_notice: 'Se accendi la lettura con il modello, PigroCRM invia al fornitore il testo di ogni PDF.',
}
const READY = {
  ...OFF,
  provider: 'anthropic',
  model: 'claude-x',
  has_key: true,
  daily_cap_eur: '5.00',
  input_eur_per_mtok: '3.0000',
  output_eur_per_mtok: '15.0000',
  missing: [],
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.PUT).mockReset()
  vi.mocked(api.POST).mockReset()
})

describe('the model settings', () => {
  it('is off by default, says what is missing and cannot be switched on', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(OFF) as never)
    renderWithQuery(<AiModelPanel />)
    expect(await screen.findByText('spento')).toBeInTheDocument()
    expect(screen.getByText(/mancano: il fornitore, il modello, la chiave/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Accendi il modello' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Prova' })).toBeDisabled()
  })

  it('never shows the key and sends one only when it is typed', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(READY) as never)
    vi.mocked(api.PUT).mockResolvedValue(ok(READY) as never)
    renderWithQuery(<AiModelPanel />)
    const key = await screen.findByLabelText('Chiave')
    expect(key).toHaveValue('')
    expect(screen.getByText('Una chiave è salvata e non viene mai mostrata.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Salva' })).toBeDisabled()
    await userEvent.type(key, 'una-chiave-nuova-123')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(api.PUT).toHaveBeenCalled())
    expect(vi.mocked(api.PUT).mock.calls[0]?.[1]).toEqual({ body: { api_key: 'una-chiave-nuova-123' } })
  })

  it('switches on only after the notice is acknowledged', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(READY) as never)
    vi.mocked(api.PUT).mockResolvedValue(ok({ ...READY, enabled: true }) as never)
    renderWithQuery(<AiModelPanel />)
    const on = await screen.findByRole('button', { name: 'Accendi il modello' })
    expect(on).toBeDisabled()
    await userEvent.click(screen.getByRole('checkbox', { name: 'Ho letto cosa riceve il modello' }))
    await userEvent.click(on)
    await waitFor(() => expect(api.PUT).toHaveBeenCalled())
    expect(vi.mocked(api.PUT).mock.calls[0]?.[1]).toEqual({ body: { enabled: true, privacy_acknowledged: true } })
  })

  it('tries the saved settings and reports what the call used', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(READY) as never)
    vi.mocked(api.POST).mockResolvedValue(ok({ ok: true, input_tokens: 12, output_tokens: 2, cost_eur: '0.0001' }) as never)
    renderWithQuery(<AiModelPanel />)
    await userEvent.click(await screen.findByRole('button', { name: 'Prova' }))
    expect(await screen.findByText(/12 token letti, 2 scritti/)).toBeInTheDocument()
  })
})
