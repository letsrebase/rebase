/**
 * The people list's header and filter row (design spec §4). Same three assertions as
 * `customers/index.test.tsx`, on the page next to it: the title and «Nuova persona» come
 * from `PageHeader`, and the search box lives in the filter row above the table. The
 * «Azienda» filter is new: it navigates rather than filtering locally (same reasoning
 * as `deal/list.test.tsx`'s chips) because `customer_id` is a real API filter
 * (`GET /api/people?customer_id=`), and its value has to survive a remount the same way
 * the URL's own `?search=` term does.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { PeoplePage } from './PeoplePage'
import { parsePeopleSearch } from './search'

const navigate = vi.fn()

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return { ...actual, useNavigate: () => navigate }
})

vi.mock('@/lib/auth', () => ({ useCanWrite: () => true }))

const mockGet = vi.spyOn(api, 'GET')

function customerPage(items: { id: string; ragione_sociale: string }[]) {
  return Promise.resolve({
    data: { items, next_cursor: null },
    response: new Response(null, { status: 200 }),
  }) as never
}

beforeEach(() => {
  navigate.mockReset()
  mockGet.mockReset()
  mockGet.mockImplementation(((path: string) => {
    if (path === '/api/customers') {
      return customerPage([
        { id: 'cust-zeta', ragione_sociale: 'Zeta Srl' },
        { id: 'cust-acme', ragione_sociale: 'ACME Srl' },
      ])
    }
    if (path === '/api/customers/{customer_id}') {
      return Promise.resolve({
        data: { id: 'cust-acme', ragione_sociale: 'ACME Srl' },
        response: new Response(null, { status: 200 }),
      }) as never
    }
    return Promise.resolve({
      data: { items: [], next_cursor: null, custom_fields: [] },
      response: new Response(null, { status: 200 }),
    })
  }) as never)
})

function renderPage(customerId?: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <PeoplePage initialSearch="" customerId={customerId} />
    </QueryClientProvider>,
  )
}

/** The last `?customer_id=` the page asked `GET /api/people` for, or `undefined`
 *  when it asked for none. */
function lastRequestedCustomerId(): unknown {
  const calls = mockGet.mock.calls.filter((call) => call[0] === '/api/people')
  const last = calls[calls.length - 1]?.[1] as
    | { params?: { query?: Record<string, unknown> } }
    | undefined
  return last?.params?.query?.customer_id
}

/** What the page asks the router to make of the URL it is already on -- an updater,
 *  not a literal object, the same contract `deal/list.test.tsx`'s `nextSearch` checks,
 *  so the `?search=` term the box may hold survives a company chosen from the select. */
function nextSearch(previous: Record<string, unknown>): Record<string, unknown> {
  const call = navigate.mock.calls.at(-1)?.[0] as {
    search: (prev: Record<string, unknown>) => Record<string, unknown>
  }
  expect(typeof call.search).toBe('function')
  return call.search(previous)
}

