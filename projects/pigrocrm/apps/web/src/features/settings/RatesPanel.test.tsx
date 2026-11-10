import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { RatesPanel } from './RatesPanel'

/**
 * Spies on the client's own verbs, the shape `EmitterPanel.test.tsx` and
 * `PeriodsPanel.test.tsx` both use: `msw` is not a dependency here and could not
 * intercept anything anyway, since `lib/api.ts` builds one openapi-fetch client at
 * import time.
 */
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), PUT: vi.fn() } }
})
vi.mock('@rebase/ui/sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))

function ok(data: unknown, status = 200) {
  return Promise.resolve({ data, response: new Response(null, { status }) } as never)
}

function failed(error: unknown, status: number) {
  return Promise.resolve({ error, response: new Response(null, { status }) } as never)
}

const IVAN = {
  id: 'u1',
  name: 'Ivan',
  email: 'ivan@x.test',
  role: 'admin',
  active: true,
  // Six decimals, because that is what `Numeric(12,6)` stores and what must survive
  // the round trip untouched.
  default_hourly_rate: '80.000000',
  default_hourly_cost: '35.000000',
  created_at: '2026-01-01T00:00:00Z',
}
const MARA = {
  id: 'u2',
  name: 'Mara',
  email: 'mara@x.test',
  role: 'collaborator',
  active: true,
  default_hourly_rate: null,
  default_hourly_cost: null,
  created_at: '2026-01-01T00:00:00Z',
}

const DEAL_TIMES = { created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' }
const DEAL_BASE = {
  customer_id: 'cli-1',
  pipeline_stage_id: 'st-1',
  expected_value: null,
  probability: 50,
  expected_close_date: null,
  owner_id: null,
  note: null,
  budgeted_hours: null,
  budgeted_value: null,
  custom_fields: {},
  ...DEAL_TIMES,
}
const ACME = { ...DEAL_BASE, id: 'd1', name: 'Sito Acme', hourly_rate: '120.000000' }
/** A real rate meaning "these hours are not billed", not the absence of one. */
const PRO_BONO = { ...DEAL_BASE, id: 'd2', name: 'Pro bono ONLUS', hourly_rate: '0.000000' }
const WITHOUT = { ...DEAL_BASE, id: 'd3', name: 'Restyling Bianchi', hourly_rate: null }

function respond(users: unknown[], deals: unknown[]) {
  vi.mocked(api.GET).mockImplementation((path: string) => {
    if (path === '/api/users') return ok(users)
    if (path === '/api/deals') return ok({ items: deals, next_cursor: null })
    throw new Error(`unexpected GET ${path}`)
  })
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <RatesPanel />
    </QueryClientProvider>,
  )
}

/** The `<tr>` an input belongs to: both tables put their Salva button in the last cell
 *  of the row, and there is one such button per person and per deal. */
function rowOf(input: HTMLElement) {
  const row = input.closest('tr')
  if (!row) throw new Error('input is not in a row')
  return row
}

function bodyOfCall(index: number) {
  const call = vi.mocked(api.PUT).mock.calls[index] as unknown as [
    string,
    { params: { path: Record<string, string> }; body: Record<string, unknown> },
  ]
  return call[1].body
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.PUT).mockReset()
})

