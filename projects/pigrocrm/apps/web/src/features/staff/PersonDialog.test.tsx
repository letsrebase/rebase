/** «Aggiungi una persona» and «Modifica condizioni» (REB-1027): the kind first, the body the API
 *  takes, only what changed on an edit, and the server's refusal in its own words. */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { PersonDialog } from './PersonDialog'
import type { StaffMember } from './queries'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PATCH: vi.fn() } }
})
vi.mock('@/features/users/directory', () => ({ useMemberDirectory: () => ({ members: [{ id: 'u-9', name: 'Giulia' }] }) }))

const member = {
  id: 'm-1',
  legal_entity_id: 'le-1',
  kind: 'employee',
  name: 'Giulia Bianchi',
  user_id: null,
  start_date: '2026-01-01',
  end_date: null,
  hours_per_month: null,
  collective_agreement: null,
  contract_level: null,
  gross_annual_pay: '30000.00',
  monthly_payments: 14,
  part_time_percent: '100.00',
  employer_inps_rate: null,
  employer_inail_rate: null,
  employee_inps_rate: null,
  thirteenth_month: null,
  fourteenth_month: null,
  annual_fee: null,
  fee_payment: null,
  accrues_tfm: false,
  tfm_annual_amount: null,
  inps_company_share: null,
} as unknown as StaffMember

function draw(props: Partial<React.ComponentProps<typeof PersonDialog>> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <PersonDialog open onOpenChange={() => {}} legalEntityId="le-1" legalEntityName={null} directorsAllowed={false} {...props} />
    </QueryClientProvider>,
  )
}

const SITE = {
  id: 'site-1',
  legal_entity_id: 'le-1',
  region: '05',
  region_name: 'Veneto',
  city: 'Padova',
  address: null,
  staff_count: 0,
  created_at: '2026-11-01T09:00:00Z',
  updated_at: '2026-11-01T09:00:00Z',
}

/** The azienda's sites, as `GET /api/legal-entities/{id}/sites` answers them. */
function withSites(sites: unknown[]) {
  vi.mocked(api.GET).mockResolvedValue({ data: sites, response: new Response(null, { status: 200 }) } as never)
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  withSites([])
  vi.mocked(api.POST).mockReset()
  vi.mocked(api.PATCH).mockReset()
})

