import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent, { type UserEvent } from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { CeilingPanel } from './CeilingPanel'

// See `FiscalPanel.test.tsx`: the module is mocked rather than the network.
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn() } }
})

const REVENUE = {
  id: 'soglia_ricavi',
  label: 'Soglia di ricavi e compensi del regime forfettario',
  threshold: '85000.00',
  consequence: 'leaves_next_year',
  revenue: '60000.00',
  remaining: '25000.00',
  exceeded: false,
  alert_level: null,
  usage: 0.7059,
}
const FUORIUSCITA = {
  id: 'soglia_fuoriuscita_immediata',
  label: 'Soglia di fuoriuscita immediata dal regime forfettario',
  threshold: '100000.00',
  consequence: 'leaves_immediately',
  revenue: '60000.00',
  remaining: '40000.00',
  exceeded: false,
  alert_level: null,
  usage: 0.6,
}

const headroom = (thresholds: unknown[] = [REVENUE, FUORIUSCITA]) => ({
  year: 2026,
  legal_entity_id: 'a1',
  pack_id: 'it-flat-rate',
  pack_version: '1',
  thresholds,
})

const simulation = (overrides: Record<string, unknown> = {}) => ({
  year: 2026,
  legal_entity_id: 'a1',
  pack_id: 'it-flat-rate',
  pack_version: '1',
  brief_addition: '20000.00',
  thresholds: [
    {
      id: 'soglia_ricavi',
      label: REVENUE.label,
      threshold: '85000.00',
      consequence: 'leaves_next_year',
      current_revenue: '60000.00',
      current_remaining: '25000.00',
      current_usage: 0.7059,
      simulated_usage: 0.9412,
      simulated_revenue: '80000.00',
      simulated_remaining: '5000.00',
      falls_within: true,
      alert_level_simulated: 'In avvicinamento alla soglia di ricavi',
    },
    {
      id: 'soglia_fuoriuscita_immediata',
      label: FUORIUSCITA.label,
      threshold: '100000.00',
      consequence: 'leaves_immediately',
      current_revenue: '60000.00',
      current_remaining: '40000.00',
      current_usage: 0.6,
      simulated_usage: 0.8,
      simulated_revenue: '80000.00',
      simulated_remaining: '20000.00',
      falls_within: true,
      alert_level_simulated: null,
    },
  ],
  ...overrides,
})

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) }) as never
}

function failed(error: unknown, status: number) {
  return Promise.resolve({ error, response: new Response(null, { status }) }) as never
}

/** The contracts list is asked by every panel, for the contracts that cap a customer's
 *  share: an azienda with none is the default, so only the tests about caps say otherwise. */
function route(handlers: Record<string, () => unknown>) {
  const all: Record<string, () => unknown> = {
    '/api/contracts': () => ok({ items: [], next_cursor: null }),
    ...handlers,
  }
  vi.mocked(api.GET).mockImplementation(((path: string) => {
    const handler = all[path]
    if (!handler) throw new Error(`unexpected GET ${path}`)
    return handler()
  }) as never)
}

function renderPanel(props: Partial<React.ComponentProps<typeof CeilingPanel>> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <CeilingPanel year={2026} {...props} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
})

