import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { CeilingPanel } from './CeilingPanel'

// See `FiscalPanel.test.tsx`: the module is mocked rather than the network.
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn() } }
})

const RICAVI = {
  id: 'soglia_ricavi',
  etichetta: 'Soglia di ricavi e compensi del regime forfettario',
  soglia: '85000.00',
  conseguenza: 'esce_dall_anno_successivo',
  ricavi: '60000.00',
  residuo: '25000.00',
  superata: false,
  livello_allerta: null,
  utilizzo: 0.7059,
}
const FUORIUSCITA = {
  id: 'soglia_fuoriuscita_immediata',
  etichetta: 'Soglia di fuoriuscita immediata dal regime forfettario',
  soglia: '100000.00',
  conseguenza: 'esce_immediatamente',
  ricavi: '60000.00',
  residuo: '40000.00',
  superata: false,
  livello_allerta: null,
  utilizzo: 0.6,
}

const headroom = (soglie: unknown[] = [RICAVI, FUORIUSCITA]) => ({
  anno: 2026,
  azienda_id: 'a1',
  pack_id: 'it-flat-rate',
  pack_version: '1',
  soglie,
})

const simulation = (overrides: Record<string, unknown> = {}) => ({
  anno: 2026,
  azienda_id: 'a1',
  pack_id: 'it-flat-rate',
  pack_version: '1',
  aggiunta_sintetica: '20000.00',
  soglie: [
    {
      id: 'soglia_ricavi',
      etichetta: RICAVI.etichetta,
      soglia: '85000.00',
      conseguenza: 'esce_dall_anno_successivo',
      ricavi_attuali: '60000.00',
      residuo_attuale: '25000.00',
      utilizzo_attuale: 0.7059,
      utilizzo_simulato: 0.9412,
      ricavi_simulati: '80000.00',
      residuo_simulato: '5000.00',
      rientra: true,
      livello_allerta_simulato: 'In avvicinamento alla soglia di ricavi',
    },
    {
      id: 'soglia_fuoriuscita_immediata',
      etichetta: FUORIUSCITA.etichetta,
      soglia: '100000.00',
      conseguenza: 'esce_immediatamente',
      ricavi_attuali: '60000.00',
      residuo_attuale: '40000.00',
      utilizzo_attuale: 0.6,
      utilizzo_simulato: 0.8,
      ricavi_simulati: '80000.00',
      residuo_simulato: '20000.00',
      rientra: true,
      livello_allerta_simulato: null,
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

function route(handlers: Record<string, () => unknown>) {
  vi.mocked(api.GET).mockImplementation(((path: string) => {
    const handler = handlers[path]
    if (!handler) throw new Error(`unexpected GET ${path}`)
    return handler()
  }) as never)
}

function renderPanel(props: Partial<React.ComponentProps<typeof CeilingPanel>> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <CeilingPanel anno={2026} {...props} />
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

    const first = await screen.findByRole('group', { name: RICAVI.etichetta })
    expect(within(first).getByText(/Incassati 60\.000,00 € su 85\.000,00 €/)).toBeInTheDocument()
    expect(within(first).getByText('restano 25.000,00 €')).toBeInTheDocument()
    expect(
      within(first).getByText('Se la superi, si esce dal regime forfettario dall’anno successivo.'),
    ).toBeInTheDocument()
    const second = screen.getByRole('group', { name: FUORIUSCITA.etichetta })
    expect(within(second).getByText('restano 40.000,00 €')).toBeInTheDocument()
    expect(
      within(second).getByText('Se la superi, si esce dal regime forfettario subito, nell’anno stesso.'),
    ).toBeInTheDocument()
    expect(api.GET).toHaveBeenCalledWith('/api/analytics/ceilings', {
      params: { query: { anno: 2026 } },
    })
  })

  it('draws each bar from the share the server computed', async () => {
    route({ '/api/analytics/ceilings': () => ok(headroom()) })
    renderPanel()

    const first = await screen.findByRole('group', { name: RICAVI.etichetta })
    expect(within(first).getByTestId('bar-fill')).toHaveStyle({ width: '70.59%' })
  })

  it('says a crossed ceiling is crossed, by how much, and never prints a minus', async () => {
    route({
      '/api/analytics/ceilings': () =>
        ok(
          headroom([
            {
              ...RICAVI,
              ricavi: '120000.00',
              residuo: '-35000.00',
              superata: true,
              livello_allerta: 'Soglia di ricavi raggiunta',
              utilizzo: 1.4118,
            },
          ]),
        ),
    })
    renderPanel()

    const group = await screen.findByRole('group', { name: RICAVI.etichetta })
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
        ok(headroom([{ ...RICAVI, livello_allerta: 'In avvicinamento alla soglia di ricavi' }])),
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
    renderPanel({ aziendaId: 'a2', titolo: 'Studio Rossi' })

    expect(await screen.findByText('Soglie del forfettario 2026 · Studio Rossi')).toBeInTheDocument()
    expect(api.GET).toHaveBeenCalledWith('/api/analytics/ceilings', {
      params: { query: { anno: 2026, azienda_id: 'a2' } },
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
    await screen.findByRole('group', { name: RICAVI.etichetta })
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
        params: { query: { valore_preventivato: '20000.00', anno: 2027 } },
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
            soglie: [
              {
                ...simulation().soglie[0],
                ricavi_simulati: '90000.00',
                residuo_simulato: '-5000.00',
                utilizzo_simulato: 1.0588,
                rientra: false,
                livello_allerta_simulato: 'Soglia di ricavi raggiunta',
              },
            ],
          }),
        ),
    })
    renderPanel()
    await screen.findByRole('group', { name: RICAVI.etichetta })
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
        params: { query: { ore_preventivate: '100', tariffa_oraria: '80.5', anno: 2026 } },
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
    await screen.findByRole('group', { name: RICAVI.etichetta })
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
    customer_ragione_sociale: 'Acme Srl',
    pipeline_stage_id: 's-open',
    valore_preventivato: '12000.00',
    ore_preventivate: null,
    tariffa_oraria: null,
  }
  const NO_ESTIMATE = { ...DEAL, id: 'd2', nome: 'Idea', valore_preventivato: null }
  const WON = { ...DEAL, id: 'd3', nome: 'Già vinto', pipeline_stage_id: 's-won' }

  function routeWithDeals() {
    route({
      '/api/analytics/ceilings': () => ok(headroom()),
      '/api/analytics/ceilings/simulate': () => ok(simulation({ aggiunta_sintetica: '12000.00' })),
      '/api/deals': () => ok({ items: [DEAL, NO_ESTIMATE, WON], next_cursor: null }),
      '/api/pipeline-stages': () =>
        ok([
          { id: 's-open', nome: 'Trattativa', tipo: 'open' },
          { id: 's-won', nome: 'Vinto', tipo: 'won' },
        ]),
    })
  }

  it('offers the open deals only, and sends the estimate the chosen deal carries', async () => {
    routeWithDeals()
    renderPanel()
    await screen.findByRole('group', { name: RICAVI.etichetta })
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
        params: { query: { valore_preventivato: '12000.00', anno: 2026 } },
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
    await screen.findByRole('group', { name: RICAVI.etichetta })
    const user = userEvent.setup()

    await user.click(screen.getByRole('radio', { name: 'Un deal' }))

    expect(await screen.findByText(/stadi non disponibili/i)).toBeInTheDocument()
  })

  it('asks for a deal before it asks the server anything', async () => {
    routeWithDeals()
    renderPanel()
    await screen.findByRole('group', { name: RICAVI.etichetta })
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
        <CeilingPanel anno={2026} aziendaId="a1" titolo="Studio Rossi" />
        <CeilingPanel anno={2026} aziendaId="a2" titolo="Rossi Ltd" />
      </QueryClientProvider>,
    )
    await screen.findByText('Soglie del forfettario 2026 · Rossi Ltd')
    const user = userEvent.setup()

    const importi = screen.getAllByLabelText('Importo')
    expect(importi).toHaveLength(2)
    expect(importi[0]!.id).not.toBe(importi[1]!.id)
    await user.click(screen.getAllByText('Importo')[1]!)
    expect(importi[1]).toHaveFocus()
  })
})