describe('PersonDialog', () => {
  it('creates an employee with the fields typed, decimals as strings with a point', async () => {
    vi.mocked(api.POST).mockResolvedValue({ data: {}, response: new Response(null, { status: 201 }) } as never)
    const user = userEvent.setup()
    draw()
    await user.type(screen.getByLabelText(/^Nome/), 'Giulia Bianchi')
    await user.type(screen.getByLabelText(/^In servizio dal/), '2026-01-01')
    await user.type(screen.getByLabelText(/^Retribuzione lorda annua/), '30000,50')
    await user.click(screen.getByRole('button', { name: 'Aggiungi' }))
    await waitFor(() =>
      expect(api.POST).toHaveBeenCalledWith('/api/staff', {
        body: expect.objectContaining({
          kind: 'employee',
          legal_entity_id: 'le-1',
          name: 'Giulia Bianchi',
          start_date: '2026-01-01',
          gross_annual_pay: '30000.50',
          monthly_payments: 13,
        }),
      }),
    )
  })

  it('offers a director only where a company pack allows it', async () => {
    const user = userEvent.setup()
    draw()
    await user.click(screen.getByLabelText('Chi è'))
    expect(screen.queryByRole('option', { name: 'Amministratore' })).toBeNull()
    expect(screen.getByText('Gli amministratori si registrano solo su una società.')).toBeInTheDocument()
  })

  it('asks for the fields it cannot do without and sends nothing', async () => {
    const user = userEvent.setup()
    draw()
    await user.click(screen.getByRole('button', { name: 'Aggiungi' }))
    expect(screen.getByRole('alert')).toHaveTextContent('Compila i campi con l’asterisco.')
    expect(api.POST).not.toHaveBeenCalled()
  })

  it('sends only what changed on an edit, and an emptied optional field as null', async () => {
    vi.mocked(api.PATCH).mockResolvedValue({ data: {}, response: new Response(null, { status: 200 }) } as never)
    const user = userEvent.setup()
    draw({ member })
    await user.type(screen.getByLabelText(/^Ore di lavoro al mese/), '160')
    await user.click(screen.getByRole('button', { name: 'Salva le condizioni' }))
    await waitFor(() =>
      expect(api.PATCH).toHaveBeenCalledWith('/api/staff/{staff_member_id}', {
        params: { path: { staff_member_id: 'm-1' } },
        body: { hours_per_month: '160' },
      }),
    )
  })

  it('refuses a month that is not a whole number before anything is sent', async () => {
    const user = userEvent.setup()
    draw({ member })
    await user.type(screen.getByLabelText(/^Mese della tredicesima/), '1a')
    await user.click(screen.getByRole('button', { name: 'Salva le condizioni' }))
    expect(screen.getByRole('alert')).toHaveTextContent('Scrivi un numero intero')
    expect(api.PATCH).not.toHaveBeenCalled()
  })

  it('empties a stored fourteenth month when the payments go back to thirteen', async () => {
    vi.mocked(api.PATCH).mockResolvedValue({ data: {}, response: new Response(null, { status: 200 }) } as never)
    const user = userEvent.setup()
    draw({ member: { ...member, monthly_payments: 14, fourteenth_month: 6 } as StaffMember })
    await user.click(screen.getByLabelText(/^Mensilità/))
    await user.click(await screen.findByRole('option', { name: '13' }))
    await user.click(screen.getByRole('button', { name: 'Salva le condizioni' }))
    await waitFor(() =>
      expect(api.PATCH).toHaveBeenCalledWith('/api/staff/{staff_member_id}', {
        params: { path: { staff_member_id: 'm-1' } },
        body: { monthly_payments: 13, fourteenth_month: null },
      }),
    )
  })

  it('asks an employee\'s contract type and sends it, or clears it on an edit', async () => {
    vi.mocked(api.PATCH).mockResolvedValue({ data: {}, response: new Response(null, { status: 200 }) } as never)
    const user = userEvent.setup()
    draw({ member: { ...member, contract_type: 'seasonal' } as StaffMember })
    await user.click(screen.getByLabelText('Tipo di contratto'))
    await user.click(await screen.findByRole('option', { name: 'Tempo indeterminato' }))
    await user.click(screen.getByRole('button', { name: 'Salva le condizioni' }))
    await waitFor(() =>
      expect(api.PATCH).toHaveBeenCalledWith('/api/staff/{staff_member_id}', {
        params: { path: { staff_member_id: 'm-1' } },
        body: { contract_type: 'permanent' },
      }),
    )
  })

  it('asks whether the person is disabled and sends yes, no or not stated', async () => {
    vi.mocked(api.PATCH).mockResolvedValue({ data: {}, response: new Response(null, { status: 200 }) } as never)
    const user = userEvent.setup()
    draw({ member: { ...member, contract_type: 'apprentice', disabled_worker: true } as StaffMember })
    expect(screen.getByLabelText('Tipo di contratto')).toHaveTextContent('Apprendistato')
    await user.click(screen.getByLabelText('Lavoratore disabile'))
    await user.click(await screen.findByRole('option', { name: 'Non indicato' }))
    await user.click(screen.getByRole('button', { name: 'Salva le condizioni' }))
    await waitFor(() =>
      expect(api.PATCH).toHaveBeenCalledWith('/api/staff/{staff_member_id}', {
        params: { path: { staff_member_id: 'm-1' } },
        body: { disabled_worker: null },
      }),
    )
  })

  it('edits a seasonal worker\'s contract history and sends it whole, days by year', async () => {
    vi.mocked(api.PATCH).mockResolvedValue({ data: {}, response: new Response(null, { status: 200 }) } as never)
    const user = userEvent.setup()
    const first = { start_date: '2025-06-01', end_date: '2025-09-30', worked_days: { '2025': 90 } }
    draw({ member: { ...member, contract_type: 'seasonal', seasonal_contracts: [first] } as unknown as StaffMember })
    expect(screen.getByLabelText('Giorni lavorati nel 2025')).toHaveValue('90')
    await user.click(screen.getByRole('button', { name: 'Aggiungi un contratto' }))
    await user.type(screen.getByLabelText('Contratto 2, dal'), '2026-06-01')
    await user.type(screen.getByLabelText('Contratto 2, fino al'), '2026-09-30')
    await user.type(screen.getByLabelText('Giorni lavorati nel 2026'), '100')
    await user.click(screen.getByRole('button', { name: 'Salva le condizioni' }))
    await waitFor(() =>
      expect(api.PATCH).toHaveBeenCalledWith('/api/staff/{staff_member_id}', {
        params: { path: { staff_member_id: 'm-1' } },
        body: {
          seasonal_contracts: [first, { start_date: '2026-06-01', end_date: '2026-09-30', worked_days: { '2026': 100 } }],
        },
      }),
    )
  })

  it('refuses a contract with no start date before anything is sent', async () => {
    const user = userEvent.setup()
    draw({ member: { ...member, contract_type: 'seasonal', seasonal_contracts: [] } as unknown as StaffMember })
    await user.click(screen.getByRole('button', { name: 'Aggiungi un contratto' }))
    await user.type(screen.getByLabelText('Contratto 1, fino al'), '2026-09-30')
    await user.click(screen.getByRole('button', { name: 'Salva le condizioni' }))
    expect(await screen.findByText('Ogni contratto ha una data di inizio.')).toBeInTheDocument()
    expect(api.PATCH).not.toHaveBeenCalled()
  })

  it('shows the server\'s refusal as it says it', async () => {
    vi.mocked(api.POST).mockResolvedValue({ error: { detail: 'l\'azienda non ha ancora un profilo fiscale' }, response: new Response(null, { status: 422 }) } as never)
    const user = userEvent.setup()
    draw()
    await user.type(screen.getByLabelText(/^Nome/), 'Giulia')
    await user.type(screen.getByLabelText(/^In servizio dal/), '2026-01-01')
    await user.type(screen.getByLabelText(/^Retribuzione lorda annua/), '1000')
    await user.click(screen.getByRole('button', { name: 'Aggiungi' }))
    expect(await screen.findByText(/non ha ancora un profilo fiscale/)).toBeInTheDocument()
  })

  it('offers no workplace when the azienda has no sites, and sends none', async () => {
    vi.mocked(api.POST).mockResolvedValue({ data: {}, response: new Response(null, { status: 201 }) } as never)
    const user = userEvent.setup()
    draw()
    await waitFor(() => expect(api.GET).toHaveBeenCalled())
    expect(screen.queryByLabelText('Sede di lavoro')).not.toBeInTheDocument()
    await user.type(screen.getByLabelText(/^Nome/), 'Giulia')
    await user.type(screen.getByLabelText(/^In servizio dal/), '2026-01-01')
    await user.type(screen.getByLabelText(/^Retribuzione lorda annua/), '1000')
    await user.click(screen.getByRole('button', { name: 'Aggiungi' }))
    await waitFor(() => expect(api.POST).toHaveBeenCalled())
    const body = vi.mocked(api.POST).mock.calls[0]?.[1] as unknown as { body: Record<string, unknown> }
    expect(body.body).not.toHaveProperty('site_id')
    expect(body.body).not.toHaveProperty('at_registered_office')
  })

  it('offers the registered office and each site, and creates the person at the site chosen', async () => {
    withSites([SITE, { ...SITE, id: 'site-2', region: '03', region_name: 'Lombardia', city: null }])
    vi.mocked(api.POST).mockResolvedValue({ data: {}, response: new Response(null, { status: 201 }) } as never)
    const user = userEvent.setup()
    draw()
    await user.type(screen.getByLabelText(/^Nome/), 'Giulia')
    await user.type(screen.getByLabelText(/^In servizio dal/), '2026-01-01')
    await user.type(screen.getByLabelText(/^Retribuzione lorda annua/), '1000')
    await user.click(await screen.findByLabelText('Sede di lavoro'))
    expect(screen.getByRole('option', { name: 'Non indicata' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Sede legale' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Lombardia' })).toBeInTheDocument()
    await user.click(screen.getByRole('option', { name: 'Padova' }))
    await user.click(screen.getByRole('button', { name: 'Aggiungi' }))
    await waitFor(() =>
      expect(api.POST).toHaveBeenCalledWith('/api/staff', {
        body: expect.objectContaining({ site_id: 'site-1' }),
      }),
    )
    const sent = vi.mocked(api.POST).mock.calls[0]?.[1] as unknown as { body: Record<string, unknown> }
    expect(sent.body).not.toHaveProperty('at_registered_office')
  })

  it('creates a person at the registered office with the flag alone', async () => {
    withSites([SITE])
    vi.mocked(api.POST).mockResolvedValue({ data: {}, response: new Response(null, { status: 201 }) } as never)
    const user = userEvent.setup()
    draw()
    await user.type(screen.getByLabelText(/^Nome/), 'Giulia')
    await user.type(screen.getByLabelText(/^In servizio dal/), '2026-01-01')
    await user.type(screen.getByLabelText(/^Retribuzione lorda annua/), '1000')
    await user.click(await screen.findByLabelText('Sede di lavoro'))
    await user.click(screen.getByRole('option', { name: 'Sede legale' }))
    await user.click(screen.getByRole('button', { name: 'Aggiungi' }))
    await waitFor(() =>
      expect(api.POST).toHaveBeenCalledWith('/api/staff', {
        body: expect.objectContaining({ at_registered_office: true }),
      }),
    )
    const sent = vi.mocked(api.POST).mock.calls[0]?.[1] as unknown as { body: Record<string, unknown> }
    expect(sent.body).not.toHaveProperty('site_id')
  })

  it('moves a person from a site to the registered office, and from the office to nobody', async () => {
    withSites([SITE])
    vi.mocked(api.PATCH).mockResolvedValue({ data: {}, response: new Response(null, { status: 200 }) } as never)
    const user = userEvent.setup()
    const { unmount } = render_(member_({ site_id: 'site-1', at_registered_office: false }))
    await user.click(await screen.findByLabelText('Sede di lavoro'))
    await user.click(screen.getByRole('option', { name: 'Sede legale' }))
    await user.click(screen.getByRole('button', { name: 'Salva le condizioni' }))
    await waitFor(() =>
      expect(api.PATCH).toHaveBeenCalledWith('/api/staff/{staff_member_id}', {
        params: { path: { staff_member_id: 'm-1' } },
        body: { at_registered_office: true },
      }),
    )
    unmount()
    vi.mocked(api.PATCH).mockClear()
    render_(member_({ site_id: null, at_registered_office: true }))
    await user.click(await screen.findByLabelText('Sede di lavoro'))
    await user.click(screen.getByRole('option', { name: 'Non indicata' }))
    await user.click(screen.getByRole('button', { name: 'Salva le condizioni' }))
    await waitFor(() =>
      expect(api.PATCH).toHaveBeenCalledWith('/api/staff/{staff_member_id}', {
        params: { path: { staff_member_id: 'm-1' } },
        body: { at_registered_office: false },
      }),
    )
  })

  it('takes a person off their site with site_id null, and sends nothing when the place is unchanged', async () => {
    withSites([SITE])
    vi.mocked(api.PATCH).mockResolvedValue({ data: {}, response: new Response(null, { status: 200 }) } as never)
    const user = userEvent.setup()
    render_(member_({ site_id: 'site-1', at_registered_office: false }))
    await user.click(await screen.findByLabelText('Sede di lavoro'))
    await user.click(screen.getByRole('option', { name: 'Non indicata' }))
    await user.click(screen.getByRole('button', { name: 'Salva le condizioni' }))
    await waitFor(() =>
      expect(api.PATCH).toHaveBeenCalledWith('/api/staff/{staff_member_id}', {
        params: { path: { staff_member_id: 'm-1' } },
        body: { site_id: null },
      }),
    )
  })
})

function member_(place: { site_id: string | null; at_registered_office: boolean }): StaffMember {
  return { ...member, ...place } as StaffMember
}

function render_(person: StaffMember) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <PersonDialog open onOpenChange={() => {}} legalEntityId="le-1" legalEntityName={null} directorsAllowed={false} member={person} />
    </QueryClientProvider>,
  )
}