describe('CeilingPanel, the room left', () => {
  it('shows, per ceiling, what was collected, what the ceiling is and what is left', async () => {
    route({ '/api/analytics/ceilings': () => ok(headroom()) })
    renderPanel()

    const first = await screen.findByRole('group', { name: REVENUE.label })
    expect(within(first).getByText(/Incassati 60\.000,00 € su 85\.000,00 €/)).toBeInTheDocument()
    expect(within(first).getByText('restano 25.000,00 €')).toBeInTheDocument()
    expect(
      within(first).getByText('Se la superi, si esce dal regime forfettario dall’anno successivo.'),
    ).toBeInTheDocument()
    const second = screen.getByRole('group', { name: FUORIUSCITA.label })
    expect(within(second).getByText('restano 40.000,00 €')).toBeInTheDocument()
    expect(
      within(second).getByText('Se la superi, si esce dal regime forfettario subito, nell’anno stesso.'),
    ).toBeInTheDocument()
    expect(api.GET).toHaveBeenCalledWith('/api/analytics/ceilings', {
      params: { query: { year: 2026 } },
    })
  })

  it('draws each bar from the share the server computed', async () => {
    route({ '/api/analytics/ceilings': () => ok(headroom()) })
    renderPanel()

    const first = await screen.findByRole('group', { name: REVENUE.label })
    expect(within(first).getByTestId('bar-fill')).toHaveStyle({ width: '70.59%' })
  })

  it('says a crossed ceiling is crossed, by how much, and never prints a minus', async () => {
    route({
      '/api/analytics/ceilings': () =>
        ok(
          headroom([
            {
              ...REVENUE,
              revenue: '120000.00',
              remaining: '-35000.00',
              exceeded: true,
              alert_level: 'Soglia di ricavi raggiunta',
              usage: 1.4118,
            },
          ]),
        ),
    })
    renderPanel()

    const group = await screen.findByRole('group', { name: REVENUE.label })
    expect(within(group).getByText('superata di 35.000,00 €')).toBeInTheDocument()
    expect(
      within(group).getByText('Superata: si esce dal regime forfettario dall’anno successivo.'),
    ).toBeInTheDocument()
    expect(within(group).queryByText(/Se la superi/)).not.toBeInTheDocument()
    expect(within(group).getByText('Superata')).toBeInTheDocument()
    expect(group.textContent).not.toContain('-35')
    // A ratio above one is clamped by the bar, not drawn past its track.
    expect(within(group).getByTestId('bar-fill')).toHaveStyle({ width: '100.00%' })
  })

  it('names the alert level the server reached', async () => {
    route({
      '/api/analytics/ceilings': () =>
        ok(headroom([{ ...REVENUE, alert_level: 'In avvicinamento alla soglia di ricavi' }])),
    })
    renderPanel()

    expect(await screen.findByText('In avvicinamento alla soglia di ricavi')).toBeInTheDocument()
  })

  it('draws nothing for an azienda the forfettario ceilings do not apply to', async () => {
    route({ '/api/analytics/ceilings': () => ok(headroom([])) })
    const { container } = renderPanel()

    await waitFor(() => expect(api.GET).toHaveBeenCalled())
    await waitFor(() => expect(container).toBeEmptyDOMElement())
    expect(screen.queryByText(/soglie del forfettario/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/ci sta/i)).not.toBeInTheDocument()
  })

  it('draws nothing, and no prompt of its own, when there is no fiscal profile yet', async () => {
    route({
      '/api/analytics/ceilings': () => failed({ detail: 'fiscal_profile not found' }, 404),
    })
    const { container } = renderPanel()

    await waitFor(() => expect(api.GET).toHaveBeenCalled())
    await waitFor(() => expect(container).toBeEmptyDOMElement())
  })

  it('shows the server\'s own words on any other refusal', async () => {
    route({ '/api/analytics/ceilings': () => failed({ detail: 'Servizio non disponibile' }, 500) })
    renderPanel()

    expect(await screen.findByText(/servizio non disponibile/i)).toBeInTheDocument()
  })

  it('asks for the azienda it was given, and names it in the heading', async () => {
    route({ '/api/analytics/ceilings': () => ok(headroom()) })
    renderPanel({ legalEntityId: 'a2', title: 'Studio Rossi' })

    expect(await screen.findByText('Soglie del forfettario 2026 · Studio Rossi')).toBeInTheDocument()
    expect(api.GET).toHaveBeenCalledWith('/api/analytics/ceilings', {
      params: { query: { year: 2026, legal_entity_id: 'a2' } },
    })
  })
})

