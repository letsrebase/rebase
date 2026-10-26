import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { LegalEntityContext, type LegalEntityValue } from '@/lib/legalEntity'
import { DaysTab } from './DaysTab'
import { days } from './format'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn() } }
})

const STATI = [
  'proposto',
  'approvato',
  'lavorato',
  'fatturato',
  'pagato',
  'lavorato_senza_approvazione',
  'contestato',
  'non_fatturabile',
]

function month(mese: string, over: Record<string, unknown> = {}, given: Record<string, string> = {}) {
  return {
    mese,
    completo: true,
    per_stato: STATI.map((stato) => ({
      stato,
      giorni: given[stato] ?? '0.00',
      quota: given[stato] ? 0.5 : 0,
    })),
    totale: Object.values(given).length ? '12.50' : '0.00',
    quota_totale: 1,
    registrati: '12.00',
    quota_registrati: 1,
    media_precedente: '10.00',
    senza_approvazione: '2.50',
    quota_senza_approvazione: 1,
    ...over,
  }
}

const analysis = (over: Record<string, unknown> = {}) => ({
  da: '2026-08-01',
  a: '2026-09-30',
  azienda_id: null,
  oggi: '2026-10-26',
  mesi: [
    month('2026-08', {}, { lavorato: '10.00', proposto: '2.50' }),
    month('2026-09', { registrati: '14.00', media_precedente: '11.50' }, { fatturato: '12.50' }),
  ],
  ultimo_mese_completo: '2026-09',
  giorni_nel_periodo: '25.00',
  giorni_fatturati: '12.50',
  tariffa_reale: '481.62',
  per_cliente: [
    {
      id: 'c1',
      etichetta: 'Acme S.r.l.',
      giorni_fatturati: '12.50',
      importo_fatturato: '6020.25',
      tariffa_reale: '481.62',
      giorni_senza_listino: '0.00',
      tariffa_nominale: '500.00',
    },
  ],
  per_contratto: [
    {
      id: 'k1',
      etichetta: 'Consulenza Acme',
      giorni_fatturati: '12.50',
      importo_fatturato: '6020.25',
      tariffa_reale: '481.62',
      giorni_senza_listino: '1.00',
      tariffa_nominale: null,
    },
  ],
  senza_approvazione_nel_periodo: '4.50',
  senza_approvazione_sempre: '9.50',
  ...over,
})

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) }) as never
}

const ONE: LegalEntityValue = {
  aziende: [],
  selected: null,
  select: () => {},
  several: false,
  scoped: false,
  pinned: false,
  byId: () => undefined,
}

function renderTab() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <LegalEntityContext value={ONE}>
        <DaysTab periodo={{ da: '2026-08-01', a: '2026-09-30' }} />
      </LegalEntityContext>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
})

describe('days', () => {
  it('prints days with a decimal comma and no trailing zeros', () => {
    expect(days('1.50')).toBe('1,5')
    expect(days('2.00')).toBe('2')
    expect(days('10.00')).toBe('10')
    expect(days('0.25')).toBe('0,25')
  })
})

describe('DaysTab', () => {
  it('asks for the period and shows the month\'s days and the realised rate', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(analysis()))
    renderTab()

    expect(
      within(await screen.findByRole('group', { name: 'Giornate nel periodo' })).getByText('25'),
    ).toBeInTheDocument()
    expect(
      within(screen.getByRole('group', { name: 'Tariffa reale al giorno' })).getByText('481,62 €'),
    ).toBeInTheDocument()
    expect(api.GET).toHaveBeenCalledWith('/api/analytics/days', {
      params: { query: { da: '2026-08-01', a: '2026-09-30' } },
    })
  })

  it('stacks each month by state with the server\'s shares, and names every state', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(analysis()))
    renderTab()

    const stack = await screen.findByTestId('stack-2026-08')
    const segments = [...stack.querySelectorAll('[data-stato]')]
    expect(segments.map((s) => s.getAttribute('data-stato'))).toEqual(['proposto', 'lavorato'])
    expect((segments[0] as HTMLElement).style.width).toBe('50%')
    const legend = screen.getByRole('list', { name: 'Legenda' })
    expect(within(legend).getByText('Lavorata senza approvazione')).toBeInTheDocument()
    expect(within(legend).getByText('Non fatturabile')).toBeInTheDocument()
  })

  it('sets the realised rate against the rate card and counts an uncovered day apart', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(analysis()))
    renderTab()

    const row = (await screen.findByText('Consulenza Acme')).closest('tr')!
    expect(within(row).getByText('481,62 €')).toBeInTheDocument()
    // No card covers the day the line billed: a dash, not 0,00 €, and the day is counted.
    expect(within(row).getByText('—')).toBeInTheDocument()
    expect(within(row).getByText('1')).toBeInTheDocument()
    const client = screen.getByText('Acme S.r.l.').closest('tr')!
    expect(within(client).getByText('500,00 €')).toBeInTheDocument()
  })

  it('says the last finished month against the three before it', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(analysis()))
    renderTab()

    expect(
      await screen.findByText(
        'A settembre 2026 hai registrato 14 giornate, contro una media di 11,5 nei tre mesi prima.',
      ),
    ).toBeInTheDocument()
  })

  it('says nothing about a trend while no month has ended', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(analysis({ ultimo_mese_completo: null })))
    renderTab()

    await screen.findByText('Giornate registrate e media dei tre mesi prima')
    expect(screen.queryByText(/hai registrato/)).toBeNull()
  })

  it('shows the worked-without-approval history, this period and for ever', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(analysis()))
    renderTab()

    const group = await screen.findByRole('group', { name: 'Senza approvazione' })
    expect(within(group).getByText('4,5')).toBeInTheDocument()
    expect(within(group).getByText(/in tutto 9,5/)).toBeInTheDocument()
    expect(screen.getByText('Giornate senza approvazione per mese')).toBeInTheDocument()
  })

  it('says so, three times, on a space with no days at all', async () => {
    vi.mocked(api.GET).mockImplementation(() =>
      ok(
        analysis({
          mesi: [month('2026-08'), month('2026-09')].map((m) => ({ ...m, totale: '0.00' })),
          giorni_nel_periodo: '0.00',
          giorni_fatturati: '0.00',
          tariffa_reale: null,
          per_cliente: [],
          per_contratto: [],
          senza_approvazione_nel_periodo: '0.00',
          senza_approvazione_sempre: '0.00',
        }),
      ),
    )
    renderTab()

    expect(await screen.findAllByText('Nessuna giornata nel periodo.')).toHaveLength(2)
    expect(screen.getByText('Nessuna giornata fatturata nel periodo.')).toBeInTheDocument()
    expect(
      screen.getByText('Nessuna giornata è mai stata lavorata senza l’approvazione che il contratto chiede.'),
    ).toBeInTheDocument()
  })
})
