import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from '@tanstack/react-router'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Accedi } from './Accedi'

function answer(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function mount() {
  const root = createRootRoute({ component: () => <Outlet /> })
  const login = createRoute({ getParentRoute: () => root, path: '/login', component: Accedi })
  const freelance = createRoute({ getParentRoute: () => root, path: '/freelance', component: () => <h1>Wizard</h1> })
  const router = createRouter({
    routeTree: root.addChildren([login, freelance]),
    history: createMemoryHistory({ initialEntries: ['/login'] }),
  })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

afterEach(() => vi.restoreAllMocks())

describe('/login', () => {
  it('says the same thing for any address', async () => {
    // A fresh Response per call: this test drives two real submissions, and a `Response`
    // body can only be read once (`mockResolvedValue` would hand out the same instance
    // twice, which no live network round trip ever does).
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(() => Promise.resolve(answer(202, { ok: true })))
    mount()
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText('Email'), 'ada@studio.it')
    await user.click(screen.getByRole('button', { name: 'Mandami il link' }))
    const sentence = await screen.findByText(/Se sei dentro, ti abbiamo scritto/)
    expect(fetchSpy).toHaveBeenCalledWith(
      '/api/hub/auth/link',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ email: 'ada@studio.it' }) }),
    )

    await user.click(screen.getByRole('button', { name: /chiedine un altro/ }))
    await user.clear(await screen.findByLabelText('Email'))
    await user.type(screen.getByLabelText('Email'), 'nessuno@studio.it')
    await user.click(screen.getByRole('button', { name: 'Mandami il link' }))
    expect((await screen.findByText(/Se sei dentro, ti abbiamo scritto/)).textContent).toBe(
      sentence.textContent,
    )
  })

  it('shows the API sentence when the mail is not active yet', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(503, { detail: "L'accesso via email non è ancora attivo. Riprova più avanti." }),
    )
    mount()
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText('Email'), 'ada@studio.it')
    await user.click(screen.getByRole('button', { name: 'Mandami il link' }))
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent('non è ancora attivo'),
    )
  })
})