describe('CeilingPanel, «ci sta?»', () => {
  async function open() {
    route({
      '/api/analytics/ceilings': () => ok(headroom()),
      '/api/analytics/ceilings/simulate': () => ok(simulation()),
    })
    renderPanel()
    await screen.findByRole('group', { name: REVENUE.label })
    return userEvent.setup()
  }

  it('sends the typed amount as the decimal the API reads, for the year of the date', async () => {
    const user = await open()

    await user.type(screen.getByLabelText('Importo'), '20.000,00')
    await user.clear(screen.getByLabelText('Incasso previsto il'))
    await user.type(screen.getByLabelText('Incasso previsto il'), '2027-03-15')
    await user.click(screen.getByRole('button', { name: 'Simula' }))

    await waitFor(() =>
      expect(api.GET).toHaveBeenCalledWith('/api/analytics/ceilings/simulate', {
        params: { query: { budgeted_value: '20000.00', expected_date: '2027-03-15', year: 2027 } },
      }),
    )
  })

  it('says it fits, ceiling by ceiling, with what is left', async () => {
    const user = await open()

    await user.type(screen.getByLabelText('Importo'), '20000')
    await user.click(screen.getByRole('button', { name: 'Simula' }))

    expect(await screen.findByText('Ci sta.')).toBeInTheDocument()
    expect(screen.getByText(/Aggiungendo 20\.000,00 € ai ricavi incassati del 2026/)).toBeInTheDocument()
    expect(screen.getByText(/60\.000,00 € diventano 80\.000,00 €: restano 5\.000,00 €/)).toBeInTheDocument()
    expect(screen.getAllByText('Rientra')).toHaveLength(2)
  })

  it('says it does not fit when any ceiling would be crossed, and what crossing means', async () => {
    route({
      '/api/analytics/ceilings': () => ok(headroom()),
      '/api/analytics/ceilings/simulate': () =>
        ok(
          simulation({
            thresholds: [
              {
                ...simulation().thresholds[0],
                simulated_revenue: '90000.00',
                simulated_remaining: '-5000.00',
                simulated_usage: 1.0588,
                falls_within: false,
                alert_level_simulated: 'Soglia di ricavi raggiunta',
              },
            ],
          }),
        ),
    })
    renderPanel()
    await screen.findByRole('group', { name: REVENUE.label })
    const user = userEvent.setup()

    await user.type(screen.getByLabelText('Importo'), '30000')
    await user.click(screen.getByRole('button', { name: 'Simula' }))

    expect(await screen.findByText('Non ci sta.')).toBeInTheDocument()
    expect(screen.getByText('Non rientra')).toBeInTheDocument()
    expect(screen.getByText(/superata di 5\.000,00 €/)).toBeInTheDocument()
    expect(
      screen.getByText('Superata: si esce dal regime forfettario dall’anno successivo.'),
    ).toBeInTheDocument()
  })

  it('asks the server again when «Simula» is pressed again with the same inputs', async () => {
    const user = await open()

    await user.type(screen.getByLabelText('Importo'), '20000')
    await user.click(screen.getByRole('button', { name: 'Simula' }))
    await screen.findByText('Ci sta.')
    await user.click(screen.getByRole('button', { name: 'Simula' }))

    await waitFor(() =>
      expect(
        vi
          .mocked(api.GET)
          .mock.calls.filter((call) => (call as unknown[])[0] === '/api/analytics/ceilings/simulate'),
      ).toHaveLength(2),
    )
  })

  it('drops a verdict the moment one of the inputs it answered changes', async () => {
    const user = await open()

    await user.type(screen.getByLabelText('Importo'), '20000')
    await user.click(screen.getByRole('button', { name: 'Simula' }))
    expect(await screen.findByText('Ci sta.')).toBeInTheDocument()

    await user.type(screen.getByLabelText('Importo'), '0')
    expect(screen.queryByText('Ci sta.')).not.toBeInTheDocument()
    expect(screen.queryByText('Rientra')).not.toBeInTheDocument()
  })

  it('keeps the verdict in a live region that is in the page before it has one', async () => {
    const user = await open()

    const status = screen.getByRole('status')
    expect(status).toBeEmptyDOMElement()
    await user.type(screen.getByLabelText('Importo'), '20000')
    await user.click(screen.getByRole('button', { name: 'Simula' }))
    expect(await within(status).findByText('Ci sta.')).toBeInTheDocument()
  })

  it('sends hours and a rate when there is no amount', async () => {
    const user = await open()

    await user.type(screen.getByLabelText('oppure Ore'), '100')
    await user.type(screen.getByLabelText('per Tariffa oraria'), '80,5')
    await user.click(screen.getByRole('button', { name: 'Simula' }))

    await waitFor(() =>
      expect(api.GET).toHaveBeenCalledWith('/api/analytics/ceilings/simulate', {
        params: {
          query: {
            budgeted_hours: '100',
            hourly_rate: '80.5',
            expected_date: '2026-12-31',
            year: 2026,
          },
        },
      }),
    )
  })

  it('asks nothing, and says why, while the estimate is missing or not a number', async () => {
    const user = await open()

    await user.click(screen.getByRole('button', { name: 'Simula' }))
    expect(await screen.findByText(/scrivi un importo, oppure le ore/i)).toBeInTheDocument()

    await user.type(screen.getByLabelText('Importo'), 'tanto')
    await user.click(screen.getByRole('button', { name: 'Simula' }))
    expect(await screen.findByText(/scrivi un numero, per esempio 12\.000,50/)).toBeInTheDocument()

    expect(api.GET).not.toHaveBeenCalledWith('/api/analytics/ceilings/simulate', expect.anything())
  })

  it('refuses a date the API has no year for before it asks', async () => {
    const user = await open()

    await user.type(screen.getByLabelText('Importo'), '1000')
    await user.clear(screen.getByLabelText('Incasso previsto il'))
    await user.click(screen.getByRole('button', { name: 'Simula' }))

    expect(await screen.findByText(/scegli una data tra il 2000 e il 2200/i)).toBeInTheDocument()
    expect(api.GET).not.toHaveBeenCalledWith('/api/analytics/ceilings/simulate', expect.anything())
  })

  it('shows the server\'s refusal of a simulation in place', async () => {
    route({
      '/api/analytics/ceilings': () => ok(headroom()),
      '/api/analytics/ceilings/simulate': () => failed({ detail: 'Stima non valida' }, 422),
    })
    renderPanel()
    await screen.findByRole('group', { name: REVENUE.label })
    const user = userEvent.setup()

    await user.type(screen.getByLabelText('Importo'), '10')
    await user.click(screen.getByRole('button', { name: 'Simula' }))

    expect(await screen.findByText(/stima non valida/i)).toBeInTheDocument()
  })
})

