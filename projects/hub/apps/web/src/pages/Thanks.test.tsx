import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from '@tanstack/react-router'
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Thanks } from './Thanks'

function mount(chi: string) {
  const root = createRootRoute({ component: () => <Outlet /> })
  const thanks = createRoute({
    getParentRoute: () => root,
    path: '/thanks',
    validateSearch: (search: Record<string, unknown>): { chi: 'freelance' | 'azienda' } => ({
      chi: search.chi === 'azienda' ? 'azienda' : 'freelance',
    }),
    component: Thanks,
  })
  const router = createRouter({
    routeTree: root.addChildren([thanks]),
    history: createMemoryHistory({ initialEntries: [`/thanks?chi=${chi}`] }),
  })
  render(<RouterProvider router={router} />)
}

describe('the thank-you page', () => {
  it('tells a freelancer the area exists', async () => {
    mount('freelance')
    expect(await screen.findByRole('link', { name: /Entra nella tua area/ })).toHaveAttribute(
      'href',
      '/login',
    )
  })

  it('does not tell a company', async () => {
    mount('azienda')
    await screen.findByRole('heading', { name: 'Grazie, ci siamo.' })
    expect(screen.queryByRole('link', { name: /Entra nella tua area/ })).toBeNull()
  })
})
