/**
 * Slice 8 part A's tab (REB-329). What a snapshot would not reach: every amount is the
 * string the API sent, the six buckets are drawn in the server's order with the server's
 * labels, the overdue bucket alone carries a link to the filtered invoice list, a
 * reminder count reads as sent letters, and a failed request renders an error rather than
 * an empty scadenziario.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { ReceivablesTab } from './ReceivablesTab'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PUT: vi.fn(), DELETE: vi.fn() } }
})

vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, children }: { to: string; children: ReactNode }) => <a href={to}>{children}</a>,
}))

function ok(data: unknown) {
  return { data, response: new Response(null, { status: 200 }) } as never
}

function failed(error: unknown, status: number) {
  return { error, response: new Response(null, { status }) } as never
}

const fascia = (codice: string, etichetta: string, importo: string, numero: number) => ({
  codice,
  etichetta,
  da: null,
  a: null,
  importo,
  numero,
  quota: 0.5,
  collegamento: codice === 'scaduto' ? '/app/invoices?scadute=true' : null,
})

const RESPONSE = {
  calcolato_alle: '2026-09-22T10:00:00Z',
  oggi: '2026-09-22',
  totale: '2100.00',
  fasce: [
    fascia('scaduto', 'Scaduto', '100.00', 1),
    fascia('entro_30', 'Entro 30 giorni', '200.00', 1),
    fascia('da_31_a_60', 'Da 31 a 60 giorni', '300.00', 1),
    fascia('da_61_a_90', 'Da 61 a 90 giorni', '400.00', 1),
    fascia('oltre_90', 'Oltre 90 giorni', '500.00', 1),
    fascia('senza_scadenza', 'Senza scadenza', '600.00', 1),
  ],
  per_mese: [{ mese: '2026-10-01', importo: '900.00', numero: 2, quota: 1 }],
  per_cliente: [
    {
      customer_id: 'c-beta',
      ragione_sociale: 'Beta S.r.l.',
      importo: '1300.00',
      numero: 3,
      scaduto: '0.00',
      quota: 1,
    },
  ],
  scadute: [
    {
      invoice_id: 'f-1',
      numero: '2026/1',
      customer_id: 'c-acme',
      cliente: 'Acme S.r.l.',
      data_scadenza: '2026-08-13',
      giorni_di_ritardo: 40,
      importo: '100.00',
      solleciti_inviati: 1,
      ultimo_sollecito_il: '2026-09-15',
    },
  ],
  scadute_totale: 1,
}

function renderTab() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ReceivablesTab />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
})

describe('ReceivablesTab', () => {
  it('draws the six buckets in the server’s order, each amount as sent', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(RESPONSE))
    renderTab()
    const chart = await screen.findByRole('table', { name: 'Scadenziario per fascia' })
    const labels = within(chart)
      .getAllByRole('rowheader')
      .map((cell) => cell.textContent)
    expect(labels).toEqual([
      'Scaduto (1)',
      'Entro 30 giorni (1)',
      'Da 31 a 60 giorni (1)',
      'Da 61 a 90 giorni (1)',
      'Oltre 90 giorni (1)',
      'Senza scadenza (1)',
    ])
    expect(within(chart).getByText(/600,00/)).toBeInTheDocument()
    expect(screen.getByRole('group', { name: 'Da incassare' })).toHaveTextContent(/2\.100,00/)
    expect(screen.getByRole('group', { name: 'Di cui scaduto' })).toHaveTextContent(/100,00/)
  })

  it('links the overdue bucket to the filtered invoice list, and nothing else', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(RESPONSE))
    renderTab()
    const link = await screen.findByRole('link', { name: 'Vedi le fatture scadute' })
    expect(link).toHaveAttribute('href', '/app/invoices')
  })

  it('names the month the money is due in, and who owes it', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(RESPONSE))
    renderTab()
    expect(await screen.findByText('ottobre 2026 (2)')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Beta S.r.l.' })).toBeInTheDocument()
    // `Intl` separates the amount and the sign with a no-break space: matched loosely.
    expect(screen.getByRole('cell', { name: /1\.300,00/ })).toBeInTheDocument()
  })

  it('reads a reminder count as letters that left, with the last date', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(RESPONSE))
    renderTab()
    expect(await screen.findByRole('link', { name: '2026/1' })).toBeInTheDocument()
    expect(screen.getByText('1 sollecito, ultimo il 15/09/2026')).toBeInTheDocument()
    expect(screen.getByText('40 gg')).toBeInTheDocument()
  })

  it('renders an error, not an empty scadenziario, when the request fails', async () => {
    vi.mocked(api.GET).mockResolvedValue(
      failed({ type: 'about:blank', title: 'Errore', status: 500, detail: 'boom' }, 500),
    )
    renderTab()
    expect(await screen.findByRole('alert')).toHaveTextContent(/boom/)
    expect(screen.queryByText(/scadenziario per fascia/i)).not.toBeInTheDocument()
  })
})