describe('CeilingPanel, simulating a deal', () => {
  const DEAL = {
    id: 'd1',
    nome: 'Sito vetrina',
    customer_legal_name: 'Acme Srl',
    pipeline_stage_id: 's-open',
    budgeted_value: '12000.00',
    budgeted_hours: null,
    hourly_rate: null,
  }
  const NO_ESTIMATE = { ...DEAL, id: 'd2', nome: 'Idea', budgeted_value: null }
  const WON = { ...DEAL, id: 'd3', nome: 'Già vinto', pipeline_stage_id: 's-won' }

  function routeWithDeals() {
    route({
      '/api/analytics/ceilings': () => ok(headroom()),
      '/api/analytics/ceilings/simulate': () => ok(simulation({ brief_addition: '12000.00' })),
      '/api/deals': () => ok({ items: [DEAL, NO_ESTIMATE, WON], next_cursor: null }),
      '/api/pipeline-stages': () =>
        ok([
          { id: 's-open', nome: 'Trattativa', type: 'open' },
          { id: 's-won', nome: 'Vinto', type: 'won' },
        ]),
    })
  }

  it('offers the open deals only, and sends the estimate the chosen deal carries', async () => {
    routeWithDeals()
    renderPanel()
    await screen.findByRole('group', { name: REVENUE.label })
    const user = userEvent.setup()

    await user.click(screen.getByRole('radio', { name: 'Un deal' }))
    await user.click(await screen.findByRole('combobox', { name: 'Deal' }))
    expect(screen.queryByRole('option', { name: /già vinto/i })).not.toBeInTheDocument()
    expect(screen.getByRole('option', { name: /idea.*senza stima/i })).toHaveAttribute(
      'aria-disabled',
      'true',
    )
    await user.click(screen.getByRole('option', { name: /sito vetrina · acme srl/i }))
    expect(screen.getByText('Stima del deal: 12.000,00 €')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Simula' }))

    await waitFor(() =>
      expect(api.GET).toHaveBeenCalledWith('/api/analytics/ceilings/simulate', {
        params: { query: { budgeted_value: '12000.00', expected_date: '2026-12-31', year: 2026 } },
      }),
    )
    expect(await screen.findByText('Ci sta.')).toBeInTheDocument()
  })

  it('says so when the pipeline stages cannot be read, instead of waiting for them', async () => {
    route({
      '/api/analytics/ceilings': () => ok(headroom()),
      '/api/deals': () => ok({ items: [DEAL], next_cursor: null }),
      '/api/pipeline-stages': () => failed({ detail: 'Stadi non disponibili' }, 500),
    })
    renderPanel()
    await screen.findByRole('group', { name: REVENUE.label })
    const user = userEvent.setup()

    await user.click(screen.getByRole('radio', { name: 'Un deal' }))

    expect(await screen.findByText(/stadi non disponibili/i)).toBeInTheDocument()
  })

  it('asks for a deal before it asks the server anything', async () => {
    routeWithDeals()
    renderPanel()
    await screen.findByRole('group', { name: REVENUE.label })
    const user = userEvent.setup()

    await user.click(screen.getByRole('radio', { name: 'Un deal' }))
    await screen.findByRole('combobox', { name: 'Deal' })
    await user.click(screen.getByRole('button', { name: 'Simula' }))

    expect(await screen.findByText('Scegli un deal.')).toBeInTheDocument()
    expect(api.GET).not.toHaveBeenCalledWith('/api/analytics/ceilings/simulate', expect.anything())
  })
})

describe('CeilingPanel, one per azienda in «Tutte»', () => {
  it('gives each simulator its own field ids, so a label reaches its own input', async () => {
    route({ '/api/analytics/ceilings': () => ok(headroom()) })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <CeilingPanel year={2026} legalEntityId="a1" title="Studio Rossi" />
        <CeilingPanel year={2026} legalEntityId="a2" title="Rossi Ltd" />
      </QueryClientProvider>,
    )
    await screen.findByText('Soglie del forfettario 2026 · Rossi Ltd')
    const user = userEvent.setup()

    const amounts = screen.getAllByLabelText('Importo')
    expect(amounts).toHaveLength(2)
    expect(amounts[0]!.id).not.toBe(amounts[1]!.id)
    await user.click(screen.getAllByText('Importo')[1]!)
    expect(amounts[1]).toHaveFocus()
  })
})

