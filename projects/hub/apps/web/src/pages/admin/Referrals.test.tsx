import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AdminReferrals } from './Referrals'

function answer(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

const SETTINGS = { rate_freelancer: '0.1000', rate_company: '0.3000', updated_at: '2026-09-26T10:00:00Z', updated_by_nome: 'Ivan' }

const CONFIRMED_REWARD = {
  referral_id: 'ref1',
  reward_id: 'r1',
  kind: 'freelancer',
  referrer_nome: 'Mario Rossi',
  referrer_email: 'mario@community.it',
  referred_nome: 'Ada Lovelace',
  match_id: 'm1',
  rate: '0.1000',
  base_amount: '7000.00',
  reward_amount: '700.00',
  stato: 'da_confermare',
  note: null,
  created_at: '2026-09-26T10:00:00Z',
  confirmed_at: null,
  paid_at: null,
}

const UNPRICED_REWARD = {
  ...CONFIRMED_REWARD,
  referral_id: 'ref2',
  reward_id: 'r2',
  kind: 'company',
  referred_nome: 'ACME S.r.l.',
  base_amount: null,
  reward_amount: null,
}

const PENDING_REFERRAL = {
  ...CONFIRMED_REWARD,
  referral_id: 'ref3',
  reward_id: null,
  kind: 'freelancer',
  referred_nome: 'Grace Hopper',
  rate: null,
  base_amount: null,
  reward_amount: null,
  stato: null,
}

/** Branches on the path, not on call order: this page fires the settings and the
 *  ledger queries independently, so a sequential `mockResolvedValueOnce` queue would
 *  be a race. `overrides` lets one test answer a specific path differently. */
function mount(
  items: unknown[] = [],
  overrides: Record<string, (init: RequestInit | undefined) => Response> = {},
) {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = new URL(String(input), 'http://localhost')
    const path = url.pathname
    for (const [pattern, handler] of Object.entries(overrides)) {
      if (path === pattern || path.startsWith(pattern)) return handler(init)
    }
    if (path === '/api/hub/referral-settings') return answer(200, SETTINGS)
    if (path === '/api/hub/referrals') return answer(200, { items, next_cursor: null })
    return answer(404, { detail: 'not found' })
  })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <AdminReferrals />
    </QueryClientProvider>,
  )
  return client
}

afterEach(() => vi.restoreAllMocks())

