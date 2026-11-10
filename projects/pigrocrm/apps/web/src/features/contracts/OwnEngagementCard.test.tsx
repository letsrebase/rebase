import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { OwnEngagementCard } from './OwnEngagementCard'

const auth = vi.hoisted(() => ({ admin: true }))
vi.mock('@/lib/auth', () => ({ useIsAdmin: () => auth.admin }))

const mockGet = vi.spyOn(api, 'GET')

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) }) as never
}
function failed(error: unknown, status: number) {
  return Promise.resolve({ error, response: new Response(null, { status }) }) as never
}

function month(n: number, extra: Record<string, unknown> = {}) {
  const mm = String(n).padStart(2, '0')
  return {
    month: `2026-${mm}-01`,
    label: `Mese ${mm}`,
    state: 'approved',
    received_on: null,
    answer_by: null,
    held_dates: [],
    ...extra,
  }
}

const BASE = {
  letter_number: '2026/12',
  state: 'active',
  starts_on: '2026-01-01',
  ends_on: null,
  fee: '400.00',
  fee_unit: 'day',
  days_per_week: '5',
  payment_rule: 'invoice_term',
  payment_days: 30,
  payment_end_of_month: false,
  collection_days: null,
  months: [month(1)],
}

function renderCard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <OwnEngagementCard contractId="c1" />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  mockGet.mockReset()
  auth.admin = true
})

describe('OwnEngagementCard', () => {
  it('asks nothing and draws nothing for anyone who is not an administrator', () => {
    auth.admin = false
    const { container } = renderCard()
    expect(container).toBeEmptyDOMElement()
    expect(mockGet).not.toHaveBeenCalled()
  })

  it('renders nothing on 404', async () => {
    mockGet.mockReturnValue(failed({ status: 404, code: 'not_found', detail: 'x' }, 404))
    const { container } = renderCard()
    await vi.waitFor(() => expect(mockGet).toHaveBeenCalled())
    await new Promise((r) => setTimeout(r, 20))
    expect(container).toBeEmptyDOMElement()
  })

  it('says the held days and shows the dates', async () => {
    mockGet.mockReturnValue(
      ok({ ...BASE, months: [month(10, { held_dates: ['2026-10-05', '2026-10-06'] })] }),
    )
    renderCard()
    expect(await screen.findByText('Approvato, 2 giorni non riconosciuti')).toBeInTheDocument()
    expect(screen.getByText('05/10/2026, 06/10/2026')).toBeInTheDocument()
    expect(screen.getByText('2026/12')).toBeInTheDocument()
  })

  it('shows the answer date of a received month', async () => {
    mockGet.mockReturnValue(
      ok({ ...BASE, months: [month(10, { state: 'received', answer_by: '2026-11-12' })] }),
    )
    renderCard()
    expect(await screen.findByText('Ricevuto da rebase')).toBeInTheDocument()
    expect(screen.getByText('risposta entro il 12/11/2026')).toBeInTheDocument()
  })

  it('caps at six months and the one button shows the rest', async () => {
    mockGet.mockReturnValue(ok({ ...BASE, months: [1, 2, 3, 4, 5, 6, 7, 8].map((n) => month(n)) }))
    renderCard()
    expect(await screen.findByText('Mese 08')).toBeInTheDocument()
    expect(screen.queryByText('Mese 02')).not.toBeInTheDocument()
    expect(screen.getAllByRole('button')).toHaveLength(1)
    await userEvent.click(screen.getByRole('button', { name: 'Mostra i mesi precedenti' }))
    expect(screen.getByText('Mese 01')).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('has no button on a short engagement', async () => {
    mockGet.mockReturnValue(ok(BASE))
    renderCard()
    await screen.findByText('Mese 01')
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('says the fixed sentence on another error', async () => {
    mockGet.mockReturnValue(failed({ status: 500, code: 'boom', detail: 'server' }, 500))
    renderCard()
    expect(
      await screen.findByText(
        "Non riesco a leggere lo stato dell'incarico. I tuoi giorni e la tariffa si vedono lo stesso.",
      ),
    ).toBeInTheDocument()
  })
})
