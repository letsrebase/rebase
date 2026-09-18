import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { formatElapsed } from './elapsed'
import { TimerBar } from './TimerBar'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PATCH: vi.fn(), DELETE: vi.fn() } }
})
vi.mock('@rebase/ui/sonner', () => ({ toast: { info: vi.fn(), success: vi.fn(), error: vi.fn() } }))

const USER = '33333333-3333-7333-8333-333333333333'
const DEAL = 'aaaaaaaa-aaaa-7aaa-8aaa-aaaaaaaaaaaa'
const deal = { id: DEAL, nome: 'Progetto Alfa' } as never

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) }) as never
}

const running = {
  id: 't1',
  user_id: USER,
  deal_id: DEAL,
  descrizione: 'Call con il cliente',
  fatturabile: true,
  started_at: '2026-03-12T09:00:00Z',
}

beforeAll(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date('2026-03-12T10:30:15Z'))
})
afterAll(() => vi.useRealTimers())

const typist = () => userEvent.setup({ advanceTimers: vi.advanceTimersByTime })

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  vi.mocked(api.PATCH).mockReset()
  vi.mocked(api.DELETE).mockReset()
})

function renderBar(canWrite = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <TimerBar deals={[deal]} userId={USER} canWrite={canWrite} />
    </QueryClientProvider>,
  )
}

describe('formatElapsed', () => {
  it('reads hh:mm:ss and does not cap the hours', () => {
    expect(formatElapsed(0)).toBe('00:00:00')
    expect(formatElapsed(5415)).toBe('01:30:15')
    expect(formatElapsed(30 * 3600)).toBe('30:00:00')
  })
})

describe('TimerBar', () => {
  it('starts a timer with what was typed, the deal still to be chosen', async () => {
    vi.mocked(api.GET).mockImplementation((() => ok(null)) as never)
    vi.mocked(api.POST).mockImplementation((() => ok({ ...running, deal_id: null })) as never)
    renderBar()
    const user = typist()
    await user.type(await screen.findByLabelText('Descrizione'), 'Call con il cliente')
    // Manual is the default; the stopwatch is one click away.
    await user.click(screen.getByRole('button', { name: /usa il timer/i }))
    await user.click(screen.getByRole('button', { name: /avvia/i }))
    await waitFor(() => expect(vi.mocked(api.POST)).toHaveBeenCalledTimes(1))
    const [path, init] = vi.mocked(api.POST).mock.calls[0] as unknown as [string, { body: unknown }]
    expect(path).toBe('/api/time-entries/timer/start')
    expect(init.body).toEqual({ deal_id: null, descrizione: 'Call con il cliente', fatturabile: true })
  })

  it('shows a running clock with its elapsed time, and stops it into an entry', async () => {
    vi.mocked(api.GET).mockImplementation((() => ok(running)) as never)
    vi.mocked(api.POST).mockImplementation(
      (() => ok({ id: 'e9', deal_id: DEAL, ore: '1.50' })) as never,
    )
    renderBar()
    // 09:00:00 to 10:30:15, from the server's `started_at`, not from a local count.
    expect(await screen.findByRole('timer')).toHaveTextContent('01:30:15')
    expect(screen.getByLabelText('Descrizione')).toHaveValue('Call con il cliente')
    act(() => vi.advanceTimersByTime(2000))
    expect(screen.getByRole('timer')).toHaveTextContent('01:30:17')

    await typist().click(screen.getByRole('button', { name: /stop/i }))
    await waitFor(() => expect(vi.mocked(api.POST)).toHaveBeenCalledTimes(1))
    const [path, init] = vi.mocked(api.POST).mock.calls[0] as unknown as [string, { body: unknown }]
    expect(path).toBe('/api/time-entries/timer/stop')
    expect(init.body).toMatchObject({ deal_id: DEAL, descrizione: 'Call con il cliente' })
    // The day is the browser's own calendar day.
    expect((init.body as { data: string }).data).toMatch(/^2026-03-12$/)
  })

  it('opens on the manual line and refuses to log without a deal', async () => {
    vi.mocked(api.GET).mockImplementation((() => ok(null)) as never)
    vi.mocked(api.POST).mockImplementation((() => ok({ id: 'e9', deal_id: DEAL, ore: '2.50' })) as never)
    renderBar()
    const user = typist()
    await user.type(await screen.findByLabelText('Ore'), '2,5')
    // No deal chosen: refused before any request, with a sentence rather than a 422.
    await user.click(screen.getByRole('button', { name: /aggiungi/i }))
    expect(vi.mocked(api.POST)).not.toHaveBeenCalled()
  })

  it('renders nothing for a reader', () => {
    renderBar(false)
    expect(screen.queryByLabelText('Descrizione')).not.toBeInTheDocument()
    expect(vi.mocked(api.GET)).not.toHaveBeenCalled()
  })
})
