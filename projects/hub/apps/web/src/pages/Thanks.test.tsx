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

function mount(who: string) {
  const root = createRootRoute({ component: () => <Outlet /> })
  const thanks = createRoute({
    getParentRoute: () => root,
    path: '/thanks',
    validateSearch: (search: Record<string, unknown>): { who: 'freelance' | 'company' } => ({
      who: search.who === 'company' ? 'company' : 'freelance',
    }),
    component: Thanks,
  })
  const router = createRouter({
    routeTree: root.addChildren([thanks]),
    history: createMemoryHistory({ initialEntries: [`/thanks?who=${who}`] }),
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

  it('tells a company the area exists too (REB-380)', async () => {
    mount('company')
    await screen.findByRole('heading', { name: 'Grazie, ci siamo.' })
    expect(await screen.findByRole('link', { name: /Entra nella tua area/ })).toHaveAttribute(
      'href',
      '/login',
    )
  })
})
