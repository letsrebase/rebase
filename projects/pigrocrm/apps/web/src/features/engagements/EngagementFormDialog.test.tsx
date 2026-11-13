import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { toast } from '@rebase/ui/sonner'
import { api } from '@/lib/api'
import { ok, refused, renderWithQuery } from '@/features/inbox/testing'
import { EngagementFormDialog } from './EngagementFormDialog'
import { engagement } from './listFixtures'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PATCH: vi.fn() } }
})
vi.mock('@rebase/ui/sonner', () => ({ toast: { error: vi.fn(), success: vi.fn(), warning: vi.fn() } }))

const navigate = vi.fn()
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => navigate }))

vi.mock('@/lib/auth', () => ({
  useIsAdmin: () => true,
  useCanWrite: () => true,
  useCan: () => true,
  useAuth: () => ({ user: { id: 'u1', role: 'admin' }, isLoading: false }),
}))

const FREELANCER = { id: 'f1', legal_name: 'Giulia Martini' }
const CLIENT = { id: 'c1', legal_name: 'Alfa Logistica S.r.l.' }

const WEEKS = [
  { week: '2026-W45', starts_on: '2026-11-02', status: 'committed', committed_days: '3.0' },
  { week: '2026-W46', starts_on: '2026-11-09', status: 'free', committed_days: '0' },
]

function mockGets() {
  vi.mocked(api.GET).mockImplementation(((path: string, init?: { params?: { query?: Record<string, unknown> } }) => {
    if (path === '/api/organizations') {
      const role = init?.params?.query?.role
      return Promise.resolve(ok({ items: role === 'freelancer' ? [FREELANCER] : [CLIENT], next_cursor: null }))
    }
    if (path === '/api/organizations/{organization_id}') {
      const id = (init as unknown as { params: { path: { organization_id: string } } }).params.path.organization_id
      return Promise.resolve(ok(id === 'f1' ? FREELANCER : CLIENT))
    }
    if (path === '/api/freelancers/availability') return Promise.resolve(ok({ weeks: WEEKS }))
    if (path === '/api/deals') {
      return Promise.resolve(ok({ items: [{ id: 'd1', name: 'Migrazione WMS' }], next_cursor: null }))
    }
    if (path === '/api/contracts') {
      return Promise.resolve(ok({ items: [{ id: 'k1', title: 'Contratto quadro 2026' }], next_cursor: null }))
    }
    return Promise.resolve(ok({ items: [], next_cursor: null }))
  }) as never)
}

function render(props: Partial<Parameters<typeof EngagementFormDialog>[0]> = {}) {
  const onOpenChange = vi.fn()
  renderWithQuery(<EngagementFormDialog open onOpenChange={onOpenChange} {...props} />)
  return onOpenChange
}

/** The one write of the test, its body. */
function body(method: 'POST' | 'PATCH'): Record<string, unknown> {
  const call = vi.mocked(api[method]).mock.calls[0] as unknown as [string, { body: Record<string, unknown> }]
  return call[1].body
}

async function choose(combobox: string, option: string) {
  await userEvent.click(screen.getByRole('combobox', { name: combobox }))
  await userEvent.click(await screen.findByRole('option', { name: option }))
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  vi.mocked(api.PATCH).mockReset()
  vi.mocked(toast.success).mockReset()
  vi.mocked(toast.warning).mockReset()
  navigate.mockReset()
  mockGets()
})

