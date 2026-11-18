import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from '@tanstack/react-router'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { NoAccount } from './NoAccount'

function answer(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function mount() {
  const root = createRootRoute({ component: () => <Outlet /> })
  const page = createRoute({ getParentRoute: () => root, path: '/login/no-account', component: NoAccount })
  const login = createRoute({ getParentRoute: () => root, path: '/login', component: () => <h1>Login</h1> })
  const chooser = createRoute({ getParentRoute: () => root, path: '/', component: () => <h1>Chooser</h1> })
  const router = createRouter({
    routeTree: root.addChildren([page, login, chooser]),
    history: createMemoryHistory({ initialEntries: ['/login/no-account'] }),
  })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('/login/no-account', () => {
  it('names the provider and the address, and offers the magic link and the chooser', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(200, { provider: 'github', email: 'ada@elsewhere.it' }),
    )
    mount()
    expect(await screen.findByRole('heading', { name: 'Non troviamo il tuo account' })).toBeTruthy()
    expect(screen.getByText(/GitHub ci ha detto/)).toBeTruthy()
    expect(screen.getByText('ada@elsewhere.it')).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Entra con il link via mail' }).getAttribute('href')).toBe('/login')
    expect(screen.getByRole('link', { name: 'Raccontaci chi sei' }).getAttribute('href')).toBe('/')
  })

  it('says the same without an address when this browser holds none', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(404, { detail: 'Nessun accesso in sospeso.' }))
    mount()
    expect(await screen.findByText(/non è quello di nessun account rebase/)).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Entra con il link via mail' })).toBeTruthy()
  })
})
