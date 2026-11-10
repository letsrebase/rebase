import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Outlet, RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from '@tanstack/react-router'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AdminCreateCompany } from './CreateCompany'

const DEAL = '0192e0a0-0000-7000-8000-00000000d3a1'

function mount() {
  const root = createRootRoute({ component: () => <Outlet /> })
  const signedIn = createRoute({ getParentRoute: () => root, id: 'signedIn', component: () => <Outlet /> })
  const fresh = createRoute({ getParentRoute: () => signedIn, path: '/admin/companies/new', component: AdminCreateCompany })
  const list = createRoute({ getParentRoute: () => signedIn, path: '/admin/companies', component: () => <p>elenco</p> })
  const one = createRoute({ getParentRoute: () => signedIn, path: '/admin/companies/$id', component: () => <p>scheda</p> })
  const router = createRouter({
    routeTree: root.addChildren([signedIn.addChildren([fresh, list, one])]),
    history: createMemoryHistory({ initialEntries: ['/admin/companies/new'] }),
  })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

/** One field, filled by pasting: a keystroke is a render and a turn of the event loop, and
 *  the wizard has some 150 of them, which a starved machine spends more than the 20 s a test
 *  gets (REB-1029). A paste is one change event, and the form sees the same value. */
async function put(user: ReturnType<typeof userEvent.setup>, field: HTMLElement, text: string) {
  await user.click(field)
  await user.paste(text)
}

async function fill(user: ReturnType<typeof userEvent.setup>, deal: string) {
  await put(user, await screen.findByLabelText('Azienda'), 'Studio Rossi Srl')
  await put(user, screen.getByLabelText('Figura richiesta'), 'Backend developer')
  await put(user, screen.getByLabelText('Nome del referente'), 'Maria')
  await put(user, screen.getByLabelText('Cognome del referente'), 'Verdi')
  await put(user, screen.getByLabelText('Email'), 'maria@studiorossi.it')
  await put(user, screen.getByLabelText('Telefono'), '+39 345 1234567')
  await put(user, screen.getByLabelText('Progetto'), 'Un backend developer per tre mesi.')
  await user.type(screen.getByLabelText('Inizio desiderato'), '2026-12-01')
  await put(user, screen.getByLabelText('Durata'), '3 mesi')
  await put(user, screen.getByLabelText('Budget giornaliero (€)'), '500')
  await user.click(screen.getByRole('combobox', { name: 'Modalità' }))
  await user.click(await screen.findByRole('option', { name: 'Da remoto' }))
  if (deal) await put(user, screen.getByLabelText('Deal nel CRM'), deal)
}

afterEach(() => vi.restoreAllMocks())

describe('«Nuova richiesta»', () => {
  it('sends the wizard fields and the deal id parsed from the pasted address', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ id: 'c1', crm_status: 'linked' }), {
        status: 201,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    mount()
    const user = userEvent.setup()
    await fill(user, `https://crm.test/rebase/app/deal/${DEAL}`)
    await user.click(screen.getByRole('button', { name: 'Apri la richiesta' }))

    expect(await screen.findByText('scheda')).toBeInTheDocument()
    const [url, init] = fetch.mock.calls[0]!
    expect(url).toBe('/api/hub/admin/companies')
    expect(JSON.parse(String(init?.body))).toMatchObject({
      company_name: 'Studio Rossi Srl',
      daily_budget: '500.00',
      remote: 'remote',
      onsite_days: null,
      headcount: 1,
      deal_id: DEAL,
    })
  })

  it('sends no deal when the field is empty, and refuses a deal that is not one', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ id: 'c1' }), { status: 201, headers: { 'Content-Type': 'application/json' } }),
    )
    mount()
    const user = userEvent.setup()
    await fill(user, 'non un deal')
    await user.click(screen.getByRole('button', { name: 'Apri la richiesta' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Il deal non è un id')
    expect(fetch).not.toHaveBeenCalled()

    await user.clear(screen.getByLabelText('Deal nel CRM'))
    await user.click(screen.getByRole('button', { name: 'Apri la richiesta' }))
    await screen.findByText('scheda')
    expect(JSON.parse(String(fetch.mock.calls[0]![1]?.body)).deal_id).toBeNull()
  })
})
