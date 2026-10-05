import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { EconomicTab } from './EconomicTab'
import type { CashMonth } from './queries'
import { api } from '@/lib/api'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PUT: vi.fn(), DELETE: vi.fn() } }
})

vi.mock('@tanstack/react-router', () => ({
  Link: ({
    to,
    children,
    ...rest
  }: {
    to: string
    children: React.ReactNode
  } & Record<string, unknown>) => (
    <a href={to} {...rest}>
      {children}
    </a>
  ),
}))

function ok(data: unknown) {
  return { data, response: new Response(null, { status: 200 }) } as never
}

/**
 * Two endpoints answer this screen since the «Stima fiscale» card moved into it:
 * `/api/analytics/overview` for the cards and the charts, `/api/analytics/fiscal` for
 * the card below them. Mocked by path rather than with one `mockResolvedValue`, because a
 * single answer for both would feed the panel a payload with no `avvertenza` and no year
 * -- green on an assertion about a card that never rendered what it renders in the app.
 */
function byPath(panoramica: unknown, fiscale: unknown = FISCALE) {
  return (path: string) =>
    Promise.resolve(path === '/api/analytics/fiscal' ? ok(fiscale) : ok(panoramica))
}

function failed(error: unknown, status: number) {
  return { error, response: new Response(null, { status }) } as never
}

/** Typed as the wire shape so a field added to `CashMonth` fails here rather than
 *  rendering something nobody asserted on (ORB-139: the two `pila_*` heights). The
 *  heights are illustrative, not sums: the chart prints them, it never checks them. */
function month(
  mese: number,
  incassato = '0.00',
  da_incassare = '0.00',
  bozze = '0.00',
  costi = '0.00',
): CashMonth {
  return {
    anno: 2026,
    mese,
    incassato,
    da_incassare,
    bozze,
    costi,
    pila_andamento: incassato,
    pila_proiezione: incassato,
    quote_andamento: { incassato: incassato === '0.00' ? 0 : 1, costi: costi === '0.00' ? 0 : 0.1 },
    quote_proiezione: {
      incassato: incassato === '0.00' ? 0 : 0.6,
      da_incassare: da_incassare === '0.00' ? 0 : 0.3,
      bozze: bozze === '0.00' ? 0 : 0.05,
      costi: costi === '0.00' ? 0 : 0.05,
    },
  }
}

const MESI = [
  month(1),
  month(2),
  month(3),
  month(4, '3990.00', '0.00', '0.00', '100.00'),
  month(5, '0.00', '0.00', '2500.00', '0.00'),
  month(6, '1200.00'),
  month(7, '6300.00', '0.00', '0.00', '97.50'),
  month(8, '7458.62', '6954.03', '0.00', '100.00'),
  month(9),
  month(10),
  month(11),
  month(12),
]

const FISCALE = {
  anno: 2026,
  stima: true,
  avvertenza: 'stima',
  ricavi: '20628.62',
  coefficiente_redditivita: '67.00',
  imponibile: '13821.18',
  aliquota_imposta_sostitutiva: '5.00',
  imposta_sostitutiva: '691.06',
  aliquota_inps: '26.07',
  contributi: '3423.02',
  reddito_netto_stimato: '16514.54',
  totale_dovuto: '4114.08',
}

const CONCENTRAZIONE = [
  {
    customer_id: 'c-grande',
    ragione_sociale: 'Grande S.r.l.',
    ricavi: '6000.00',
    fatture: 2,
    quota: 2 / 3,
  },
  {
    customer_id: 'c-piccolo',
    ragione_sociale: 'Piccolo S.r.l.',
    ricavi: '3000.00',
    fatture: 1,
    quota: 1 / 3,
  },
]

const RESPONSE = {
  calcolato_alle: '2026-09-08T10:00:00Z',
  cassa: {
    anno: 2026,
    base: 'competenza',
    incassato: '20628.62',
    da_incassare: '6954.03',
    bozze: '2500.00',
    proiettato: '30082.65',
    costi: '297.50',
    lordo_effettivo: '20331.12',
    lordo_proiettato: '29785.15',
    mesi: MESI,
  },
  fiscale: FISCALE,
  fiscale_proiettato: { ...FISCALE, ricavi: '30082.65', imponibile: '20155.38', totale_dovuto: '5999.55' },
  concentrazione_clienti: CONCENTRAZIONE,
  netto_effettivo: '16217.04',
  netto_proiettato: '23785.60',
}

