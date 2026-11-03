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
import { Shell } from './Shell'

function mount() {
  const root = createRootRoute({ component: () => <Outlet /> })
  const home = createRoute({
    getParentRoute: () => root,
    path: '/',
    component: () => <Shell>a step</Shell>,
  })
  const router = createRouter({
    routeTree: root.addChildren([home]),
    history: createMemoryHistory({ initialEntries: ['/'] }),
  })
  render(<RouterProvider router={router} />)
}

describe('the public frame', () => {
  it('signs its footer with the policies alone, never with a fixture or the studio', async () => {
    // ORB-97: this footer said «Orbiters è un progetto di Studio Rossi», the suite's
    // stock customer, and served it from production for a week. REB-704 took the
    // studio's link off both footers; the site's own is pinned in landing-pages.test.ts,
    // and two footers that disagree is exactly how the first one drifted.
    mount()
    expect(await screen.findByRole('link', { name: 'Privacy' })).toHaveAttribute('href', 'https://letsrebase.com/privacy')
    expect(screen.getByRole('link', { name: 'Termini' })).toHaveAttribute('href', 'https://letsrebase.com/terms')
    expect(document.body.textContent).not.toMatch(/Studio Rossi|example\.com|Humancraft/)
  })

  it('links the two policy pages on the site they belong to', async () => {
    // Absolute, not relative: the hub is served under /hub/ and the policies are the
    // website's own pages, so a relative href would resolve inside the SPA and 404.
    mount()
    expect(await screen.findByRole('link', { name: 'Privacy' })).toHaveAttribute(
      'href',
      'https://letsrebase.com/privacy',
    )
    expect(screen.getByRole('link', { name: 'Termini' })).toHaveAttribute(
      'href',
      'https://letsrebase.com/terms',
    )
  })
})
