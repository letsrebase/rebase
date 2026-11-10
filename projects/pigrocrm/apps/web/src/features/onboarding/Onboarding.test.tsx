/**
 * The onboarding driven as a new space's administrator drives it: the API is stubbed at
 * `api.GET`/`api.PUT`/`api.POST`, the same seam the settings panels' tests use.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Proposal } from './model'

const navigate = vi.fn()
vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return { ...actual, useNavigate: () => navigate }
})
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), PUT: vi.fn(), POST: vi.fn() } }
})

const logout = vi.fn()
vi.mock('@/lib/auth', () => ({ useAuth: () => ({ logout }) }))

import { api } from '@/lib/api'
import { Onboarding } from './Onboarding'

const GET = api.GET as unknown as ReturnType<typeof vi.fn>
const PUT = api.PUT as unknown as ReturnType<typeof vi.fn>
const POST = api.POST as unknown as ReturnType<typeof vi.fn>

const ok = (data: unknown) => ({ data, response: { status: 200 } })

function mod(key: string, enabled = true, records = 0, requires: string[] = []) {
  return {
    key,
    label: { deals: 'Deal', contracts: 'Contratti', time: 'Ore', costs: 'Costi', taxes: 'Tasse' }[key],
    description: `descrizione ${key}`,
    requires,
    enabled,
    stored: null,
    default: true,
    holds_data: records > 0,
    records,
  }
}

function proposal(dealRecords = 0): Proposal {
  return {
    profile: 'solo',
    previous_profile: null,
    applied: false,
    changes: [
      { module: 'deals', label: 'Deal', was: true, becomes: false, records: dealRecords },
      { module: 'time', label: 'Ore', was: true, becomes: false, records: 0 },
    ],
    state: {
      profile: null,
      onboarding: 'pending',
      modules: [
        mod('deals', true, dealRecords),
        mod('contracts'),
        mod('time', true, 0, ['deals']),
        mod('costs'),
        mod('taxes'),
      ],
    },
  } as Proposal
}

function stub({ saved = false, dealRecords = 0, entities = [{ id: 'le-1' }] as unknown[] | 'error' } = {}) {
  GET.mockImplementation(async (path: string) => {
    if (path === '/api/legal-entities') {
      return entities === 'error' ? { error: { status: 500 }, response: { status: 500 } } : ok(entities)
    }
    return saved ? ok({ pack_id: 'it-flat-rate' }) : { error: {}, response: { status: 404 } }
  })
  PUT.mockImplementation(async (path: string, init: { body: { confirm?: boolean } }) => {
    if (path.endsWith('/fiscal-profile')) return ok({})
    return ok({ ...proposal(dealRecords), applied: init.body.confirm === true })
  })
  POST.mockResolvedValue(ok({ onboarding: 'skipped' }))
}

function renderOnboarding() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <Onboarding />
    </QueryClientProvider>,
  )
}

const profileCalls = () =>
  PUT.mock.calls.filter(([path]) => path === '/api/settings/modules/profile') as [string, { body: unknown }][]

const fiscalCalls = () => PUT.mock.calls.filter(([path]) => String(path).endsWith('/fiscal-profile'))

beforeEach(() => {
  logout.mockReset()
  GET.mockReset()
  PUT.mockReset()
  POST.mockReset()
  navigate.mockReset()
})

describe('Onboarding', () => {
  it('asks how the person works first and goes on only once one is chosen', async () => {
    stub()
    const user = userEvent.setup()
    renderOnboarding()

    expect(screen.getByRole('heading', { name: 'Come lavori?' })).toBeInTheDocument()
    expect(screen.getByText('Passo 1 di 3')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Avanti' })).toBeDisabled()

    await user.click(screen.getByRole('radio', { name: /Da solo, con partita IVA/ }))
    await user.click(screen.getByRole('button', { name: 'Avanti' }))

    expect(await screen.findByRole('heading', { name: 'Con quale regime fatturi?' })).toBeInTheDocument()
    expect(PUT).not.toHaveBeenCalled()
  })

  it('«Decido dopo» on the first step skips the onboarding and opens «Primi passi»', async () => {
    stub()
    const user = userEvent.setup()
    renderOnboarding()

    await user.click(screen.getByRole('button', { name: 'Decido dopo' }))

    await waitFor(() => expect(navigate).toHaveBeenCalledWith({ to: '/app/get-started' }))
    expect(POST).toHaveBeenCalledWith('/api/settings/modules/onboarding/skip')
    expect(PUT).not.toHaveBeenCalled()
  })

  it('shows the modules, and writes the regime and the profile only at the end', async () => {
    stub()
    const user = userEvent.setup()
    renderOnboarding()

    await user.click(screen.getByRole('radio', { name: /Da solo/ }))
    await user.click(screen.getByRole('button', { name: 'Avanti' }))
    await user.click(await screen.findByRole('radio', { name: /Forfettario/ }))
    await user.click(screen.getByRole('button', { name: 'Avanti' }))

    expect(await screen.findByRole('heading', { name: 'Ecco come sarà il tuo spazio' })).toBeInTheDocument()
    // Nothing is written until the last step, so going back never leaves a stale answer.
    expect(fiscalCalls()).toHaveLength(0)
    const list = screen.getByTestId('onboarding-modules')
    expect(within(list).getByRole('checkbox', { name: 'Deal' })).not.toBeChecked()
    expect(within(list).getByRole('checkbox', { name: 'Contratti' })).toBeChecked()
    expect(profileCalls()).toHaveLength(1)
    expect(profileCalls()[0]![1].body).toEqual({ profile: 'solo', confirm: false })

    await user.click(screen.getByRole('button', { name: 'Conferma' }))

    await waitFor(() => expect(navigate).toHaveBeenCalledWith(expect.objectContaining({ to: '/app' })))
    expect(fiscalCalls()).toHaveLength(1)
    expect(fiscalCalls()[0]![1]).toMatchObject({
      body: { pack_id: 'it-flat-rate', regime_code: 'RF19', updated_at: null },
    })
    expect(profileCalls()[1]![1].body).toEqual({ profile: 'solo', confirm: true, modules: {} })
  })

  it('does not overwrite a fiscal profile the space already saved', async () => {
    stub({ saved: true })
    const user = userEvent.setup()
    renderOnboarding()

    await user.click(screen.getByRole('radio', { name: /Da solo/ }))
    await user.click(screen.getByRole('button', { name: 'Avanti' }))
    expect(await screen.findByText(/già impostato/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Avanti' }))

    expect(await screen.findByRole('heading', { name: 'Ecco come sarà il tuo spazio' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Conferma' }))
    await waitFor(() => expect(navigate).toHaveBeenCalled())
    expect(fiscalCalls()).toHaveLength(0)
  })

  it('offers a company the srl and marks the other forms as coming', async () => {
    stub()
    const user = userEvent.setup()
    renderOnboarding()

    await user.click(screen.getByRole('radio', { name: /Come azienda/ }))
    await user.click(screen.getByRole('button', { name: 'Avanti' }))

    expect(await screen.findByRole('radio', { name: /Srl/ })).toBeChecked()
    expect(screen.getByRole('radio', { name: /SpA/ })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Avanti' }))
    await screen.findByRole('heading', { name: 'Ecco come sarà il tuo spazio' })
    await user.click(screen.getByRole('button', { name: 'Conferma' }))
    await waitFor(() => expect(fiscalCalls()).toHaveLength(1))
    expect(fiscalCalls()[0]![1]).toMatchObject({
      body: { pack_id: 'it-standard', regime_code: 'RF01', default_vat_rate: '22.00' },
    })
  })

  it('names what a space with data would hide and lets the admin keep it on', async () => {
    stub({ dealRecords: 12 })
    const user = userEvent.setup()
    renderOnboarding()

    await user.click(screen.getByRole('radio', { name: /Da solo/ }))
    await user.click(screen.getByRole('button', { name: 'Avanti' }))
    await user.click(await screen.findByRole('radio', { name: /Ordinario/ }))
    await user.click(screen.getByRole('button', { name: 'Avanti' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Deal (12 record)')
    expect(screen.getByText(/12 record: restano, riaccendendolo li ritrovi/)).toBeInTheDocument()

    await user.click(screen.getByRole('checkbox', { name: 'Deal' }))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Conferma' }))

    await waitFor(() => expect(navigate).toHaveBeenCalled())
    expect(profileCalls()[1]![1].body).toEqual({
      profile: 'solo',
      confirm: true,
      modules: { deals: true },
    })
  })

  it('«Decido dopo» on any later step skips the onboarding and writes nothing', async () => {
    stub({ dealRecords: 3 })
    const user = userEvent.setup()
    renderOnboarding()

    await user.click(screen.getByRole('radio', { name: /Da solo/ }))
    await user.click(screen.getByRole('button', { name: 'Avanti' }))
    await user.click(await screen.findByRole('radio', { name: /Forfettario/ }))
    await user.click(screen.getByRole('button', { name: 'Decido dopo' }))

    await waitFor(() => expect(navigate).toHaveBeenCalledWith({ to: '/app/get-started' }))
    expect(POST).toHaveBeenCalledWith('/api/settings/modules/onboarding/skip')
    expect(PUT).not.toHaveBeenCalled()
  })

  it('skips from the last step too, keeping every module on', async () => {
    stub({ dealRecords: 3 })
    const user = userEvent.setup()
    renderOnboarding()

    await user.click(screen.getByRole('radio', { name: /Da solo/ }))
    await user.click(screen.getByRole('button', { name: 'Avanti' }))
    await user.click(await screen.findByRole('radio', { name: /Forfettario/ }))
    await user.click(screen.getByRole('button', { name: 'Avanti' }))
    await screen.findByRole('heading', { name: 'Ecco come sarà il tuo spazio' })
    await user.click(screen.getByRole('button', { name: 'Decido dopo' }))

    await waitFor(() => expect(POST).toHaveBeenCalled())
    expect(profileCalls()).toHaveLength(1)
    expect(profileCalls()[0]![1].body).toEqual({ profile: 'solo', confirm: false })
    expect(fiscalCalls()).toHaveLength(0)
  })

  it('says so, and goes no further, when the legal entity cannot be read or does not exist', async () => {
    stub({ entities: 'error' })
    const user = userEvent.setup()
    const first = renderOnboarding()
    await user.click(screen.getByRole('radio', { name: /Da solo/ }))
    await user.click(screen.getByRole('button', { name: 'Avanti' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Non riesco a leggere')
    expect(screen.getByRole('button', { name: 'Avanti' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Riprova' })).toBeInTheDocument()
    first.unmount()

    stub({ entities: [] })
    renderOnboarding()
    await user.click(screen.getByRole('radio', { name: /Da solo/ }))
    await user.click(screen.getByRole('button', { name: 'Avanti' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Non c’è ancora un’azienda')
    expect(screen.getByRole('button', { name: 'Avanti' })).toBeDisabled()
  })

  it('carries its own way out, since it stands outside the shell', async () => {
    stub()
    const user = userEvent.setup()
    renderOnboarding()
    await user.click(screen.getByRole('button', { name: 'Esci' }))
    expect(logout).toHaveBeenCalled()
  })
})
