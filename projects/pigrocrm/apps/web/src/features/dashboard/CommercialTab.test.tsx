/**
 * §4's tab. Properties a snapshot test would not reach: the figures are rendered as the
 * strings the API sent, the weighted value is labelled a *stima* and never sits in the
 * same total as revenue, the period in the URL is the period actually requested, and a
 * failed request renders an error rather than an empty dashboard.
 *
 * `vi.mock('@/lib/api')`, not a stubbed `globalThis.fetch`: `api` is an openapi-fetch
 * client built at import time, so replacing `fetch` afterwards changes nothing.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { CommercialTab } from './CommercialTab'
import { api } from '@/lib/api'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PUT: vi.fn(), DELETE: vi.fn() } }
})

function ok(data: unknown) {
  return { data, response: new Response(null, { status: 200 }) } as never
}

function failed(error: unknown, status: number) {
  return { error, response: new Response(null, { status }) } as never
}

const RESPONSE = {
  periodo: { da: '2026-03-01', a: '2026-03-31' },
  calcolato_alle: '2026-03-15T10:00:00Z',
  pipeline: [
    {
      stage_id: 's1',
      stage_code: 'lead',
      stage_nome: 'Lead',
      stage_tipo: 'open',
      posizione: 0,
      numero: 4,
      valore_totale: '3000.00',
      senza_valore: 1,
      valore_ponderato: '610.00',
    },
    {
      stage_id: 's2',
      stage_code: 'offerta',
      stage_nome: 'Offerta',
      stage_tipo: 'open',
      posizione: 2,
      numero: 1,
      valore_totale: '500.00',
      senza_valore: 0,
      valore_ponderato: '250.00',
    },
    {
      stage_id: 's3',
      stage_code: 'vinto',
      stage_nome: 'Vinto',
      stage_tipo: 'won',
      posizione: 4,
      numero: 8,
      valore_totale: '9000.00',
      senza_valore: 0,
      valore_ponderato: '9000.00',
    },
    {
      stage_id: 's4',
      stage_code: 'perso',
      stage_nome: 'Perso',
      stage_tipo: 'lost',
      posizione: 5,
      numero: 2,
      valore_totale: '1000.00',
      senza_valore: 0,
      valore_ponderato: '0.00',
    },
  ],
  chiusure: { vinti: 3, persi: 1, valore_vinto: '15000.00', tasso_conversione: '75.00' },
  offerte_in_attesa: [
    {
      document_id: 'd1',
      titolo: 'Offerta impianti',
      deal_id: 'x',
      customer_id: null,
      stato_dal: '2026-02-01',
      giorni: 42,
    },
  ],
  offerte_in_attesa_totale: 1,
  chiusure_previste_30_giorni: 2,
  chiusure_non_attribuibili: 7,
  offerte_accettate_deal_non_vinto: 2,
}

const PERIODO = { da: '2026-03-01', a: '2026-03-31' }

function renderTab(periodo = PERIODO) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <CommercialTab periodo={periodo} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
})

describe('CommercialTab', () => {
  it('asks for the period it was given, so a shared link answers its own question', async () => {
    // The other half of the URL round trip: `periodo.test.ts` proves the search params are
    // read back, this proves they reach the request instead of being decoration.
    vi.mocked(api.GET).mockResolvedValue(ok(RESPONSE))
    renderTab({ da: '2025-01-01', a: '2025-12-31' })
    await screen.findByRole('table', { name: /pipeline per stato/i })
    expect(api.GET).toHaveBeenCalledWith('/api/dashboard/sales', {
      params: { query: { da: '2025-01-01', a: '2025-12-31' } },
    })
  })

  it('renders the pipeline as a table with the values the API sent', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(RESPONSE))
    renderTab()
    const table = await screen.findByRole('table', { name: /pipeline per stato/i })
    expect(within(table).getByRole('rowheader', { name: 'Lead' })).toBeInTheDocument()
    expect(within(table).getByRole('rowheader', { name: 'Offerta' })).toBeInTheDocument()
  })

  it('formats every money figure from the API string, cents included', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(RESPONSE))
    renderTab()
    expect(await screen.findByText('15.000,00 €')).toBeInTheDocument()
  })

  it('shows the conversion rate, and shows a dash rather than 0% when it is null', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(RESPONSE))
    const { unmount } = renderTab()
    expect(await screen.findByText('75,00%')).toBeInTheDocument()
    unmount()

    vi.mocked(api.GET).mockResolvedValue(
      ok({ ...RESPONSE, chiusure: { ...RESPONSE.chiusure, tasso_conversione: null } }),
    )
    renderTab()
    // "0%" would say "I lost everything"; null says "nothing closed". Different facts.
    expect(await screen.findByText('—')).toBeInTheDocument()
    expect(screen.queryByText('0,00%')).not.toBeInTheDocument()
  })

  it('declares the deals that cannot be attributed to a period', async () => {
    // §4.1: `chiuso_il` is not backfilled, so the dashboard says so instead of counting
    // those rows as zero or putting them in the wrong month.
    vi.mocked(api.GET).mockResolvedValue(ok(RESPONSE))
    renderTab()
    expect(await screen.findByText(/7 deal chiusi prima/i)).toBeInTheDocument()
    expect(screen.getByText(/non sono attribuibili/i)).toBeInTheDocument()
  })

  it('says nothing about unattributable deals when there are none', async () => {
    // A permanent "0 deal chiusi prima..." would be a caveat about nothing, printed for
    // every user forever.
    vi.mocked(api.GET).mockResolvedValue(ok({ ...RESPONSE, chiusure_non_attribuibili: 0 }))
    renderTab()
    await screen.findByRole('table', { name: /pipeline per stato/i })
    expect(screen.queryByText(/non sono attribuibili/i)).not.toBeInTheDocument()
  })

  it('draws every stage, the closed ones last and behind a rule', async () => {
    // The card is «Pipeline per stato» since 2026-09-09: a list that stopped at the last
    // open stage never said where the work ended up. The order is open stages in their
    // `posizione` order, then the separator, then the closed ones -- grouped by
    // `stage_tipo` and never by `stage_nome`, which the user may rename.
    vi.mocked(api.GET).mockResolvedValue(ok(RESPONSE))
    renderTab()
    const table = await screen.findByRole('table', { name: /pipeline per stato/i })
    const labels = within(table)
      .getAllByRole('rowheader')
      .map((cell) => cell.textContent)
    expect(labels).toEqual(['Lead', 'Offerta', 'Vinto', 'Perso'])
    const separated = table.querySelectorAll('[data-separator="true"]')
    expect(separated).toHaveLength(1)
    expect(separated[0]).toHaveTextContent('Vinto')
    // The hairline the reader sees, not only the attribute the query found it by.
    expect(separated[0]).toHaveClass('border-t')
  })

  it('scales every bar against the widest stage, closed ones included', async () => {
    // Vinto (8) is the widest row, and it is a closed one. Scaling the open stages
    // against the widest *open* stage would draw a four-deal Lead as a full bar beside an
    // eight-deal Vinto, which is a chart that lies about the two it puts side by side.
    vi.mocked(api.GET).mockResolvedValue(ok(RESPONSE))
    renderTab()
    await screen.findByRole('table', { name: /pipeline per stato/i })
    const bars = screen.getAllByTestId('bar-fill')
    expect(bars[0]).toHaveStyle({ width: '50.00%' })
    expect(bars[2]).toHaveStyle({ width: '100.00%' })
  })

  it('paints the two closed stages in ink and in Watermelon, not in a chart hue', async () => {
    // Won is the ink and lost is the destructive tint the `destructive` badge already
    // uses, so the two ends of the pipeline read as outcomes rather than as two more
    // stages in the sequence. No sixth colour enters the palette.
    vi.mocked(api.GET).mockResolvedValue(ok(RESPONSE))
    renderTab()
    await screen.findByRole('table', { name: /pipeline per stato/i })
    const bars = screen.getAllByTestId('bar-fill')
    expect(bars[2]).toHaveStyle({ backgroundColor: 'var(--foreground)' })
    expect(bars[3]).toHaveStyle({
      backgroundColor: 'color-mix(in oklab, var(--destructive) 10%, transparent)',
    })
  })

  it('says that the closed stages count today and not the period', async () => {
    // «Vinto 8» sits under «Deal vinti nel periodo 3» and the two answer different
    // questions. Without this line the card contradicts itself in the reader's eye.
    vi.mocked(api.GET).mockResolvedValue(ok(RESPONSE))
    renderTab()
    const caveat = await screen.findByText(/stanno oggi in quello stato, non il periodo/i)
    expect(caveat).toBeInTheDocument()
  })

  it('draws no separator when the pipeline has no closed stage', async () => {
    // A rule with nothing under it is a divider between a list and its own end.
    vi.mocked(api.GET).mockResolvedValue(
      ok({ ...RESPONSE, pipeline: RESPONSE.pipeline.filter((row) => row.stage_tipo === 'open') }),
    )
    renderTab()
    const table = await screen.findByRole('table', { name: /pipeline per stato/i })
    expect(table.querySelectorAll('[data-separator="true"]')).toHaveLength(0)
    // And no caveat either: a note about rows the card is not drawing.
    expect(screen.queryByText(/stanno oggi in quello stato/i)).not.toBeInTheDocument()
  })

  it('renders an error banner and no dashboard when the request fails', async () => {
    vi.mocked(api.GET).mockResolvedValue(
      failed({ title: 'Errore', detail: 'Non disponibile', code: 'unavailable' }, 500),
    )
    renderTab()
    expect(await screen.findByRole('alert')).toHaveTextContent('Non disponibile')
    expect(screen.queryByRole('table', { name: /pipeline per stato/i })).not.toBeInTheDocument()
    // The rule from §8.6, applied here too: an empty dashboard drawn after a failure says
    // "there is nothing" when the truth is "I do not know". The error branch is also
    // checked *before* the loading branch, because on a failure `isPending` is false while
    // `data` is still undefined -- a single `isPending || !data` guard answers a failed
    // read with a spinner that never resolves.
    expect(screen.queryByText(/nessun dato/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/aggiornato/i)).not.toBeInTheDocument()
  })

  it('shows how old the figures are', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(RESPONSE))
    renderTab()
    expect(await screen.findByText(/aggiornato/i)).toBeInTheDocument()
  })

  it('renders an empty pipeline as an empty state, not as a bar of nothing', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok({ ...RESPONSE, pipeline: [] }))
    renderTab()
    expect(await screen.findByText(/nessun dato nel periodo/i)).toBeInTheDocument()
  })

  it('shows nothing but the first row and the pipeline: no offers, no signals, no detail table', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(RESPONSE))
    renderTab()
    await screen.findByText('Pipeline per stato')
    expect(screen.queryByText(/Offerte in attesa/)).toBeNull()
    expect(screen.queryByText('Segnali')).toBeNull()
    expect(screen.queryByText('Dettaglio per stato')).toBeNull()
    expect(screen.queryByText(/Chiusure previste/)).toBeNull()
  })
})
