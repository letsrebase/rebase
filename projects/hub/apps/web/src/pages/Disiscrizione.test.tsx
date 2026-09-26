import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Outlet, RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from '@tanstack/react-router'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Disiscrizione } from './Disiscrizione'

function mount(path: string) {
  const root = createRootRoute({ component: () => <Outlet /> })
  const page = createRoute({
    getParentRoute: () => root,
    path: '/disiscrizione',
    component: Disiscrizione,
    validateSearch: (search: Record<string, unknown>): { t: string } => ({ t: typeof search.t === 'string' ? search.t : '' }),
  })
  const router = createRouter({ routeTree: root.addChildren([page]), history: createMemoryHistory({ initialEntries: [path] }) })
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

afterEach(() => vi.restoreAllMocks())

describe('«Disiscrizione»', () => {
  it('posts the token only on the click, then says it is done', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    )
    mount('/disiscrizione?t=abc')
    const button = await screen.findByRole('button', { name: 'Non scrivermi più' })
    expect(fetch).not.toHaveBeenCalled()
    await userEvent.click(button)
    expect(await screen.findByText('Fatto: non riceverai più queste mail da rebase.')).toBeInTheDocument()
    expect(fetch.mock.calls[0]![0]).toBe('/api/hub/campagne/disiscrizione?t=abc')
  })

  it('says the link is incomplete without a token', async () => {
    mount('/disiscrizione')
    expect(await screen.findByText(/Il link non è completo/)).toBeInTheDocument()
  })
})
