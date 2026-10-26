import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { DayForm } from './DayForm'
import { CONTRACT_ID, contract, ok, rateCard, refused, renderWithQuery, row } from './testing'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn() } }
})

const navigate = vi.fn()
vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, params, children }: { to: string; params?: Record<string, string>; children: React.ReactNode }) => (
    <a href={params ? to.replace(/\$(\w+)/g, (_, key: string) => params[key] ?? '') : to}>{children}</a>
  ),
  useNavigate: () => navigate,
}))

const auth = { ruolo: 'collaboratore' as 'readonly' | 'collaboratore' | 'admin' }
vi.mock('@/lib/auth', () => ({ useCanWrite: () => auth.ruolo !== 'readonly' }))
vi.mock('@rebase/ui/sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

function serving(options: {
  contracts?: unknown[]
  cards?: unknown[]
  sameDate?: unknown[]
} = {}) {
  vi.mocked(api.GET).mockImplementation(((path: string) => {
    if (path === '/api/contracts') return Promise.resolve(ok({ items: options.contracts ?? [contract()], next_cursor: null }))
    if (path === '/api/contracts/{contract_id}/rate-cards') return Promise.resolve(ok(options.cards ?? [rateCard()]))
    if (path === '/api/work-units') return Promise.resolve(ok({ items: options.sameDate ?? [], troncato: false }))
    return Promise.resolve(ok(undefined))
  }) as never)
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  navigate.mockReset()
  auth.ruolo = 'collaboratore'
})

describe('recording a day', () => {
  it('records half a day on the only contract, with the rate in force beside it, and opens the day', async () => {
    serving()
    vi.mocked(api.POST).mockResolvedValue(ok(row({ id: 'new-day' })) as never)
    const user = userEvent.setup()
    renderWithQuery(<DayForm initialDate="2026-03-02" />)

    // One contract on offer is chosen for the person, and its rate is on the page before saving.
    expect(await screen.findByText('450,00 €')).toBeInTheDocument()
    expect(screen.getByText('al giorno')).toBeInTheDocument()
    await user.click(screen.getByRole('radio', { name: 'Mezza giornata' }))
    await user.type(screen.getByLabelText('Cosa hai fatto'), '  Revisione architettura servizi ')
    await user.click(screen.getByRole('button', { name: 'Registra giornata' }))

    await waitFor(() => expect(api.POST).toHaveBeenCalledTimes(1))
    expect(vi.mocked(api.POST).mock.calls[0]?.[0]).toBe('/api/work-units')
    expect(vi.mocked(api.POST).mock.calls[0]?.[1]).toEqual({
      body: {
        contract_id: CONTRACT_ID,
        data: '2026-03-02',
        quantita: '0.50',
        descrizione: 'Revisione architettura servizi',
        stato: 'lavorato',
      },
    })
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith({ to: '/app/days/$workUnitId', params: { workUnitId: 'new-day' } }),
    )
  })

  it('cannot be saved without a task', async () => {
    serving()
    renderWithQuery(<DayForm initialDate="2026-03-02" />)
    await screen.findByText('450,00 €')
    expect(screen.getByRole('button', { name: 'Registra giornata' })).toBeDisabled()
  })

  it('says a day without the required approval is recorded flagged, and does not block it', async () => {
    serving({ contracts: [contract({ requires_prior_approval: true })] })
    renderWithQuery(<DayForm initialDate="2026-03-02" />)
    expect(await screen.findByText(/richiede l’approvazione preventiva/)).toBeInTheDocument()
    expect(screen.getByText(/Lavorata senza approvazione/)).toBeInTheDocument()
  })

  it('says when the date is outside the contract', async () => {
    serving({ contracts: [contract({ inizio: '2026-04-01', fine: '2026-12-31' })] })
    renderWithQuery(<DayForm initialDate="2026-03-02" />)
    expect(await screen.findByText(/La data è fuori dal contratto, che va dal 01\/04\/2026 al 31\/12\/2026/)).toBeInTheDocument()
  })

  it('offers no half day where the rate in force prices whole days only', async () => {
    serving({ cards: [rateCard({ frazioni_ammesse: ['1.00'] })] })
    renderWithQuery(<DayForm initialDate="2026-03-02" />)
    expect(await screen.findByText(/accetta solo giornate intere/)).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'Mezza giornata' })).toBeDisabled()
    expect(screen.getByRole('radio', { name: 'Giornata intera' })).toBeChecked()
  })

  it('says when no rate is in force on the date', async () => {
    serving({ cards: [rateCard({ valido_da: '2026-06-01' })] })
    renderWithQuery(<DayForm initialDate="2026-03-02" />)
    expect(await screen.findByText(/Nessuna tariffa in vigore il 02\/03\/2026/)).toBeInTheDocument()
  })

  it('blocks a second day on a date the contract already holds one for, and links the first', async () => {
    serving({ sameDate: [row({ id: 'taken', stato: 'lavorato' })] })
    const user = userEvent.setup()
    renderWithQuery(<DayForm initialDate="2026-03-02" />)
    expect(await screen.findByText(/ha già una giornata il 02\/03\/2026/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Aprila' })).toHaveAttribute('href', '/app/days/taken')
    await user.type(screen.getByLabelText('Cosa hai fatto'), 'Altro')
    expect(screen.getByRole('button', { name: 'Registra giornata' })).toBeDisabled()
  })

  it('does not count a revoked day as taking the date', async () => {
    serving({ sameDate: [row({ id: 'gone', stato: 'revocato' })] })
    const user = userEvent.setup()
    renderWithQuery(<DayForm initialDate="2026-03-02" />)
    await screen.findByText('450,00 €')
    await user.type(screen.getByLabelText('Cosa hai fatto'), 'Altro')
    expect(screen.getByRole('button', { name: 'Registra giornata' })).toBeEnabled()
  })

  it('shows the server’s own sentence when it refuses', async () => {
    serving()
    vi.mocked(api.POST).mockResolvedValue(
      refused(409, { title: 'x', status: 409, detail: 'work_unit: questo contratto ha gia\' una giornata in quella data', code: 'conflict', instance: '/' }) as never,
    )
    const user = userEvent.setup()
    renderWithQuery(<DayForm initialDate="2026-03-02" />)
    await screen.findByText('450,00 €')
    await user.type(screen.getByLabelText('Cosa hai fatto'), 'Revisione')
    await user.click(screen.getByRole('button', { name: 'Registra giornata' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/gia' una giornata in quella data/)
    expect(navigate).not.toHaveBeenCalled()
  })

  it('opens on the contract it was reached from', async () => {
    serving({ contracts: [contract(), contract({ id: 'other', titolo: 'Sviluppo piattaforma' })] })
    renderWithQuery(<DayForm initialContractId="other" initialDate="2026-03-02" />)
    await waitFor(() =>
      expect(screen.getByRole('combobox', { name: 'Contratto' })).toHaveTextContent('Sviluppo piattaforma'),
    )
  })

  it('offers a reader nothing to record', () => {
    auth.ruolo = 'readonly'
    serving()
    renderWithQuery(<DayForm />)
    expect(screen.getByText(/puoi leggere le giornate ma non registrarne/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Registra giornata' })).not.toBeInTheDocument()
  })
})
