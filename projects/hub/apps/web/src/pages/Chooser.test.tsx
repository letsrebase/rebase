import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from '@tanstack/react-router'
import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { REFERRAL_STORAGE_KEY } from '@/lib/utm'
import { Chooser } from './Chooser'

function mount(initialEntry = '/') {
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
    history: createMemoryHistory({ initialEntries: [initialEntry] }),
  })
  render(<RouterProvider router={router} />)
}

afterEach(() => sessionStorage.clear())

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

  it('remembers ?ref= before either door drops the query string (CodeRabbit, P-REB-44)', async () => {
    // Regression: `<Link to="/freelance">` carries no `search`, so a visitor who
    // opened /hub/?ref=CODE and picked a door lost the code the moment they clicked,
    // since nothing had stored it yet.
    mount('/?ref=ABC123')
    await screen.findByRole('link', { name: /Sono un talento/ })
    expect(sessionStorage.getItem(REFERRAL_STORAGE_KEY)).toBe('ABC123')
  })

  it('still remembers `?rif=`, the name of the referral link before the 2026-11 rename, and writes `rebase.ref`', async () => {
    // Members shared /hub/?rif=CODE: those links are out in the world for good.
    mount('/?rif=ABC123')
    await screen.findByRole('link', { name: /Sono un talento/ })
    expect(sessionStorage.getItem(REFERRAL_STORAGE_KEY)).toBe('ABC123')
    expect(sessionStorage.getItem('rebase.rif')).toBeNull()
  })
})