describe('RatesPanel', () => {
  it('lists every person, showing an unset rate as an empty field', async () => {
    respond([IVAN, MARA], [])
    renderPanel()

    await waitFor(() =>
      expect(screen.getByLabelText('Tariffa oraria di Ivan')).toHaveValue(80),
    )
    // `null` is "no rate", and an empty input is the only honest rendering of it: a 0
    // here would claim the hour is worth nothing, which is a different statement.
    expect(screen.getByLabelText('Tariffa oraria di Mara')).toHaveValue(null)
    expect(screen.getByLabelText('Costo orario di Mara')).toHaveValue(null)
  })

  /**
   * The sentence that makes this screen safe to use, in front of both tables. A rate is
   * copied onto an hours row when the row is written, so nothing edited here moves a
   * figure already reported; if this copy ever goes missing, the screen starts reading
   * like a retroactive rewrite of every invoice and report that used the old rate.
   */
  it('says in both tables that a rate change does not touch hours already recorded', async () => {
    respond([IVAN], [ACME])
    renderPanel()

    await waitFor(() =>
      expect(
        screen.getAllByText(/non modifica nessuna voce di ore già registrata/i),
      ).toHaveLength(2),
    )
    expect(screen.getAllByText(/serve il ricalcolo/i)).toHaveLength(2)
  })

  /**
   * Both keys, every time, and a cleared rate spelled `null` rather than omitted. The
   * server reads `UserRatesUpdate` with `exclude_unset`, so an omitted key means
   * "leave it as it was": omitting the cleared one would leave the old rate in force
   * while the screen showed the field as empty -- the same regression the entity forms
   * have already had to fix twice.
   */
  it('sends both rates on every save, with a cleared one as an explicit null', async () => {
    respond([IVAN], [])
    vi.mocked(api.PUT).mockImplementation(() => ok(undefined, 204))
    renderPanel()

    const rateValue = await screen.findByLabelText('Tariffa oraria di Ivan')
    await userEvent.clear(rateValue)
    await userEvent.click(within(rowOf(rateValue)).getByRole('button', { name: 'Salva' }))

    await waitFor(() => expect(api.PUT).toHaveBeenCalled())
    expect(api.PUT).toHaveBeenCalledWith('/api/users/{user_id}/rates', {
      params: { path: { user_id: 'u1' } },
      body: { default_hourly_rate: null, default_hourly_cost: '35.000000' },
    })
    expect(bodyOfCall(0)).toHaveProperty('default_hourly_rate', null)
  })

  /**
   * Zero is a rate. A deal billed at 0 has decided its hours are free, and that
   * decision belongs in the table where it can be seen and changed -- not in the
   * "add a rate" picker, which is for deals that have never had one. A truthiness
   * test instead of `!== null` puts it in the wrong half of the screen.
   */
  it('treats a zero deal rate as a rate, not as the absence of one', async () => {
    respond([], [ACME, PRO_BONO, WITHOUT])
    renderPanel()

    await waitFor(() =>
      expect(screen.getByLabelText('Tariffa oraria del deal Pro bono ONLUS')).toHaveValue(0),
    )
    await userEvent.click(screen.getByRole('combobox', { name: /aggiungi una tariffa/i }))
    expect(screen.getByRole('option', { name: 'Restyling Bianchi' })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: 'Pro bono ONLUS' })).not.toBeInTheDocument()
  })

  /**
   * The deal rate is an override, on the deal's own endpoint, and writes nothing to
   * any person: that is the whole point of it -- agreeing a price with one client
   * without touching what anybody's hour is worth everywhere else.
   */
  it('writes a deal rate to the deal alone, never to a person', async () => {
    respond([IVAN], [ACME])
    vi.mocked(api.PUT).mockImplementation(() => ok(undefined, 204))
    renderPanel()

    const rate = await screen.findByLabelText('Tariffa oraria del deal Sito Acme')
    await userEvent.clear(rate)
    await userEvent.type(rate, '150')
    await userEvent.click(within(rowOf(rate)).getByRole('button', { name: 'Salva' }))

    await waitFor(() =>
      expect(api.PUT).toHaveBeenCalledWith('/api/deals/{deal_id}/rate', {
        params: { path: { deal_id: 'd1' } },
        body: { hourly_rate: '150' },
      }),
    )
    expect(api.PUT).toHaveBeenCalledTimes(1)
    expect(screen.getByLabelText('Tariffa oraria di Ivan')).toHaveValue(80)
  })

  it('sets a rate on a deal that has none through the picker', async () => {
    respond([], [WITHOUT])
    vi.mocked(api.PUT).mockImplementation(() => ok(undefined, 204))
    renderPanel()

    await userEvent.click(await screen.findByRole('combobox', { name: /aggiungi una tariffa/i }))
    await userEvent.click(screen.getByRole('option', { name: 'Restyling Bianchi' }))
    await userEvent.type(screen.getByLabelText('Tariffa (€/h)'), '90')
    await userEvent.click(screen.getByRole('button', { name: 'Imposta' }))

    await waitFor(() =>
      expect(api.PUT).toHaveBeenCalledWith('/api/deals/{deal_id}/rate', {
        params: { path: { deal_id: 'd3' } },
        body: { hourly_rate: '90' },
      }),
    )
  })

  /**
   * An empty rate means "no rate", so submitting the picker with one empty would send
   * `null` -- removing a rate from a button that offers to add one. Disabling it is
   * how that contradiction stays impossible.
   */
  it('refuses to submit the picker until both a deal and a rate are chosen', async () => {
    respond([], [WITHOUT])
    renderPanel()

    expect(await screen.findByRole('button', { name: 'Imposta' })).toBeDisabled()
    await userEvent.click(screen.getByRole('combobox', { name: /aggiungi una tariffa/i }))
    await userEvent.click(screen.getByRole('option', { name: 'Restyling Bianchi' }))
    expect(screen.getByRole('button', { name: 'Imposta' })).toBeDisabled()
    expect(api.PUT).not.toHaveBeenCalled()
  })

  /**
   * A row has two identical-looking inputs and the page has one banner, above every
   * other row: "Input should be greater than or equal to 0" up there is true and
   * useless. The server already said which field it refused, so the message goes on
   * that field -- and `aria-invalid` is the only part of it a screen reader can use.
   */
  it('marks the input a 422 names, and leaves the other one alone', async () => {
    respond([IVAN], [])
    vi.mocked(api.PUT).mockImplementation(() =>
      failed(
        {
          detail: [
            {
              type: 'greater_than_equal',
              loc: ['body', 'default_hourly_cost'],
              msg: 'Input should be greater than or equal to 0',
            },
          ],
        },
        422,
      ),
    )
    renderPanel()

    const cost = await screen.findByLabelText('Costo orario di Ivan')
    await userEvent.clear(cost)
    await userEvent.type(cost, '-5')
    await userEvent.click(within(rowOf(cost)).getByRole('button', { name: 'Salva' }))

    expect(
      await screen.findByText('Input should be greater than or equal to 0'),
    ).toBeInTheDocument()
    expect(cost).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByLabelText('Tariffa oraria di Ivan')).toHaveAttribute(
      'aria-invalid',
      'false',
    )
  })

  /** A refusal with no field to blame has nowhere to go but the page banner: hiding it
   *  on a row would leave a save that did nothing look like a save that worked. */
  it('puts a failure with no field on the page banner', async () => {
    respond([IVAN], [])
    vi.mocked(api.PUT).mockImplementation(() =>
      failed(
        {
          type: 'https://pigrocrm.dev/errors/permission_denied',
          title: 'Permesso negato',
          status: 403,
          detail: 'update_user_rates requires one of [admin], actor has collaboratore',
          code: 'permission_denied',
        },
        403,
      ),
    )
    renderPanel()

    const rateValue = await screen.findByLabelText('Tariffa oraria di Ivan')
    await userEvent.click(within(rowOf(rateValue)).getByRole('button', { name: 'Salva' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/requires one of \[admin\]/)
    expect(rateValue).toHaveAttribute('aria-invalid', 'false')
  })

  /**
   * The banner is cleared before every attempt. Without this the retry that succeeds
   * prints "Tariffe aggiornate" under a red alert still claiming the save was refused,
   * and nothing on screen says which of the two is current.
   */
  it('clears the previous failure when the retry succeeds', async () => {
    respond([IVAN], [])
    vi.mocked(api.PUT).mockImplementationOnce(() =>
      failed({ detail: 'periodo chiuso', code: 'period_closed' }, 409),
    )
    renderPanel()

    const rateValue = await screen.findByLabelText('Tariffa oraria di Ivan')
    const save = within(rowOf(rateValue)).getByRole('button', { name: 'Salva' })
    await userEvent.click(save)
    expect(await screen.findByRole('alert')).toHaveTextContent('periodo chiuso')

    vi.mocked(api.PUT).mockImplementation(() => ok(undefined, 204))
    await userEvent.click(save)
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
  })

  it('says plainly when no deal has a rate of its own', async () => {
    respond([IVAN], [WITHOUT])
    renderPanel()

    expect(
      await screen.findByText(/Nessun deal ha una tariffa propria/i),
    ).toBeInTheDocument()
  })

  it('renders a failed load as an alert instead of an empty screen', async () => {
    vi.mocked(api.GET).mockImplementation((path: string) => {
      if (path === '/api/users') return failed({ detail: 'database non raggiungibile' }, 503)
      return ok({ items: [], next_cursor: null })
    })
    renderPanel()

    expect(await screen.findByRole('alert')).toHaveTextContent('database non raggiungibile')
    expect(screen.queryByText('Tariffe delle persone')).not.toBeInTheDocument()
  })

  /**
   * REB-1057: four columns of inputs scrolled sideways on a phone and took «Salva» off the
   * screen, so below 768px a person is a card: the name, each field under its label, «Salva».
   */
  it('draws a person as a card on a phone, with Salva under the fields and the same save', async () => {
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: query.includes('max-width: 767px'),
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    }))
    try {
      respond([IVAN], [])
      vi.mocked(api.PUT).mockReturnValue(ok(IVAN))
      renderPanel()

      const rate = await screen.findByLabelText('Tariffa oraria di Ivan')
      expect(screen.queryByRole('table')).not.toBeInTheDocument()
      const card = rate.closest('li')
      if (!card) throw new Error('the rate is not in a card')
      expect(within(card).getByText('Ivan')).toBeInTheDocument()
      expect(within(card).getByText('Costo orario (€/h)')).toBeInTheDocument()

      await userEvent.click(within(card).getByRole('button', { name: 'Salva' }))
      await waitFor(() => expect(api.PUT).toHaveBeenCalledTimes(1))
      expect(bodyOfCall(0)).toEqual({
        default_hourly_rate: '80.000000',
        default_hourly_cost: '35.000000',
      })
    } finally {
      vi.unstubAllGlobals()
    }
  })
})
