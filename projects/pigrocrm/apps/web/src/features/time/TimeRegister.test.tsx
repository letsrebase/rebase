import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import type { TimeEntry } from './queries'
import { groupByDay } from './register'
import { TimeRegister } from './TimeRegister'
import { weekDays } from './week'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PATCH: vi.fn(), DELETE: vi.fn() } }
})
vi.mock('@rebase/ui/sonner', () => ({ toast: { info: vi.fn(), success: vi.fn(), error: vi.fn() } }))
vi.mock('@/lib/auth', () => ({
  useAuth: () => ({ user: { id: 'u1' }, isLoading: false, login: vi.fn(), logout: vi.fn() }),
}))

const DEAL = 'aaaaaaaa-aaaa-7aaa-8aaa-aaaaaaaaaaaa'

function entry(overrides: Partial<TimeEntry> = {}): TimeEntry {
  return {
    id: 'e1',
    deal_id: DEAL,
    user_id: 'u1',
    data: '2026-03-09',
    ore: '2.50',
    descrizione: 'Analisi',
    fatturabile: true,
    tariffa_applicata: null,
    costo_applicato: null,
    tariffa_origine: 'assente',
    costo_origine: 'assente',
    valore_riga: null,
    costo_riga: null,
    invoice_line_id: null,
    note_interne: null,
    custom_fields: {},
    created_at: '2026-03-09T09:00:00Z',
    updated_at: '2026-03-09T09:00:00Z',
    ...overrides,
  } as TimeEntry
}

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) }) as never
}

const days = weekDays(new Date(2026, 2, 12))

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  vi.mocked(api.GET).mockImplementation(((path: string) =>
    path === '/api/time-entries/timer' ? ok(null) : ok({ custom_fields: [] })) as never)
})

function renderRegister(entries: TimeEntry[], canWrite = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <TimeRegister
        entries={entries}
        days={days}
        dealNames={new Map([[DEAL, 'Progetto Alfa']])}
        canWrite={canWrite}
      />
    </QueryClientProvider>,
  )
}

describe('groupByDay', () => {
  it('puts the latest day first, the latest entry first within it, and sums each day', () => {
    const groups = groupByDay(
      [
        entry({ id: 'a', data: '2026-03-09', ore: '2.50', created_at: '2026-03-09T09:00:00Z' }),
        entry({ id: 'b', data: '2026-03-11', ore: '1.25' }),
        entry({ id: 'c', data: '2026-03-09', ore: '0.75', created_at: '2026-03-09T15:00:00Z' }),
      ],
      days,
    )
    expect(groups.map((group) => group.iso)).toEqual(['2026-03-11', '2026-03-09'])
    expect(groups[1]?.entries.map((item) => item.id)).toEqual(['c', 'a'])
    expect(groups[1]?.total).toBe('3.25')
    expect(groups[1]?.label).toMatch(/lunedì 9 marzo/)
  })
})

describe('TimeRegister', () => {
  it('shows the week total, the billable share and one section per day', () => {
    renderRegister([entry(), entry({ id: 'e2', data: '2026-03-11', ore: '1.25', fatturabile: false })])
    expect(screen.getByTestId('week-total')).toHaveTextContent('3,75 h')
    expect(screen.getByTestId('week-total')).toHaveTextContent('fatturabili 2,5 h')
    expect(screen.getByRole('region', { name: /lunedì 9 marzo/ })).toBeInTheDocument()
    expect(screen.getByText('non fatturabile')).toBeInTheDocument()
  })

  it('continues an entry as a new timer on the same deal with the same words', async () => {
    vi.mocked(api.POST).mockImplementation((() => ok({ id: 't1', deal_id: DEAL })) as never)
    renderRegister([entry()])
    await userEvent.click(await screen.findByRole('button', { name: /continua: analisi/i }))
    await waitFor(() => expect(vi.mocked(api.POST)).toHaveBeenCalledTimes(1))
    const [path, init] = vi.mocked(api.POST).mock.calls[0] as unknown as [string, { body: unknown }]
    expect(path).toBe('/api/time-entries/timer/start')
    expect(init.body).toEqual({ deal_id: DEAL, descrizione: 'Analisi', fatturabile: true })
  })

  it('offers no actions to a reader, and says when there is nothing', () => {
    renderRegister([], false)
    expect(screen.getByText(/Nessuna ora registrata/)).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })
})