describe('«Nuovo incarico»', () => {
  it('opens on the fields of the brief and the action «Crea la bozza»', async () => {
    render()
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('heading', { name: 'Nuovo incarico' })).toBeInTheDocument()
    for (const label of ['Ruolo', 'Inizio', 'Fine', 'Giorni a settimana', 'Compenso', 'Giorni previsti', 'Tipo di lettera', 'Pagamento']) {
      expect(within(dialog).getByText(label)).toBeInTheDocument()
    }
    expect(within(dialog).getByRole('button', { name: 'Crea la bozza' })).toBeInTheDocument()
    expect(within(dialog).getByText('Scegli il freelance per vedere quando è libero.')).toBeInTheDocument()
  })

  it('names what is missing and sends nothing', async () => {
    render()
    await userEvent.click(await screen.findByRole('button', { name: 'Crea la bozza' }))
    expect(screen.getByText('Scegli il freelance.')).toBeInTheDocument()
    expect(screen.getByText('Scrivi il ruolo.')).toBeInTheDocument()
    expect(screen.getByText('Scegli la data di inizio.')).toBeInTheDocument()
    expect(screen.getByText('Scegli i giorni a settimana.')).toBeInTheDocument()
    expect(screen.getByText('Scrivi il compenso.')).toBeInTheDocument()
    expect(screen.getByText('Scrivi i giorni dalla fattura.')).toBeInTheDocument()
    expect(api.POST).not.toHaveBeenCalled()
  })

  it('shows the freelancer’s availability from the chosen start, as soon as a freelancer is chosen', async () => {
    render()
    await userEvent.click(await screen.findByRole('button', { name: 'Giulia Martini' }))
    expect(await screen.findByRole('list', { name: 'Disponibilità, una cella a settimana' })).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Inizio'), { target: { value: '2026-11-02' } })
    await waitFor(() => {
      const calls = vi.mocked(api.GET).mock.calls as unknown as [string, { params: { query: Record<string, unknown> } }][]
      const last = calls.filter(([path]) => path === '/api/freelancers/availability').at(-1)
      expect(last?.[1].params.query).toMatchObject({ freelancer_id: 'f1', date_from: '2026-11-02' })
    })
  })

  it('creates the draft with decimals as strings, then opens its page', async () => {
    vi.mocked(api.POST).mockResolvedValue(
      ok(engagement({ id: 'e9', warnings: [{ code: 'overlap', message: 'Si sovrappone a un altro incarico.', engagement_ids: ['e1'], peak_days_per_week: '5' }] })) as never,
    )
    const onOpenChange = render()
    await userEvent.click(await screen.findByRole('button', { name: 'Giulia Martini' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Alfa Logistica S.r.l.' }))
    await choose('Deal', 'Migrazione WMS')
    await userEvent.type(screen.getByLabelText('Ruolo'), 'Backend developer')
    fireEvent.change(screen.getByLabelText('Inizio'), { target: { value: '2026-11-02' } })
    await choose('Giorni a settimana', '4,5')
    await userEvent.type(screen.getByLabelText('Compenso in euro'), '450,50')
    await userEvent.type(screen.getByLabelText('Giorni dalla fattura'), '30')
    await userEvent.click(screen.getByRole('checkbox', { name: 'A fine mese' }))
    await userEvent.click(screen.getByRole('button', { name: 'Crea la bozza' }))

    await waitFor(() => expect(api.POST).toHaveBeenCalled())
    expect(body('POST')).toEqual({
      freelancer_id: 'f1',
      client_organization_id: 'c1',
      deal_id: 'd1',
      role: 'Backend developer',
      starts_on: '2026-11-02',
      days_per_week: '4.5',
      fee: '450.50',
      fee_unit: 'day',
      payment_rule: 'fixed_term',
      payment_end_of_month: true,
      payment_days: 30,
    })
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith({
        to: '/app/engagements/$engagementId',
        params: { engagementId: 'e9' },
      }),
    )
    expect(toast.warning).toHaveBeenCalledWith('Si sovrappone a un altro incarico.')
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('opens filled in from the hub’s «Crea match» and creates the draft under the hub’s id', async () => {
    vi.mocked(api.POST).mockResolvedValue(ok(engagement({ id: 'e9' })) as never)
    render({
      freelancerId: 'f1',
      prefill: { clientId: 'c1', dealId: 'd1', hubMatchId: '0197f0b2-7a3c-7b1e-9c53-2f0a6a1d4e55' },
    })
    // The freelancer is the one the hub named, fixed; the client and the deal are the request's.
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Deal' })).toHaveTextContent('Migrazione WMS'))
    expect(await screen.findByText('Alfa Logistica S.r.l.')).toBeInTheDocument()
    expect(await screen.findByText('Giulia Martini')).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('Ruolo'), 'Backend developer')
    fireEvent.change(screen.getByLabelText('Inizio'), { target: { value: '2026-11-02' } })
    await choose('Giorni a settimana', '4')
    await userEvent.type(screen.getByLabelText('Compenso in euro'), '450')
    await userEvent.type(screen.getByLabelText('Giorni dalla fattura'), '30')
    await userEvent.click(screen.getByRole('button', { name: 'Crea la bozza' }))

    await waitFor(() => expect(api.POST).toHaveBeenCalled())
    expect(body('POST')).toMatchObject({
      freelancer_id: 'f1',
      client_organization_id: 'c1',
      deal_id: 'd1',
      hub_match_id: '0197f0b2-7a3c-7b1e-9c53-2f0a6a1d4e55',
    })
  })

  it('sends no hub id for an engagement nobody handed over', async () => {
    vi.mocked(api.POST).mockResolvedValue(ok(engagement({ id: 'e9' })) as never)
    render({ freelancerId: 'f1' })
    await userEvent.type(await screen.findByLabelText('Ruolo'), 'Backend developer')
    fireEvent.change(screen.getByLabelText('Inizio'), { target: { value: '2026-11-02' } })
    await choose('Giorni a settimana', '4')
    await userEvent.type(screen.getByLabelText('Compenso in euro'), '450')
    await userEvent.type(screen.getByLabelText('Giorni dalla fattura'), '30')
    await userEvent.click(screen.getByRole('button', { name: 'Crea la bozza' }))
    await waitFor(() => expect(api.POST).toHaveBeenCalled())
    expect(body('POST')).not.toHaveProperty('hub_match_id')
  })

  it('asks for the days after the client’s payment on an «a incasso» rule, and sends only that', async () => {
    vi.mocked(api.POST).mockResolvedValue(ok(engagement({ id: 'e9' })) as never)
    render()
    await userEvent.click(await screen.findByRole('button', { name: 'Giulia Martini' }))
    await userEvent.type(screen.getByLabelText('Ruolo'), 'Data engineer')
    fireEvent.change(screen.getByLabelText('Inizio'), { target: { value: '2026-12-01' } })
    await choose('Giorni a settimana', '2')
    await userEvent.type(screen.getByLabelText('Compenso in euro'), '60')
    await choose('Unità del compenso', "All'ora")
    await choose('Pagamento', 'A incasso, dopo che il cliente ha pagato rebase')
    expect(screen.queryByRole('checkbox', { name: 'A fine mese' })).not.toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('Giorni dall’incasso'), '15')
    await userEvent.click(screen.getByRole('button', { name: 'Crea la bozza' }))

    await waitFor(() => expect(api.POST).toHaveBeenCalled())
    expect(body('POST')).toMatchObject({
      fee_unit: 'hour',
      payment_rule: 'on_collection',
      payment_end_of_month: false,
      collection_days: 15,
    })
    expect(body('POST')).not.toHaveProperty('payment_days')
    expect(body('POST')).not.toHaveProperty('letter_type')
  })

  it('puts the server’s field error under its field, and any other refusal in the dialog', async () => {
    vi.mocked(api.POST).mockResolvedValueOnce(
      refused(422, {
        title: 'Dati non validi',
        detail: 'Compenso non valido',
        code: 'validation_failed',
        field: 'fee',
        reason: 'Il compenso è troppo alto.',
      }) as never,
    )
    render({ freelancerId: 'f1' })
    await userEvent.type(await screen.findByLabelText('Ruolo'), 'Backend developer')
    fireEvent.change(screen.getByLabelText('Inizio'), { target: { value: '2026-11-02' } })
    await choose('Giorni a settimana', '4')
    await userEvent.type(screen.getByLabelText('Compenso in euro'), '99999999')
    await userEvent.type(screen.getByLabelText('Giorni dalla fattura'), '30')
    await userEvent.click(screen.getByRole('button', { name: 'Crea la bozza' }))
    expect(await screen.findByText('Il compenso è troppo alto.')).toBeInTheDocument()
    expect(navigate).not.toHaveBeenCalled()

    vi.mocked(api.POST).mockResolvedValueOnce(
      refused(409, { title: 'Conflitto', detail: 'Il freelance non ha uno spazio.' }) as never,
    )
    await userEvent.click(screen.getByRole('button', { name: 'Crea la bozza' }))
    expect(await screen.findByText('Il freelance non ha uno spazio.')).toBeInTheDocument()
  })

  it('fixes the freelancer when the page already knows it', async () => {
    render({ freelancerId: 'f1' })
    expect(await screen.findByText('Giulia Martini')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Rimuovi il freelance' })).toBeDisabled()
    await waitFor(() => {
      const calls = vi.mocked(api.GET).mock.calls as unknown as [string, { params: { query: Record<string, unknown> } }][]
      expect(calls.some(([path, init]) => path === '/api/freelancers/availability' && init.params.query.freelancer_id === 'f1')).toBe(true)
    })
  })
})

describe('«Modifica»', () => {
  it('opens a draft with its values and the server’s warnings in a band', async () => {
    render({
      engagement: engagement({
        warnings: [{ code: 'over_capacity', message: 'Sopra i cinque giorni a settimana dal 02/11/2026.', engagement_ids: ['e2'], peak_days_per_week: '6' }],
      }),
    })
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('heading', { name: 'Modifica l’incarico' })).toBeInTheDocument()
    expect(within(dialog).getByRole('status', { name: 'Da sapere' })).toHaveTextContent(
      'Sopra i cinque giorni a settimana dal 02/11/2026.',
    )
    expect(within(dialog).getByLabelText('Ruolo')).toHaveValue('Backend developer')
    expect(within(dialog).getByLabelText('Inizio')).toHaveValue('2026-11-02')
    expect(within(dialog).getByRole('combobox', { name: 'Giorni a settimana' })).toHaveTextContent('4')
    expect(within(dialog).getByLabelText('Compenso in euro')).toHaveValue('450.00')
    expect(within(dialog).getByRole('button', { name: 'Salva' })).toBeInTheDocument()
    expect(await within(dialog).findByText('Alfa Logistica S.r.l.')).toBeInTheDocument()
  })

  it('sends only what changed, and excludes the engagement from its own availability', async () => {
    vi.mocked(api.PATCH).mockResolvedValue(ok(engagement()) as never)
    render({ engagement: engagement() })
    const role = await screen.findByLabelText('Ruolo')
    await userEvent.clear(role)
    await userEvent.type(role, 'Tech lead')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(api.PATCH).toHaveBeenCalled())
    expect(body('PATCH')).toEqual({ role: 'Tech lead' })
    const calls = vi.mocked(api.GET).mock.calls as unknown as [string, { params: { query: Record<string, unknown> } }][]
    expect(
      calls.some(([path, init]) => path === '/api/freelancers/availability' && init.params.query.excluding_engagement_id === 'e1'),
    ).toBe(true)
  })

  it('leaves a letter that has gone out read-only except the days a week and the client links', async () => {
    vi.mocked(api.PATCH).mockResolvedValue(ok(engagement({ state: 'awaiting_signature' })) as never)
    render({ engagement: engagement({ state: 'awaiting_signature', days_per_week: null, waiting: [{ code: 'missing_days_per_week' }] }) })
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/La lettera è già partita/)).toBeInTheDocument()
    expect(within(dialog).getByLabelText('Ruolo')).toBeDisabled()
    expect(within(dialog).getByLabelText('Inizio')).toBeDisabled()
    expect(within(dialog).getByLabelText('Compenso in euro')).toBeDisabled()
    expect(within(dialog).getByRole('combobox', { name: 'Giorni a settimana' })).toBeEnabled()
    await choose('Giorni a settimana', '3,5')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(api.PATCH).toHaveBeenCalled())
    expect(body('PATCH')).toEqual({ days_per_week: '3.5' })
  })

  it('closes without a request when nothing changed', async () => {
    const onOpenChange = render({ engagement: engagement() })
    await userEvent.click(await screen.findByRole('button', { name: 'Salva' }))
    expect(api.PATCH).not.toHaveBeenCalled()
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })
})
