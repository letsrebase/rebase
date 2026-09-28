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
import { Chooser } from './Chooser'

function mount() {
  const root = createRootRoute({ component: () => <Outlet /> })
  const chooser = createRoute({ getParentRoute: () => root, path: '/', component: Chooser })
  const freelance = createRoute({
    getParentRoute: () => root,
    path: '/freelance',
    component: () => <h1>Freelance</h1>,
  })
  const companies = createRoute({
    getParentRoute: () => root,
    path: '/companies',
    component: () => <h1>Companies</h1>,
  })
  const team = createRoute({
    getParentRoute: () => root,
    path: '/team',
    component: () => <h1>Team</h1>,
  })
  const router = createRouter({
    routeTree: root.addChildren([chooser, freelance, companies, team]),
    history: createMemoryHistory({ initialEntries: ['/'] }),
  })
  render(<RouterProvider router={router} />)
}

describe('the root chooser', () => {
  it('reads Sono un talento in place of the old developer-or-CTO split (REB-350)', async () => {
    mount()
    expect(await screen.findByRole('link', { name: /Sono un talento/ })).toHaveAttribute(
      'href',
      '/freelance',
    )
    expect(screen.queryByText(/developer o un CTO/)).toBeNull()
  })

  it('leaves the company door untouched', async () => {
    mount()
    expect(
      await screen.findByRole('link', { name: /Cerco persone per un progetto/ }),
    ).toHaveAttribute('href', '/companies')
  })

  it('links «Cerca un team» to the team builder, beside the two doors', async () => {
    mount()
    expect(await screen.findByRole('link', { name: 'Cerca un team' })).toHaveAttribute('href', '/team')
  })
})