describe('the people list', () => {
  it('opens with its title as the page heading', async () => {
    renderPage()
    expect(await screen.findByRole('heading', { level: 1, name: 'Persone' })).toBeInTheDocument()
  })

  it('offers its primary action in the header', async () => {
    renderPage()
    expect(await screen.findByRole('button', { name: /nuova persona/i })).toBeInTheDocument()
  })

  it('puts the search box in the filter row', async () => {
    renderPage()
    const filters = await screen.findByRole('search')
    expect(within(filters).getByPlaceholderText(/cerca per nome/i)).toBeInTheDocument()
  })

  it('offers an «Azienda» filter in the filter row, «Tutte le aziende» to begin with', async () => {
    renderPage()
    const filters = await screen.findByRole('search')
    expect(within(filters).getByLabelText('Filtra per azienda')).toHaveTextContent(
      'Tutte le aziende',
    )
    expect(lastRequestedCustomerId()).toBeUndefined()
  })

  it('searches the companies on the server instead of loading them all', async () => {
    renderPage()
    const filters = await screen.findByRole('search')
    await userEvent.click(within(filters).getByLabelText('Filtra per azienda'))

    expect(await screen.findByRole('button', { name: 'ACME Srl' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Zeta Srl' })).toBeInTheDocument()
    const asked = mockGet.mock.calls.find((call) => call[0] === '/api/customers')?.[1] as unknown as {
      params: { query: Record<string, unknown> }
    }
    expect(asked.params.query.limit).toBeLessThan(200)
  })

  it('reads its initial company from the URL', async () => {
    renderPage('cust-acme')
    const filters = await screen.findByRole('search')
    expect(await within(filters).findByText('ACME Srl')).toBeInTheDocument()
    expect(lastRequestedCustomerId()).toBe('cust-acme')
  })

  it('navigates with the chosen company rather than filtering locally', async () => {
    renderPage()
    const filters = await screen.findByRole('search')
    await userEvent.click(within(filters).getByLabelText('Filtra per azienda'))
    await userEvent.click(await screen.findByRole('button', { name: 'ACME Srl' }))

    expect(nextSearch({ search: 'mario' })).toEqual({
      search: 'mario',
      customer_id: 'cust-acme',
    })
  })

  it('clears the company filter by removing the chosen company', async () => {
    renderPage('cust-acme')
    const filters = await screen.findByRole('search')
    await userEvent.click(within(filters).getByLabelText('Filtra per azienda'))
    await userEvent.click(await screen.findByRole('button', { name: 'Rimuovi il cliente' }))

    expect(nextSearch({ customer_id: 'cust-acme' })).toEqual({})
  })
})

/**
 * A `customerId` already in the URL is named by reading that one customer, not by finding
 * it in a list: while the read is in flight, or if it fails for good, the trigger must say
 * so rather than render blank.
 */
describe('the trigger label with a customer_id in the URL', () => {
  it('shows a loading label rather than blank while the customer is being read', async () => {
    mockGet.mockImplementation(((path: string) =>
      path === '/api/customers/{customer_id}'
        ? new Promise(() => {})
        : Promise.resolve({
            data: { items: [], next_cursor: null, custom_fields: [] },
            response: new Response(null, { status: 200 }),
          })) as never)

    renderPage('cust-acme')
    const filters = await screen.findByRole('search')
    expect(within(filters).getByLabelText('Filtra per azienda')).toHaveTextContent('Caricamento…')
  })

  it('shows an error label rather than blank when the customer cannot be read, permanently', async () => {
    mockGet.mockImplementation(((path: string) =>
      path === '/api/customers/{customer_id}'
        ? Promise.resolve({
            error: { detail: 'boom' },
            response: new Response(null, { status: 500 }),
          })
        : Promise.resolve({
            data: { items: [], next_cursor: null, custom_fields: [] },
            response: new Response(null, { status: 200 }),
          })) as never)

    renderPage('cust-acme')
    const filters = await screen.findByRole('search')
    expect(
      await within(filters).findByText('Azienda non disponibile'),
    ).toBeInTheDocument()
  })
})

/**
 * `GET /api/people`'s `customer_id` is typed `UUID | None` on the server
 * (`apps/api/src/pigrocrm_api/routers/people.py`), so a `?customer_id=` that is not a
 * UUID -- a stale bookmark, a typo, a hand-edited query string -- must never reach the
 * request: it would 422, and `unwrap()` turns that into `DataTable`'s error banner over
 * the whole Persone list, not just an ignored filter. `validateSearch` is where that is
 * caught, the same place an empty `?search=` is already dropped.
 */
describe('validateSearch', () => {
  it('drops a customer_id that is not shaped like a UUID', () => {
    expect(parsePeopleSearch({ customer_id: 'cust-acme' }).customer_id).toBeUndefined()
    expect(parsePeopleSearch({ customer_id: '' }).customer_id).toBeUndefined()
    expect(parsePeopleSearch({ customer_id: 'not-a-uuid' }).customer_id).toBeUndefined()
  })

  it('keeps a customer_id shaped like a UUID', () => {
    const uuid = '3fa85f64-5717-4562-b3fc-2c963f66afa6'
    expect(parsePeopleSearch({ customer_id: uuid }).customer_id).toBe(uuid)
  })
})

/**
 * The full URL-to-request path for a malformed `customer_id`, end to end:
 * `PeopleRoute` reads `Route.useSearch()` -- already filtered through `validateSearch`
 * above -- and passes its result straight into `PeoplePage` as `customerId`. Feeding
 * `parsePeopleSearch`'s own output into `renderPage` is what proves a garbage URL
 * value never reaches the API and never leaves the filter looking like nothing is
 * chosen (blank, per the review that raised this) rather than «Tutte le aziende».
 */
describe('a garbage customer_id in the URL', () => {
  it('turns into no filter at all, «Tutte le aziende» shown', async () => {
    const { customer_id } = parsePeopleSearch({
      customer_id: 'cust-acme;DROP TABLE customers',
    })

    renderPage(customer_id)
    const filters = await screen.findByRole('search')
    expect(within(filters).getByLabelText('Filtra per azienda')).toHaveTextContent(
      'Tutte le aziende',
    )
    expect(lastRequestedCustomerId()).toBeUndefined()
  })
})