const PERIODO = { da: '2026-09-01', a: '2026-09-30' }

function renderTab(periodo = PERIODO, base: 'competenza' | 'incasso' = 'competenza') {
  const onBaseChange = vi.fn()
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const rendered = render(
    <QueryClientProvider client={client}>
      <EconomicTab periodo={periodo} base={base} onBaseChange={onBaseChange} />
    </QueryClientProvider>,
  )
  return { ...rendered, onBaseChange }
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
})

describe('EconomicTab', () => {
  it('asks for the year the period names, because cash and taxes are told by the year', async () => {
    vi.mocked(api.GET).mockImplementation(byPath(RESPONSE) as never)
    renderTab({ da: '2025-03-01', a: '2025-03-31' })
    await screen.findByText(/Vista economica 2025/)
    expect(api.GET).toHaveBeenCalledWith('/api/analytics/overview', {
      params: { query: { anno: 2025, base: 'competenza' } },
    })
  })

  it('offers the two readings as one switch, marks the current one and hands the other back', async () => {
    // ORB-133, Ivan: «un interruttore che dia modo di passare da competenza / incasso è
    // utile, default a competenza». The switch is a radio group so the reading is announced
    // as a choice, not as two unrelated buttons; the change goes back to the URL through
    // the callback, never into local state.
    vi.mocked(api.GET).mockImplementation(byPath(RESPONSE) as never)
    const { onBaseChange } = renderTab()
    const gruppo = await screen.findByRole('radiogroup', { name: 'Lettura dei mesi' })
    const competenza = within(gruppo).getByRole('radio', { name: 'Competenza' })
    const incasso = within(gruppo).getByRole('radio', { name: 'Incasso' })
    expect(competenza).toHaveAttribute('aria-checked', 'true')
    expect(incasso).toHaveAttribute('aria-checked', 'false')
    await userEvent.click(incasso)
    expect(onBaseChange).toHaveBeenCalledWith('incasso')
    expect(onBaseChange).toHaveBeenCalledTimes(1)
  })

  it('requests the reading it is given and says which one the charts show', async () => {
    vi.mocked(api.GET).mockImplementation(
      byPath({ ...RESPONSE, cassa: { ...RESPONSE.cassa, base: 'incasso' } }) as never,
    )
    renderTab(PERIODO, 'incasso')
    await screen.findByRole('figure', { name: 'Andamento economico 2026' })
    expect(api.GET).toHaveBeenCalledWith('/api/analytics/overview', {
      params: { query: { anno: 2026, base: 'incasso' } },
    })
    expect(screen.getByRole('radio', { name: 'Incasso' })).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByText(/Vista economica 2026 per incasso/)).toBeInTheDocument()
  })

  it('tells the reader the tax block is on the money whatever the charts read by', async () => {
    // The estimate follows what was collected in the year (DECISIONS.md, 2026-09-10), so
    // under the accrual reading its cards say so rather than letting the reader assume
    // the tax moved with the bars. The projected estimate's hint quotes the revenue the
    // estimate itself was computed on, which under the accrual reading is not the
    // `proiettato` the cards above show: here they differ on purpose.
    vi.mocked(api.GET).mockImplementation(
      byPath({
        ...RESPONSE,
        fiscale_proiettato: { ...RESPONSE.fiscale_proiettato, ricavi: '31082.65' },
      }) as never,
    )
    renderTab()
    const netto = await screen.findByRole('group', { name: 'Totale netto ricavi' })
    expect(netto).toHaveTextContent(/su base incasso/)
    expect(screen.getByRole('group', { name: 'Totale netto ricavi con proiezione' })).toHaveTextContent(
      /su base incasso/,
    )
    const proiezione = screen.getByRole('group', { name: 'Totale da saldare con proiezione' })
    expect(proiezione).toHaveTextContent('31.082,65 €')
    expect(proiezione).not.toHaveTextContent('30.082,65 €')
  })

  it('moves the reading with the arrow keys, as a radio group promises', async () => {
    // One tab stop and the arrows, the radio keyboard model: two buttons announced as
    // radios would let a screen-reader user hear «1 of 2» and then find the arrows dead.
    vi.mocked(api.GET).mockImplementation(byPath(RESPONSE) as never)
    const { onBaseChange } = renderTab()
    const competenza = await screen.findByRole('radio', { name: 'Competenza' })
    competenza.focus()
    // Held, not tapped: Radix moves focus on a timeout after the keydown and checks the
    // item on focus only while the arrow is still down, as a finger on a key is. A
    // `{ArrowRight}` that releases in the same tick never selects, in any browser.
    await userEvent.keyboard('{ArrowRight>}')
    await waitFor(() => expect(onBaseChange).toHaveBeenCalledWith('incasso'))
    await userEvent.keyboard('{/ArrowRight}')
  })

  it('shows the money figures from the API strings, cents included', async () => {
    vi.mocked(api.GET).mockImplementation(byPath(RESPONSE) as never)
    renderTab()
    const incassati = await screen.findByRole('group', { name: 'Ricavi incassati' })
    expect(incassati).toHaveTextContent('20.628,62 €')
    expect(screen.getByRole('group', { name: 'Ricavi proiettati' })).toHaveTextContent('30.082,65 €')
    expect(screen.getByRole('group', { name: 'Costi passivi' })).toHaveTextContent('297,50 €')
    expect(screen.getByRole('group', { name: 'Totale lordo effettivo' })).toHaveTextContent(
      '20.331,12 €',
    )
  })

  it('shows the fiscal estimate on collected and projected revenue', async () => {
    vi.mocked(api.GET).mockImplementation(byPath(RESPONSE) as never)
    renderTab()
    expect(await screen.findByRole('group', { name: 'Totale da saldare' })).toHaveTextContent(
      '4.114,08 €',
    )
    expect(screen.getByRole('group', { name: 'Totale da saldare con proiezione' })).toHaveTextContent(
      '5.999,55 €',
    )
    expect(screen.getByRole('group', { name: 'Totale netto ricavi' })).toHaveTextContent(
      '16.217,04 €',
    )
    expect(screen.getByRole('group', { name: 'Imponibile forfettario stimato' })).toHaveTextContent(
      '13.821,18 €',
    )
    // The rates are on the «Stima fiscale» card below, row by row, so the paragraph that
    // used to repeat them beside a link to another screen has nothing left to add.
    expect(screen.queryByRole('link', { name: /stima fiscale/i })).toBeNull()
    expect(screen.queryByText(/Stima basata sul regime forfettario/)).toBeNull()
  })

  it('shows cash alone, and no link out, when the estimate is not available', async () => {
    vi.mocked(api.GET).mockImplementation(
      byPath({
        ...RESPONSE,
        fiscale: null,
        fiscale_proiettato: null,
        netto_effettivo: null,
        netto_proiettato: null,
      }) as never,
    )
    renderTab()
    await screen.findByRole('group', { name: 'Ricavi incassati' })
    expect(screen.queryByRole('group', { name: 'Totale da saldare' })).toBeNull()
    expect(screen.getByText(/serve un profilo fiscale configurato/)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /stima fiscale/i })).toBeNull()
  })

  it('carries the whole «Stima fiscale» card, after the figures it explains', async () => {
    vi.mocked(api.GET).mockImplementation(byPath(RESPONSE) as never)
    const { container } = renderTab()

    // Every row of it, which is what «la scheda completa» means: the figures above answer
    // "how much do I owe", the card answers "out of what, at which rates".
    //
    // Two dependent round trips, not one: `EconomicTab`'s own `useEconomicOverview` gates
    // everything below it (`EconomicTab.tsx`), and `FiscalPanel` only mounts -- starting
    // its own `useFiscalEstimate` fetch -- once that resolves, so this text needs both
    // queries' fetch-then-render cycles to finish in sequence, twice the chain most of
    // this file's other `findBy*` calls wait on. That also means the whole card (this
    // title, every row below, the note) and everything above it (the charts, the cards)
    // are already committed by the time this resolves, which is why the synchronous
    // `getByText`/`getByRole` calls that follow stay safe without their own `findBy*`.
    // What is not enough is the default one-second `asyncUtilTimeout`: on a loaded full
    // `pnpm --filter web test` run the doubled chain lost that race (REB-416), the same
    // shape `vite.config.ts`'s own `testTimeout: 20_000` comment already describes for
    // the suite as a whole. Matching that headroom here.
    expect(
      await screen.findByText('Stima fiscale 2026', {}, { timeout: 10_000 }),
    ).toBeInTheDocument()
    for (const label of [
      'Coefficiente di redditività',
      'Imponibile',
      'Aliquota imposta sostitutiva',
      'Imposta sostitutiva',
      'Aliquota INPS',
      'Contributi INPS',
      'Reddito netto stimato',
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument()
    }
    // The revenue row, addressed by the hint only it carries: its own label shares an
    // element with that hint, and its figure is also the «Ricavi incassati» card's.
    expect(screen.getByText(/fatture emesse nell'anno/)).toBeInTheDocument()
    expect(screen.getByText('67,00 %')).toBeInTheDocument()
    expect(screen.getByText('691,06 €')).toBeInTheDocument()
    expect(screen.getByText('16.514,54 €')).toBeInTheDocument()
    // The server's own caveat, above the figures it qualifies, as on the screen this card
    // came from -- its own sentence, not just that some note rendered.
    expect(screen.getByRole('note')).toHaveTextContent(FISCALE.avvertenza)

    // Position asserted by document order: charts, then the cards, then this card, after
    // the figures it explains. The order of the first two is upstream's (2026-09-09: the
    // shape of the year is read before its exact numbers); what this test owns is the
    // last step: the card answering "at which rates" never comes before the figures it
    // explains. This does not assert adjacency -- `EconomicTab.tsx` puts «Concentrazione
    // clienti» between the last figure and this card -- only that it is not pushed above
    // them, which is the one thing document order can prove.
    const grafico = screen.getByRole('figure', { name: 'Andamento economico 2026' })
    const cards = screen.getByRole('group', { name: 'Ricavi incassati' })
    const scheda = screen.getByText('Stima fiscale 2026')
    expect(grafico.compareDocumentPosition(cards) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(cards.compareDocumentPosition(scheda) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    const ultima = screen.getByRole('group', { name: 'Totale netto ricavi con proiezione' })
    expect(ultima.compareDocumentPosition(scheda) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(container.textContent).not.toMatch(/Apri la stima fiscale/)
  })

  it('asks the estimate for the year the period names', async () => {
    vi.mocked(api.GET).mockImplementation(byPath(RESPONSE, { ...FISCALE, anno: 2025 }) as never)
    renderTab({ da: '2025-03-01', a: '2025-03-31' })

    // Same doubled chain as the card test above (REB-416): the default one-second
    // asyncUtilTimeout is not enough here either, observed failing on a loaded full run.
    await screen.findByText('Stima fiscale 2025', {}, { timeout: 10_000 })
    expect(api.GET).toHaveBeenCalledWith('/api/analytics/fiscal', {
      params: { query: { anno: 2025 } },
    })
  })

  it('draws the two monthly charts with a legend and a table view each', async () => {
    vi.mocked(api.GET).mockImplementation(byPath(RESPONSE) as never)
    renderTab()
    const andamento = await screen.findByRole('figure', { name: 'Andamento economico 2026' })
    expect(within(andamento).getByRole('list', { name: 'Legenda' })).toHaveTextContent(
      'Ricavi incassatiCosti passivi',
    )
    const proiezione = screen.getByRole('figure', { name: 'Proiezione economica 2026' })
    expect(within(proiezione).getByRole('list', { name: 'Legenda' })).toHaveTextContent(
      'Da incassare',
    )
    // The tallest month is labelled directly; the table carries every value.
    expect(within(andamento).getAllByTestId('segment').length).toBeGreaterThan(0)
    expect(within(andamento).getByRole('table')).toHaveTextContent('6.300,00 €')
  })

  it('shows how old the figures are', async () => {
    vi.mocked(api.GET).mockImplementation(byPath(RESPONSE) as never)
    renderTab()
    expect(await screen.findByText(/Aggiornato/)).toBeInTheDocument()
  })

  it('ranks each customer by its own share of the year\'s whole revenue, distinct from the exposure table on the receivables page', async () => {
    vi.mocked(api.GET).mockImplementation(byPath(RESPONSE) as never)
    renderTab()

    const heading = await screen.findByRole('heading', { name: 'Concentrazione clienti' })
    const table = heading.closest('section')?.querySelector('table')
    expect(table).not.toBeNull()
    const rows = within(table as HTMLTableElement).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(2)
    // Ranked, largest first -- the server's own order, never re-sorted here.
    expect(rows[0]).toHaveTextContent('Grande S.r.l.')
    expect(rows[0]).toHaveTextContent('6.000,00 €')
    expect(rows[0]).toHaveTextContent('66,7%')
    expect(rows[1]).toHaveTextContent('Piccolo S.r.l.')
    expect(rows[1]).toHaveTextContent('33,3%')
    // A different figure from `per_cliente`'s receivables exposure, in words as well as
    // in data: this table answers a different question and says so.
    expect(screen.getByText(/non l'esposizione residua di «Da incassare»/)).toBeInTheDocument()
  })

  it('says so, rather than rendering an empty table, when nobody was invoiced this year', async () => {
    vi.mocked(api.GET).mockImplementation(
      byPath({ ...RESPONSE, concentrazione_clienti: [] }) as never,
    )
    renderTab()
    await screen.findByRole('group', { name: 'Ricavi incassati' })
    const heading = screen.getByRole('heading', { name: 'Concentrazione clienti' })
    expect(screen.getByText('Nessuna fattura emessa nell\'anno.')).toBeInTheDocument()
    expect(heading.closest('section')?.querySelector('table')).toBeNull()
  })

  it('renders an error banner and no figures when the request fails', async () => {
    vi.mocked(api.GET).mockResolvedValue(
      failed({ type: 'x', title: 'Errore', status: 500, detail: 'boom', code: 'domain_error' }, 500),
    )
    renderTab()
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Ricavi incassati' })).toBeNull()
  })
})

// -- the sidebar's azienda (REB-632, spec 2026-10-03 §1.9, §5) ---------------------------

import { AziendaContext, type AziendaRecord, type AziendaValue } from '@/lib/azienda'

function aziendaRecord(id: string, nome: string): AziendaRecord {
  return {
    id,
    nome,
    predefinita: id === 'a-1',
    attiva: true,
    ragione_sociale: nome,
    partita_iva: null,
    codice_fiscale: null,
    indirizzo: null,
    cap: null,
    comune: null,
    provincia: null,
    nazione: 'IT',
    pec: null,
    codice_sdi: null,
    telefono: null,
    email: null,
    sito_web: null,
    logo_key: null,
    firma_key: null,
    firma_email: null,
    regime_fiscale: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  }
}

const HUMANCRAFT = aziendaRecord('a-1', 'humancraft')
const REBASE_LTD = aziendaRecord('a-2', 'rebase ltd')

function twoAziende(selected: string | null): AziendaValue {
  return {
    aziende: [HUMANCRAFT, REBASE_LTD],
    selected,
    select: vi.fn(),
    several: true, scoped: false, pinned: false,
    byId: (id) => [HUMANCRAFT, REBASE_LTD].find((a) => a.id === id),
  }
}

const CONCENTRAZIONE_PER_AZIENDA = [
  { ...CONCENTRAZIONE[0], azienda_id: 'a-1', quota: 1 },
  { ...CONCENTRAZIONE[1], azienda_id: 'a-2', quota: 1 },
]

function renderWithAziende(value: AziendaValue) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <AziendaContext value={value}>
        <EconomicTab periodo={PERIODO} base="competenza" onBaseChange={vi.fn()} />
      </AziendaContext>
    </QueryClientProvider>,
  )
}

type GetCall = [string, { params?: { query?: Record<string, unknown> } } | undefined]

function queriesSentTo(path: string): Record<string, unknown>[] {
  return (vi.mocked(api.GET).mock.calls as unknown as GetCall[])
    .filter(([called]) => called === path)
    .map(([, init]) => init?.params?.query ?? {})
}

describe('EconomicTab, with several aziende', () => {
  it('in «tutte» asks the estimate once per azienda and names each share’s azienda', async () => {
    vi.mocked(api.GET).mockImplementation((path: string, init?: unknown) => {
      if (path === '/api/analytics/fiscal') {
        const query = (init as GetCall[1])?.params?.query ?? {}
        // The forfettario has an estimate; the foreign company has no profile at all.
        return Promise.resolve(
          query.azienda_id === 'a-1' ? ok(FISCALE) : failed({ code: 'not_found' }, 404),
        )
      }
      return Promise.resolve(
        ok({
          ...RESPONSE,
          azienda_id: null,
          fiscale: null,
          fiscale_proiettato: null,
          netto_effettivo: null,
          netto_proiettato: null,
          concentrazione_clienti: CONCENTRAZIONE_PER_AZIENDA,
        }),
      )
    })
    renderWithAziende(twoAziende(null))

    // The cards say why there is no space-wide estimate, never «non disponibile».
    expect(await screen.findByText(/Con più aziende la stima è una per azienda/)).toBeInTheDocument()
    expect(screen.queryByText(/Non disponibile qui/)).not.toBeInTheDocument()
    // One estimate per azienda, by id; the overview itself was asked for every azienda.
    await waitFor(() =>
      expect(queriesSentTo('/api/analytics/fiscal')).toEqual(
        expect.arrayContaining([
          expect.objectContaining({ azienda_id: 'a-1' }),
          expect.objectContaining({ azienda_id: 'a-2' }),
        ]),
      ),
    )
    expect(queriesSentTo('/api/analytics/overview')[0]).not.toHaveProperty('azienda_id')
    // The one card that exists carries its azienda's name; the other draws nothing.
    expect(
      await screen.findByRole('heading', { name: 'Stima fiscale 2026 · humancraft' }),
    ).toBeInTheDocument()
    expect(
      screen.queryByRole('heading', { name: 'Stima fiscale 2026 · rebase ltd' }),
    ).not.toBeInTheDocument()
    expect(screen.queryByText(/Profilo fiscale non configurato/)).not.toBeInTheDocument()
    // The concentration table is split by azienda, each share of its own revenue.
    const section = screen.getByRole('heading', { name: 'Concentrazione clienti' }).closest('section')
    expect(section).not.toBeNull()
    expect(within(section as HTMLElement).getByRole('heading', { name: 'humancraft' })).toBeInTheDocument()
    expect(within(section as HTMLElement).getByRole('heading', { name: 'rebase ltd' })).toBeInTheDocument()
    expect(within(section as HTMLElement).getAllByText('100,0%')).toHaveLength(2)
  })

  it('for a non-admin in «tutte» draws no card and no banner, only the note', async () => {
    vi.mocked(api.GET).mockImplementation((path: string) =>
      Promise.resolve(
        path === '/api/analytics/fiscal'
          ? failed({ code: 'permission_denied', detail: 'solo un admin' }, 403)
          : ok({
              ...RESPONSE,
              azienda_id: null,
              fiscale: null,
              fiscale_proiettato: null,
              netto_effettivo: null,
              netto_proiettato: null,
              concentrazione_clienti: CONCENTRAZIONE_PER_AZIENDA,
            }),
      ),
    )
    renderWithAziende(twoAziende(null))
    expect(await screen.findByText(/Con più aziende la stima è una per azienda/)).toBeInTheDocument()
    await waitFor(() => expect(queriesSentTo('/api/analytics/fiscal')).toHaveLength(2))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /^Stima fiscale \d{4}/ })).not.toBeInTheDocument()
  })

  it('with one azienda selected sends it on the overview and the estimate alike', async () => {
    vi.mocked(api.GET).mockImplementation(byPath({ ...RESPONSE, azienda_id: 'a-1' }) as never)
    renderWithAziende(twoAziende('a-1'))
    expect(await screen.findByRole('heading', { name: 'Stima fiscale 2026' })).toBeInTheDocument()
    expect(queriesSentTo('/api/analytics/overview')[0]).toMatchObject({ azienda_id: 'a-1' })
    expect(queriesSentTo('/api/analytics/fiscal')).toEqual([{ anno: 2026, azienda_id: 'a-1' }])
    // One azienda on screen: no per-azienda headings anywhere.
    expect(screen.queryByRole('heading', { name: 'humancraft' })).not.toBeInTheDocument()
  })
})
