/**
 * The losses of a tax in «Tasse» (REB-1088): the typed ones listed with «Correggi» and «Togli»,
 * «Digita una perdita» sending exactly what was typed, a refusal of the server shown as it is, and
 * what the estimate did with the losses drawn from the server's own figures.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { TaxLossSection } from './TaxLosses'
import type { FiscalEstimate } from './labels'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), PUT: vi.fn(), DELETE: vi.fn() } }
})

const ok = (data: unknown) => Promise.resolve({ data, error: undefined, response: new Response(null, { status: 200 }) })

const OFFSET = {
  tax: 'ires',
  year: 2026,
  income: '36800.00',
  used: '29440.00',
  remaining: '20560.00',
  estimated: false,
  origins: [
    {
      origin_year: 2025,
      estimated: false,
      limit_percent: '80',
      available: '50000.00',
      used: '29440.00',
      remaining: '20560.00',
    },
  ],
  missing: [],
  assumptions: [],
  sources: [],
}

function estimate(overrides: Record<string, unknown> = {}): FiscalEstimate {
  return {
    legal_entity_id: 'le-1',
    losses: [
      {
        legal_entity_id: 'le-1',
        tax: 'ires',
        year: 2025,
        amount: '50000.00',
        remaining: '50000.00',
        remaining_as_of_year: 2025,
        new_activity: false,
        activity_start_year: null,
        source: 'typed',
        updated_at: '2026-11-12T10:00:00Z',
      },
    ],
    company: { ires: { losses: OFFSET }, irap: {} },
    ...overrides,
  } as unknown as FiscalEstimate
}

function renderSection(value: FiscalEstimate, tax: 'ires' | 'irpef' = 'ires') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <TaxLossSection estimate={value} tax={tax} year={2026} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  for (const verb of [api.GET, api.PUT, api.DELETE]) vi.mocked(verb).mockReset()
  vi.mocked(api.PUT).mockImplementation((() => ok({})) as never)
  vi.mocked(api.DELETE).mockImplementation((() => ok(undefined)) as never)
})

describe('«Perdite degli anni precedenti»', () => {
  it('lists the typed loss and what the estimate did with it', () => {
    renderSection(estimate())
    const section = screen.getByRole('heading', { name: 'Perdite degli anni precedenti' }).closest('section')!
    expect(within(section).getByText(/2025: 50\.000,00/)).toBeInTheDocument()
    expect(within(section).getByText(/Perdita del 2025 \(digitata\): disponibile/)).toBeInTheDocument()
    expect(within(section).getByText(/limite 80,00 %/)).toBeInTheDocument()
  })

  it('says a loss is estimated when it is not a typed one', () => {
    const offset = { ...OFFSET, estimated: true, origins: [{ ...OFFSET.origins[0], estimated: true }] }
    renderSection(estimate({ losses: [], company: { ires: { losses: offset }, irap: {} } }))
    expect(screen.getByText('Nessuna perdita digitata.', { exact: false })).toBeInTheDocument()
    expect(screen.getByText(/Perdita del 2025 \(stimata\)/)).toBeInTheDocument()
  })

  it('shows what is missing for the figure instead of a number', () => {
    const offset = { ...OFFSET, used: '0.00', missing: ['Non c’è un reddito stimato per il 2025.'] }
    renderSection(estimate({ company: { ires: { losses: offset }, irap: {} } }))
    expect(screen.getByText('Non c’è un reddito stimato per il 2025.')).toBeInTheDocument()
  })

  it('types a loss with its remainder and the first-periods facts, sending what was typed', async () => {
    renderSection(estimate({ losses: [] }))
    await userEvent.click(screen.getByRole('button', { name: 'Digita una perdita' }))
    const dialog = await screen.findByRole('dialog', { name: /Digita una perdita: IRES/ })
    expect(within(dialog).getByLabelText('Anno della perdita')).toHaveValue('2025')

    await userEvent.click(within(dialog).getByRole('button', { name: 'Salva la perdita' }))
    expect(await within(dialog).findByText('Scrivi l’importo della perdita.')).toBeInTheDocument()
    expect(api.PUT).not.toHaveBeenCalled()

    await userEvent.type(within(dialog).getByLabelText('Importo della perdita'), '35.000,00')
    await userEvent.type(within(dialog).getByLabelText('Residuo (se già usata in parte)'), '30000')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Salva la perdita' }))
    expect(
      await within(dialog).findByText('Il residuo e l’anno a cui si riferisce vanno scritti insieme.'),
    ).toBeInTheDocument()

    await userEvent.type(within(dialog).getByLabelText('Residuo alla fine del'), '2025')
    await userEvent.click(within(dialog).getByRole('checkbox'))
    await userEvent.type(within(dialog).getByLabelText(/Anno di costituzione/), '2024')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Salva la perdita' }))
    await waitFor(() =>
      expect(api.PUT).toHaveBeenCalledWith('/api/analytics/fiscal/losses', {
        params: { query: { legal_entity_id: 'le-1' } },
        body: {
          tax: 'ires',
          year: 2025,
          amount: '35000.00',
          remaining: '30000',
          remaining_as_of_year: 2025,
          new_activity: true,
          activity_start_year: 2024,
        },
      }),
    )
  })

  it('shows the sentence of a refusal and keeps the dialog open', async () => {
    vi.mocked(api.PUT).mockImplementation((() =>
      Promise.resolve({
        data: undefined,
        error: { detail: 'il residuo non può superare la perdita', title: 'x', status: 422 },
        response: new Response(null, { status: 422 }),
      })) as never)
    renderSection(estimate({ losses: [] }))
    await userEvent.click(screen.getByRole('button', { name: 'Digita una perdita' }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.type(within(dialog).getByLabelText('Importo della perdita'), '100')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Salva la perdita' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('il residuo non può superare la perdita')
  })

  it('corrects a loss without letting its year change, and removes one', async () => {
    renderSection(estimate())
    await userEvent.click(screen.getByRole('button', { name: 'Correggi' }))
    const dialog = await screen.findByRole('dialog', { name: /Correggi una perdita: IRES/ })
    expect(within(dialog).getByLabelText('Anno della perdita')).toBeDisabled()
    expect(within(dialog).getByLabelText('Importo della perdita')).toHaveValue('50000.00')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Annulla' }))

    await userEvent.click(screen.getByRole('button', { name: 'Togli la perdita del 2025' }))
    await waitFor(() =>
      expect(api.DELETE).toHaveBeenCalledWith('/api/analytics/fiscal/losses', {
        params: { query: { legal_entity_id: 'le-1', tax: 'ires', year: 2025 } },
      }),
    )
  })

  it('tells a person that only a business loss is carried', () => {
    renderSection(estimate({ losses: [], company: undefined, person: { income: {} } }), 'irpef')
    expect(screen.getByText(/non una di lavoro autonomo/)).toBeInTheDocument()
  })
})