describe('CeilingPanel, the cap on a customer’s share', () => {
  const CAPPED = {
    id: 'k1',
    title: 'Piattaforma ACME',
    status: 'active',
    legal_entity_id: 'a1',
    currency: 'EUR',
    customer_share_cap: '50.00',
  }
  const UNCAPPED = { ...CAPPED, id: 'k2', title: 'Manutenzione Beta', customer_share_cap: null }
  const contracts = (...items: unknown[]) => () => ok({ items, next_cursor: null })

  const CAP = {
    contract_id: 'k1',
    title: 'Piattaforma ACME',
    customer_id: 'cu1',
    currency: 'EUR',
    period_from: '2026-03-01',
    period_to: '2027-02-28',
    cap: 0.5,
    current_customer_revenue: '30000.00',
    current_total_revenue: '100000.00',
    current_share: 0.3,
    simulated_customer_revenue: '50000.00',
    simulated_total_revenue: '120000.00',
    simulated_share: 0.5333,
    falls_within: false,
  }

  async function chooseContract(user: UserEvent) {
    await user.click(await screen.findByRole('combobox', { name: 'Contratto con un tetto' }))
    await user.click(screen.getByRole('option', { name: /piattaforma acme/i }))
  }

  it('draws only the simulator, under its own heading, for an azienda with no ceilings but a capped contract', async () => {
    route({
      '/api/analytics/ceilings': () => ok(headroom([])),
      '/api/contracts': contracts(CAPPED, UNCAPPED),
    })
    renderPanel()

    expect(await screen.findByText('Tetti dei contratti')).toBeInTheDocument()
    expect(screen.queryByText(/soglie del forfettario/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('group', { name: REVENUE.label })).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Ci sta?' })).toBeInTheDocument()
    expect(api.GET).toHaveBeenCalledWith('/api/contracts', {
      params: { query: expect.objectContaining({ status: 'active' }) },
    })

    const user = userEvent.setup()
    await user.click(screen.getByRole('combobox', { name: 'Contratto con un tetto' }))
    expect(screen.getByRole('option', { name: /piattaforma acme · tetto 50%/i })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: /manutenzione beta/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('option', { name: 'Nessun contratto' })).not.toBeInTheDocument()
  })

  it('asks for a contract before it asks the server anything when there is nothing else to read', async () => {
    route({
      '/api/analytics/ceilings': () => ok(headroom([])),
      '/api/contracts': contracts(CAPPED),
    })
    renderPanel()
    const user = userEvent.setup()

    await user.type(await screen.findByLabelText('Importo'), '20000')
    await user.click(screen.getByRole('button', { name: 'Simula' }))

    expect(await screen.findByText('Scegli un contratto con un tetto.')).toBeInTheDocument()
    expect(api.GET).not.toHaveBeenCalledWith('/api/analytics/ceilings/simulate', expect.anything())
  })

  it('sends the contract and the date with the estimate, and shows the cap block alone', async () => {
    route({
      '/api/analytics/ceilings': () => ok(headroom([])),
      '/api/analytics/ceilings/simulate': () =>
        ok({ ...simulation({ thresholds: [] }), contract_cap: CAP }),
      '/api/contracts': contracts(CAPPED),
    })
    renderPanel()
    const user = userEvent.setup()

    await chooseContract(user)
    await user.type(screen.getByLabelText('Importo'), '20000')
    await user.click(screen.getByRole('button', { name: 'Simula' }))

    await waitFor(() =>
      expect(api.GET).toHaveBeenCalledWith('/api/analytics/ceilings/simulate', {
        params: {
          query: {
            budgeted_value: '20000',
            expected_date: '2026-12-31',
            contract_id: 'k1',
            year: 2026,
          },
        },
      }),
    )
    const block = await screen.findByRole('group', { name: 'Tetto di Piattaforma ACME' })
    expect(within(block).getByText(/anno dal 01\/03\/2026 al 28\/02\/2027/)).toBeInTheDocument()
    expect(within(block).getByText('Non ci sta')).toBeInTheDocument()
    expect(
      within(block).getByText('Il cliente passerebbe dal 30% al 53,33% dei ricavi, tetto 50%.'),
    ).toBeInTheDocument()
    expect(within(block).getByTestId('bar-fill')).toHaveStyle({ width: '53.33%' })
    expect(within(block).getByTestId('bar-mark')).toHaveStyle({ left: '50.00%' })
    expect(screen.getByText('Non ci sta.')).toBeInTheDocument()
    expect(screen.queryByText('Rientra')).not.toBeInTheDocument()
  })

  it('says the cap fits when the server says the share stays under it', async () => {
    route({
      '/api/analytics/ceilings': () => ok(headroom([])),
      '/api/analytics/ceilings/simulate': () =>
        ok({
          ...simulation({ thresholds: [] }),
          contract_cap: { ...CAP, simulated_share: 0.42, falls_within: true },
        }),
      '/api/contracts': contracts(CAPPED),
    })
    renderPanel()
    const user = userEvent.setup()

    await chooseContract(user)
    await user.type(screen.getByLabelText('Importo'), '20000')
    await user.click(screen.getByRole('button', { name: 'Simula' }))

    expect(await screen.findByText('Ci sta.')).toBeInTheDocument()
    expect(screen.getByText('Ci sta')).toBeInTheDocument()
  })

  it('does not say «Ci sta.» from the ceilings alone when the contract’s cap would be reached', async () => {
    route({
      '/api/analytics/ceilings': () => ok(headroom()),
      '/api/analytics/ceilings/simulate': () => ok({ ...simulation(), contract_cap: CAP }),
      '/api/contracts': contracts(CAPPED),
    })
    renderPanel()
    await screen.findByRole('group', { name: REVENUE.label })
    const user = userEvent.setup()

    // With ceilings the heading is the forfettario's and the contract is optional.
    expect(screen.getByText('Soglie del forfettario 2026')).toBeInTheDocument()
    await user.click(await screen.findByRole('combobox', { name: 'Contratto con un tetto' }))
    expect(screen.getByRole('option', { name: 'Nessun contratto' })).toBeInTheDocument()
    await user.click(screen.getByRole('option', { name: /piattaforma acme/i }))
    await user.type(screen.getByLabelText('Importo'), '20000')
    await user.click(screen.getByRole('button', { name: 'Simula' }))

    expect(await screen.findByText('Non ci sta.')).toBeInTheDocument()
    expect(screen.queryByText('Ci sta.')).not.toBeInTheDocument()
    expect(screen.getAllByText('Rientra')).toHaveLength(2)
    expect(screen.getByText('Non ci sta')).toBeInTheDocument()
  })

  it('simulates the ceilings alone when no contract is chosen, as before', async () => {
    route({
      '/api/analytics/ceilings': () => ok(headroom()),
      '/api/analytics/ceilings/simulate': () => ok(simulation()),
      '/api/contracts': contracts(CAPPED),
    })
    renderPanel()
    await screen.findByRole('group', { name: REVENUE.label })
    const user = userEvent.setup()

    await user.type(screen.getByLabelText('Importo'), '20000')
    await user.click(screen.getByRole('button', { name: 'Simula' }))

    expect(await screen.findByText('Ci sta.')).toBeInTheDocument()
    expect(api.GET).toHaveBeenCalledWith('/api/analytics/ceilings/simulate', {
      params: {
        query: { budgeted_value: '20000', expected_date: '2026-12-31', year: 2026 },
      },
    })
    expect(screen.queryByRole('group', { name: /^Tetto di/ })).not.toBeInTheDocument()
  })

  it('offers no contract picker when no active contract has a cap', async () => {
    route({
      '/api/analytics/ceilings': () => ok(headroom()),
      '/api/contracts': contracts(UNCAPPED),
    })
    renderPanel()
    await screen.findByRole('group', { name: REVENUE.label })

    expect(screen.queryByRole('combobox', { name: 'Contratto con un tetto' })).not.toBeInTheDocument()
  })

  it('draws nothing with no pack ceilings and no contract that caps a share', async () => {
    route({
      '/api/analytics/ceilings': () => ok(headroom([])),
      '/api/contracts': contracts(UNCAPPED),
    })
    const { container } = renderPanel()

    await waitFor(() => expect(api.GET).toHaveBeenCalledWith('/api/contracts', expect.anything()))
    await waitFor(() => expect(container).toBeEmptyDOMElement())
    expect(screen.queryByText('Tetti dei contratti')).not.toBeInTheDocument()
    expect(screen.queryByText(/ci sta/i)).not.toBeInTheDocument()
  })

  it('says the contracts could not be read beside the ceilings, instead of hiding the picker', async () => {
    route({
      '/api/analytics/ceilings': () => ok(headroom()),
      '/api/contracts': () => failed({ detail: 'Contratti non disponibili' }, 500),
    })
    renderPanel()

    expect(await screen.findByText(/Soglie del forfettario/)).toBeInTheDocument()
    expect(await screen.findByText(/contratti non disponibili/i)).toBeInTheDocument()
  })

  it('says why the contracts could not be read, when there is nothing else to show', async () => {
    route({
      '/api/analytics/ceilings': () => ok(headroom([])),
      '/api/contracts': () => failed({ detail: 'Contratti non disponibili' }, 500),
    })
    renderPanel()

    expect(await screen.findByText(/contratti non disponibili/i)).toBeInTheDocument()
  })
})
