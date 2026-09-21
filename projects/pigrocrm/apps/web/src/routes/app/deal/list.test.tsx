/**
 * The deal list's header and filter row (design spec §4).
 *
 * The two chips are the two dashboard drill-throughs this list has always had -- no new
 * filter is invented here. They matter more than an ordinary filter chip: the URL is
 * what carries them (criterion 2 -- the same predicate the dashboard counted, evaluated
 * on the server), so a chip has to *navigate* rather than set local state, and it must
 * stay pressed for whichever one the URL arrived with. A chip that looked pressed while
 * the list behind it was unfiltered would be the page lying about what it shows.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { DealsList } from './list'

const navigate = vi.fn()

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return {
    ...actual,
    useNavigate: () => navigate,
    Link: ({ children }: { children: ReactNode }) => <a href="#">{children}</a>,
  }
})

const mockGet = vi.spyOn(api, 'GET')

beforeEach(() => {
  navigate.mockReset()
  mockGet.mockReset()
  mockGet.mockImplementation((() =>
    Promise.resolve({
      data: { items: [], next_cursor: null, custom_fields: [] },
      response: new Response(null, { status: 200 }),
    })) as never)
})

function renderList(
  filters: { fatturato_non_vinto?: boolean; da_fatturare?: boolean } = {},
  initialSearch = '',
) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <DealsList initialSearch={initialSearch} filters={filters} />
    </QueryClientProvider>,
  )
}

/**
 * What the page asks the router to make of the URL it is already on.
 *
 * The chips navigate with `search` as an *updater* rather than a literal object, which
 * is the whole point of I3: a literal replaces the query string and silently drops the
 * `?search=` term the command palette may have arrived with -- and `DealsListRoute`
 * keys the component on that term, so losing it remounts the list with an empty search
 * box. Applying the updater to a previous search is therefore the only way to assert
 * what actually reaches the URL.
 */
function nextSearch(previous: Record<string, unknown>): Record<string, unknown> {
  const call = navigate.mock.calls.at(-1)?.[0] as {
    to: string
    search: (prev: Record<string, unknown>) => Record<string, unknown>
  }
  expect(call.to).toBe('/app/deal/list')
  expect(typeof call.search).toBe('function')
  return call.search(previous)
}

describe('the deal list', () => {
  it('opens with its title as the page heading', async () => {
    renderList()
    expect(await screen.findByRole('heading', { level: 1, name: 'Deal' })).toBeInTheDocument()
  })

  it('offers the Kanban view as its header action', async () => {
    renderList()
    expect(await screen.findByRole('link', { name: /vista kanban/i })).toBeInTheDocument()
  })

  it('shows both drill-throughs as chips, none pressed on an unfiltered list', async () => {
    renderList()
    const filters = await screen.findByRole('search')
    expect(within(filters).getByRole('button', { name: 'Tutti' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(
      within(filters).getByRole('button', { name: 'Fatturato ma non vinto' }),
    ).toHaveAttribute('aria-pressed', 'false')
    expect(within(filters).getByRole('button', { name: 'Vinto ma da fatturare' })).toHaveAttribute(
      'aria-pressed',
      'false',
    )
  })

  it('keeps the chip of the filter the URL arrived with pressed', async () => {
    renderList({ da_fatturare: true })
    const filters = await screen.findByRole('search')
    expect(within(filters).getByRole('button', { name: 'Vinto ma da fatturare' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(within(filters).getByRole('button', { name: 'Tutti' })).toHaveAttribute(
      'aria-pressed',
      'false',
    )
  })

  it('navigates rather than filtering locally, because the server evaluates the predicate', async () => {
    renderList()
    const filters = await screen.findByRole('search')
    await userEvent.click(within(filters).getByRole('button', { name: 'Fatturato ma non vinto' }))
    expect(nextSearch({})).toEqual({ fatturato_non_vinto: true })
  })

  it('clears the filter when the pressed chip is pressed again', async () => {
    renderList({ fatturato_non_vinto: true })
    const filters = await screen.findByRole('search')
    await userEvent.click(within(filters).getByRole('button', { name: 'Fatturato ma non vinto' }))
    expect(nextSearch({ fatturato_non_vinto: true })).toEqual({})
  })

  /**
   * The live defect this closes: the command palette's «vedi tutti» opens this list with
   * `?search=acme`, and pressing a chip used to navigate with a literal `search` object
   * that had no `search` key -- so the term vanished from the URL, `DealsListRoute`'s
   * `key={search ?? ''}` remounted `DealsList`, and the search box came back empty. The
   * user lost a filter they never removed, with nothing on screen saying so.
   */
  it('keeps the URL’s own search term when a chip is pressed', async () => {
    renderList({}, 'acme')
    const filters = await screen.findByRole('search')
    await userEvent.click(within(filters).getByRole('button', { name: 'Vinto ma da fatturare' }))
    expect(nextSearch({ search: 'acme' })).toEqual({ search: 'acme', da_fatturare: true })
  })

  it('keeps the search term when «Tutti» clears the drill-through', async () => {
    renderList({ da_fatturare: true }, 'acme')
    const filters = await screen.findByRole('search')
    await userEvent.click(within(filters).getByRole('button', { name: 'Tutti' }))
    expect(nextSearch({ search: 'acme', da_fatturare: true })).toEqual({ search: 'acme' })
  })

  /** One at a time: the two predicates are disjoint by construction (a deal cannot be
   *  both not-won and won), so carrying the other one along would only ever produce an
   *  empty list. */
  it('replaces the other drill-through rather than adding to it', async () => {
    renderList({ fatturato_non_vinto: true })
    const filters = await screen.findByRole('search')
    await userEvent.click(within(filters).getByRole('button', { name: 'Vinto ma da fatturare' }))
    expect(nextSearch({ fatturato_non_vinto: true })).toEqual({ da_fatturare: true })
  })

  it('still states in words what an active filter is hiding', async () => {
    renderList({ da_fatturare: true })
    expect(await screen.findByRole('status')).toHaveTextContent(/solo i deal vinti/i)
  })
})
