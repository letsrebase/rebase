import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { DaysToInvoiceSection } from './DaysToInvoiceSection'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn() } }
})
const navigate = vi.hoisted(() => vi.fn())
vi.mock('@tanstack/react-router', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@tanstack/react-router')>()),
  useNavigate: () => navigate,
}))
vi.mock('@rebase/ui/sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))
const auth = vi.hoisted(() => ({ canBill: true }))
vi.mock('@/lib/auth', () => ({ useCan: () => auth.canBill }))

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) }) as never
}

const OCTOBER = {
  mese: '2026-10',
  descrizione: 'Attività ottobre 2026 - 1.50 giorno',
  giornate: 2,
  quantita: '1.50',
  unita_misura: 'giorno',
  prezzo_unitario: '500.00',
  importo: '750.00',
}
const NOVEMBER = {
  mese: '2026-11',
  descrizione: 'Attività novembre 2026 - 1.00 giorno',
  giornate: 1,
  quantita: '1.00',
  unita_misura: 'giorno',
  prezzo_unitario: '500.00',
  importo: '500.00',
}

function days(overrides: Record<string, unknown> = {}) {
  return {
    contract_id: 'c1',
    divisa: 'EUR',
    righe: [OCTOBER, NOVEMBER],
    mesi: ['2026-10', '2026-11'],
    giornate: 3,
    quantita: '2.50',
    imponibile: '1250.00',
    senza_tariffa: [],
    senza_approvazione: 0,
    fatturabile: true,
    ...overrides,
  }
}

function renderSection() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <DaysToInvoiceSection contractId="c1" divisa="EUR" />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  navigate.mockReset()
  auth.canBill = true
})

describe('DaysToInvoiceSection', () => {
  it('shows the ready days as the lines the draft would carry, priced by the server', async () => {
    vi.mocked(api.GET).mockImplementation((() => ok(days())) as never)
    renderSection()

    expect(await screen.findByText('Attività ottobre 2026 - 1.50 giorno')).toBeInTheDocument()
    expect(screen.getByText('2 giornate a 500,00 € per giorno')).toBeInTheDocument()
    expect(screen.getByText('750,00 €')).toBeInTheDocument()
    expect(screen.getByText('1.250,00 €')).toBeInTheDocument()
  })

  it('creates the draft with every ready day and opens it', async () => {
    vi.mocked(api.GET).mockImplementation((() => ok(days())) as never)
    vi.mocked(api.POST).mockImplementation((() => ok({ id: 'inv-1' })) as never)
    renderSection()

    await userEvent.click(await screen.findByRole('button', { name: 'Crea la bozza di fattura' }))

    expect(api.POST).toHaveBeenCalledWith(
      '/api/contracts/{contract_id}/invoice-days',
      expect.objectContaining({ params: { path: { contract_id: 'c1' } }, body: {} }),
    )
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith({
        to: '/app/invoices/$invoiceId',
        params: { invoiceId: 'inv-1' },
      }),
    )
  })

  it('bills one month when one is chosen, asking the server for that month', async () => {
    vi.mocked(api.GET).mockImplementation(((_path: string, init: { params: { query?: { da?: string } } }) =>
      ok(init.params.query?.da === '2026-10-01' ? days({ righe: [OCTOBER], mesi: ['2026-10', '2026-11'], imponibile: '750.00' }) : days())) as never)
    vi.mocked(api.POST).mockImplementation((() => ok({ id: 'inv-1' })) as never)
    renderSection()

    await userEvent.click(await screen.findByRole('combobox', { name: 'Periodo' }))
    await userEvent.click(await screen.findByRole('option', { name: 'ottobre 2026' }))
    await waitFor(() => expect(screen.queryByText(NOVEMBER.descrizione)).not.toBeInTheDocument())
    await userEvent.click(screen.getByRole('button', { name: 'Crea la bozza di fattura' }))

    expect(api.POST).toHaveBeenCalledWith(
      '/api/contracts/{contract_id}/invoice-days',
      expect.objectContaining({ body: { da: '2026-10-01', a: '2026-10-31' } }),
    )
  })

  it('stops the button and names the days no rate card covers', async () => {
    vi.mocked(api.GET).mockImplementation(
      (() => ok(days({ senza_tariffa: ['2026-03-05'], fatturabile: false }))) as never,
    )
    renderSection()

    expect(await screen.findByRole('alert')).toHaveTextContent('05/03/2026')
    expect(screen.getByRole('button', { name: 'Crea la bozza di fattura' })).toBeDisabled()
  })

  it('says how many days wait for an approval, and offers nothing when none is ready', async () => {
    vi.mocked(api.GET).mockImplementation(
      (() => ok(days({ righe: [], mesi: [], giornate: 0, imponibile: '0.00', senza_approvazione: 2, fatturabile: false }))) as never,
    )
    renderSection()

    expect(await screen.findByText('Nessuna giornata pronta da fatturare.')).toBeInTheDocument()
    expect(screen.getByText(/2 giornate lavorate senza l’approvazione/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Crea la bozza di fattura' })).not.toBeInTheDocument()
  })

  it('shows the lines to someone who cannot bill but not the button', async () => {
    auth.canBill = false
    vi.mocked(api.GET).mockImplementation((() => ok(days())) as never)
    renderSection()

    expect(await screen.findByText(OCTOBER.descrizione)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Crea la bozza di fattura' })).not.toBeInTheDocument()
  })
})