describe('the Referral admin page (P-REB-44)', () => {
  it('shows the two rates and every reward in the ledger', async () => {
    mount([CONFIRMED_REWARD])
    await screen.findByText('Ada Lovelace')
    expect(screen.getByText('Mario Rossi')).toBeInTheDocument()
    expect(screen.getByText('700,00 €')).toBeInTheDocument()
    expect(screen.getByLabelText('Percentuale, segnalazione di un freelance')).toHaveValue(10)
    expect(screen.getByLabelText("Percentuale, segnalazione di un'azienda")).toHaveValue(30)
  })

  it('shows a reward rate to two decimals, not rounded to a whole percent', async () => {
    // Regression: the row used to round to toFixed(0), so a valid 10.50% rate read
    // as 11% and an admin could not check how the amount was computed (Greptile).
    mount([{ ...CONFIRMED_REWARD, rate: '0.1050' }])
    await screen.findByText('Ada Lovelace')
    expect(screen.getByText('7.000,00 € · 10.50%')).toBeInTheDocument()
  })

  it('saves a new pair of rates', async () => {
    let saved: unknown = null
    mount([], {
      '/api/hub/referral-settings': (init) => {
        if (init?.method === 'PUT') {
          saved = JSON.parse(init.body as string)
          return answer(200, { ...SETTINGS, rate_freelancer: '0.1200', rate_company: '0.3500' })
        }
        return answer(200, SETTINGS)
      },
    })
    await screen.findByLabelText('Percentuale, segnalazione di un freelance')
    const freelancerField = screen.getByLabelText('Percentuale, segnalazione di un freelance')
    fireEvent.change(freelancerField, { target: { value: '12' } })
    const companyField = screen.getByLabelText("Percentuale, segnalazione di un'azienda")
    fireEvent.change(companyField, { target: { value: '35' } })
    await userEvent.setup().click(screen.getByRole('button', { name: 'Salva percentuali' }))

    await waitFor(() => expect(saved).toEqual({ rate_freelancer: '0.1200', rate_company: '0.3500' }))
  })

  it('rounds a percentage that binary floating point cannot divide by 100 exactly, instead of sending an unparseable decimal', async () => {
    // Number('22.22') / 100 === 0.22219999999999998 without the fix, which overflows
    // the API's Numeric(5, 4) and fails to save silently.
    let saved: unknown = null
    mount([], {
      '/api/hub/referral-settings': (init) => {
        if (init?.method === 'PUT') {
          saved = JSON.parse(init.body as string)
          return answer(200, { ...SETTINGS, rate_freelancer: '0.2222', rate_company: '0.1111' })
        }
        return answer(200, SETTINGS)
      },
    })
    await screen.findByLabelText('Percentuale, segnalazione di un freelance')
    fireEvent.change(screen.getByLabelText('Percentuale, segnalazione di un freelance'), {
      target: { value: '22.22' },
    })
    fireEvent.change(screen.getByLabelText("Percentuale, segnalazione di un'azienda"), {
      target: { value: '11.11' },
    })
    await userEvent.setup().click(screen.getByRole('button', { name: 'Salva percentuali' }))

    await waitFor(() => expect(saved).toEqual({ rate_freelancer: '0.2222', rate_company: '0.1111' }))
  })

  it('editing one rate never sends the other as zero', async () => {
    // Recorded for the PR's own demo video: the company field showed 0.00 on the
    // next screen after only the freelancer field was edited and saved.
    let saved: unknown = null
    mount([], {
      '/api/hub/referral-settings': (init) => {
        if (init?.method === 'PUT') {
          saved = JSON.parse(init.body as string)
          return answer(200, { ...SETTINGS, rate_freelancer: '0.1200' })
        }
        return answer(200, SETTINGS)
      },
    })
    await screen.findByLabelText('Percentuale, segnalazione di un freelance')
    fireEvent.change(screen.getByLabelText('Percentuale, segnalazione di un freelance'), {
      target: { value: '12' },
    })
    await userEvent.setup().click(screen.getByRole('button', { name: 'Salva percentuali' }))

    await waitFor(() => expect(saved).toEqual({ rate_freelancer: '0.1200', rate_company: '0.3000' }))
  })

  it('moves a reward from da_confermare to confermato on the click', async () => {
    let moved: unknown = null
    mount([CONFIRMED_REWARD], {
      '/api/hub/referrals/r1/state': (init) => {
        moved = JSON.parse(init?.body as string)
        return answer(200, { ...CONFIRMED_REWARD, stato: 'confermato' })
      },
    })
    await screen.findByText('Ada Lovelace')

    await userEvent.setup().click(screen.getByRole('button', { name: 'Conferma' }))

    await waitFor(() => expect(moved).toEqual({ stato: 'confermato' }))
  })

  it('prices a reward with no computed figure before it can move', async () => {
    let priced: unknown = null
    mount([UNPRICED_REWARD], {
      '/api/hub/referrals/r2/price': (init) => {
        priced = JSON.parse(init?.body as string)
        return answer(200, { ...UNPRICED_REWARD, base_amount: '1000.00', reward_amount: '300.00' })
      },
    })
    await screen.findByText('ACME S.r.l.')
    expect(screen.queryByRole('button', { name: 'Conferma' })).toBeNull()
    const user = userEvent.setup()
    await user.type(screen.getByLabelText('Base, €'), '1000')
    await user.type(screen.getByLabelText('Reward, €'), '300')

    await user.click(screen.getByRole('button', { name: 'Prezza' }))

    await waitFor(() => expect(priced).toEqual({ base_amount: '1000', reward_amount: '300' }))
  })

  it('shows a referral with no reward yet, instead of hiding it until one exists', async () => {
    // Regression: the ledger used to start from the reward row, so a referral that
    // had only just signed up, with no letter signed for it yet, never appeared at
    // all -- the admin had no way to tell it existed (Greptile, P-REB-44).
    mount([PENDING_REFERRAL])
    await screen.findByText('Grace Hopper')
    expect(screen.getByText('In attesa del primo contratto firmato')).toBeInTheDocument()
    expect(screen.getByText('In attesa')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Conferma' })).toBeNull()
    expect(screen.queryByLabelText('Base, €')).toBeNull()
  })

  it('shows an empty state with no referral yet', async () => {
    mount([])
    expect(await screen.findByText('Nessun referral ancora.')).toBeInTheDocument()
  })
})
