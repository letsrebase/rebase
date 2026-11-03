import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AdminLogins } from './Logins'

function stats(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    total: 5,
    members: 2,
    total_members: 4,
    last_7_days: 3,
    recent: [
      { id: 'l-1', user_id: 'u-1', first_name: 'Ada', last_name: 'Lovelace', email: 'ada@studio.it', logged_at: '2026-09-11T12:04:00Z' },
    ],
    next_cursor: null,
    ...overrides,
  }
}

function answer(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <AdminLogins />
    </QueryClientProvider>,
  )
}

afterEach(() => vi.restoreAllMocks())

describe('the Accessi page', () => {
  it('shows the three numbers and the latest logins, each naming the member (ORB-158)', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, stats()))
    mount()
    await screen.findByText('ada@studio.it')
    expect(spy.mock.calls[0]![0]).toBe('/api/hub/logins')
    expect(screen.getByRole('heading', { name: /Accessi/ })).toHaveTextContent('5')
    expect(screen.getByText('Accessi', { selector: 'dt' }).nextElementSibling).toHaveTextContent('5')
    expect(screen.getByText('Membri entrati').nextElementSibling).toHaveTextContent('2su 4')
    expect(screen.getByText('Ultimi 7 giorni').nextElementSibling).toHaveTextContent('3')
    expect(screen.getByText('Ada Lovelace')).toBeInTheDocument()
    const row = screen.getByText('ada@studio.it').closest('tr')
    expect(row).toHaveTextContent(/11 set 2026/)
  })

  it('says when nobody has come in yet', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, stats({ total: 0, members: 0, last_7_days: 0, recent: [] })))
    mount()
    await screen.findByText('Nessun accesso ancora.')
    expect(screen.getByText('Membri entrati').nextElementSibling).toHaveTextContent('0su 4')
  })

  it('says when the numbers cannot be read', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(500, { detail: 'boom' }))
    mount()
    await screen.findByText('Non riesco a leggere gli accessi.')
  })

  it('debounces the search box and asks the server, not the browser, to narrow recenti', async () => {
    const spy = vi.spyOn(globalThis, 'fetch')
    spy.mockResolvedValueOnce(answer(200, stats()))
    spy.mockResolvedValueOnce(
      answer(
        200,
        stats({
          recent: [
            { id: 'l-2', user_id: 'u-2', first_name: 'Bob', last_name: 'Smith', email: 'bob@otherco.it', logged_at: '2026-09-11T13:00:00Z' },
          ],
        }),
      ),
    )
    mount()
    await screen.findByText('ada@studio.it')
    const user = userEvent.setup()
    await user.type(screen.getByLabelText('Cerca accessi'), 'Bob')
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2), { timeout: 2000 })
    expect(spy.mock.calls[1]![0]).toBe('/api/hub/logins?q=Bob')
    await waitFor(() => expect(screen.queryByText('ada@studio.it')).toBeNull())
    expect(screen.getByText('bob@otherco.it')).toBeInTheDocument()
    // The four counters stay a plain read of the whole table -- unaffected by the search.
    expect(screen.getByText('Accessi', { selector: 'dt' }).nextElementSibling).toHaveTextContent('5')
  })

  it('loads the next page of recenti on demand', async () => {
    const spy = vi.spyOn(globalThis, 'fetch')
    spy.mockResolvedValueOnce(answer(200, stats({ next_cursor: 'cursor-1' })))
    spy.mockResolvedValueOnce(
      answer(
        200,
        stats({
          recent: [
            { id: 'l-2', user_id: 'u-2', first_name: 'Bob', last_name: 'Smith', email: 'bob@otherco.it', logged_at: '2026-09-11T13:00:00Z' },
          ],
        }),
      ),
    )
    mount()
    await screen.findByText('ada@studio.it')
    expect(screen.getByText('Mostrati 1 accessi, ce ne sono altri.')).toBeInTheDocument()

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Mostra altri' }))
    await screen.findByText('bob@otherco.it')
    expect(screen.getByText('ada@studio.it')).toBeInTheDocument()
    expect(spy.mock.calls[1]![0]).toBe('/api/hub/logins?cursor=cursor-1')
    expect(screen.queryByRole('button', { name: 'Mostra altri' })).toBeNull()
  })
})
