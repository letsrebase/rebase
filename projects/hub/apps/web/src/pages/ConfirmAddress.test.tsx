import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Outlet, RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from '@tanstack/react-router'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ConfirmAddress } from './ConfirmAddress'

function mount(path: string) {
  const root = createRootRoute({ component: () => <Outlet /> })
  const page = createRoute({
    getParentRoute: () => root,
    path: '/confirm',
    component: ConfirmAddress,
    validateSearch: (search: Record<string, unknown>): { t: string } => ({ t: typeof search.t === 'string' ? search.t : '' }),
  })
  const login = createRoute({ getParentRoute: () => root, path: '/login', component: () => <p>login</p> })
  const router = createRouter({ routeTree: root.addChildren([page, login]), history: createMemoryHistory({ initialEntries: [path] }) })
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

afterEach(() => vi.restoreAllMocks())

describe('«Conferma l\'indirizzo»', () => {
  it('posts the token only on the click, then says it is done', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    )
    mount('/confirm?t=abc')
    const button = await screen.findByRole('button', { name: 'Confermo' })
    expect(fetch).not.toHaveBeenCalled()
    await userEvent.click(button)
    expect(await screen.findByText("Fatto: l'indirizzo è confermato. Grazie.")).toBeInTheDocument()
    expect(fetch.mock.calls[0]![0]).toBe('/api/hub/campaigns/confirm?t=abc')
  })

  it('says the link does not work when the hub refuses it', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ detail: 'Il link non è valido o è scaduto.' }), {
        status: 410,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    mount('/confirm?t=old')
    await userEvent.click(await screen.findByRole('button', { name: 'Confermo' }))
    expect(await screen.findByText('Questo link non funziona')).toBeInTheDocument()
  })

  it('says the link does not work without a token', async () => {
    mount('/confirm')
    expect(await screen.findByText('Questo link non funziona')).toBeInTheDocument()
  })
})
