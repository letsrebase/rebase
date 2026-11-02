import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Outlet, RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from '@tanstack/react-router'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AdminCreaAzienda } from './CreaAzienda'

const DEAL = '0192e0a0-0000-7000-8000-00000000d3a1'

function mount() {
  const root = createRootRoute({ component: () => <Outlet /> })
  const signedIn = createRoute({ getParentRoute: () => root, id: 'signedIn', component: () => <Outlet /> })
  const fresh = createRoute({ getParentRoute: () => signedIn, path: '/admin/companies/nuova', component: AdminCreaAzienda })
  const list = createRoute({ getParentRoute: () => signedIn, path: '/admin/companies', component: () => <p>elenco</p> })
  const one = createRoute({ getParentRoute: () => signedIn, path: '/admin/companies/$id', component: () => <p>scheda</p> })
  const router = createRouter({
    routeTree: root.addChildren([signedIn.addChildren([fresh, list, one])]),
    history: createMemoryHistory({ initialEntries: ['/admin/companies/nuova'] }),
  })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

async function fill(user: ReturnType<typeof userEvent.setup>, deal: string) {
  await user.type(await screen.findByLabelText('Azienda'), 'Studio Rossi Srl')
  await user.type(screen.getByLabelText('Figura richiesta'), 'Backend developer')
  await user.type(screen.getByLabelText('Nome del referente'), 'Maria')
  await user.type(screen.getByLabelText('Cognome del referente'), 'Verdi')
  await user.type(screen.getByLabelText('Email'), 'maria@studiorossi.it')
  await user.type(screen.getByLabelText('Telefono'), '+39 345 1234567')
  await user.type(screen.getByLabelText('Progetto'), 'Un backend developer per tre mesi.')
  await user.type(screen.getByLabelText('Inizio desiderato'), '2026-12-01')
  await user.type(screen.getByLabelText('Durata'), '3 mesi')
  await user.type(screen.getByLabelText('Budget giornaliero (€)'), '500')
  await user.click(screen.getByRole('combobox', { name: 'Modalità' }))
  await user.click(await screen.findByRole('option', { name: 'Da remoto' }))
  if (deal) await user.type(screen.getByLabelText('Deal nel CRM'), deal)
}

afterEach(() => vi.restoreAllMocks())

describe('«Nuova richiesta»', () => {
  it('sends the wizard fields and the deal id parsed from the pasted address', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ id: 'c1', crm_stato: 'collegato' }), {
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
      nome_azienda: 'Studio Rossi Srl',
      budget_giornaliero: '500.00',
      remoto: 'remoto',
      giorni_presenza: null,
      numero_risorse: 1,
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
