import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Outlet, RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from '@tanstack/react-router'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AdminOpenEngagement } from './OpenEngagement'

function answer(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

const PERSON = { id: 'f1', first_name: 'Ada', last_name: 'Lovelace', email: 'ada@studio.it' }
const OPEN = {
  id: 'c1',
  company_name: 'Rossi Studio',
  contact_person: 'Mario Rossi',
  requested_role: 'Backend developer',
  period_from: '2026-10-01',
  status: 'new',
}
const COMPANIES = { total: 1, items: [OPEN], by_status: {}, next_cursor: null }
const FORM = 'https://pigro.test/rebase/app/engagements?hub_match_id=e1&freelancer_id=o1&deal_id=d1&client_id=k1'

/** Routes by `"<method> <url>"`, each handler answering in turn when it is a list. */
function routeFetch(handlers: Record<string, unknown[]>) {
  const calls: string[] = []
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const key = `${init?.method ?? 'GET'} ${String(input)}`
    calls.push(key)
    const queue = handlers[key]
    if (!queue) throw new Error(`unhandled fetch in this test: ${key}`)
    const next = queue.length > 1 ? queue.shift() : queue[0]
    return next instanceof Response ? next : answer(200, next)
  })
  return calls
}

function mount() {
  const root = createRootRoute({ component: () => <Outlet /> })
  const signedIn = createRoute({ getParentRoute: () => root, id: 'signedIn', component: () => <Outlet /> })
  const page = createRoute({
    getParentRoute: () => signedIn,
    path: '/admin/freelance/$id/match/new',
    component: AdminOpenEngagement,
  })
  const detail = createRoute({
    getParentRoute: () => signedIn,
    path: '/admin/freelance/$id',
    component: () => <p>scheda</p>,
  })
  const router = createRouter({
    routeTree: root.addChildren([signedIn.addChildren([page, detail])]),
    history: createMemoryHistory({ initialEntries: ['/admin/freelance/f1/match/new'] }),
  })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

const assign = vi.fn()
beforeEach(() => {
  assign.mockReset()
  Object.defineProperty(window, 'location', { configurable: true, value: { ...window.location, assign } })
})
afterEach(() => vi.restoreAllMocks())

const BASE = {
  'GET /api/hub/freelancers/f1': [PERSON],
  'GET /api/hub/companies?limit=50': [COMPANIES],
}

describe('«Crea match»', () => {
  it('hands the pair to the CRM and sends the admin to its form, filled in', async () => {
    const calls = routeFetch({
      ...BASE,
      'POST /api/hub/freelancers/f1/engagement-handoff': [{ state: 'ready', url: FORM, engagement_id: 'e1', message: null }],
    })
    mount()

    const open = await screen.findByRole('button', { name: 'Apri nel CRM' })
    expect(open).toBeDisabled()
    await userEvent.click(await screen.findByRole('button', { name: /Rossi Studio/ }))
    await userEvent.click(open)

    await waitFor(() => expect(assign).toHaveBeenCalledWith(FORM))
    expect(calls.filter((call) => call.includes('engagement-handoff'))).toHaveLength(1)
  })

  it('says a request with no deal is not in the CRM and offers «Riprova» instead of a blank form', async () => {
    const calls = routeFetch({
      ...BASE,
      'POST /api/hub/freelancers/f1/engagement-handoff': [
        { state: 'no_deal', url: null, engagement_id: null, message: 'Questa richiesta non è ancora nel CRM.' },
        { state: 'ready', url: FORM, engagement_id: 'e1', message: null },
      ],
      'POST /api/hub/companies/c1/crm/link': [{ ...OPEN, crm_status: 'linked' }],
    })
    mount()

    await userEvent.click(await screen.findByRole('button', { name: /Rossi Studio/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Apri nel CRM' }))
    expect(await screen.findByText('Questa richiesta non è ancora nel CRM.')).toBeInTheDocument()
    expect(assign).not.toHaveBeenCalled()

    await userEvent.click(screen.getByRole('button', { name: 'Riprova' }))

    await waitFor(() => expect(assign).toHaveBeenCalledWith(FORM))
    expect(calls.filter((call) => call.startsWith('POST /api/hub/companies/c1/crm/link'))).toHaveLength(1)
    expect(calls.filter((call) => call.includes('engagement-handoff'))).toHaveLength(2)
  })

  it('shows the CRM’s refusal in its words and goes nowhere', async () => {
    routeFetch({
      ...BASE,
      'POST /api/hub/freelancers/f1/engagement-handoff': [
        answer(409, { detail: 'la partita IVA è già di un altro freelance dell’hub' }),
      ],
    })
    mount()

    await userEvent.click(await screen.findByRole('button', { name: /Rossi Studio/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Apri nel CRM' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('la partita IVA è già di un altro freelance dell’hub')
    expect(assign).not.toHaveBeenCalled()
  })
})
