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
import { describe, expect, it } from 'vitest'
import { readOrigin } from '@/lib/utm'
import { Team } from './Team'

/** The page on a little router of its own, so the beta box's link has a wizard to open. */
function mount() {
  const root = createRootRoute({ component: () => <Outlet /> })
  const team = createRoute({ getParentRoute: () => root, path: '/team', component: Team })
  const companies = createRoute({
    getParentRoute: () => root,
    path: '/companies',
    component: () => <h1>Cerchi persone</h1>,
  })
  const router = createRouter({
    routeTree: root.addChildren([team, companies]),
    history: createMemoryHistory({ initialEntries: ['/team'] }),
  })
  render(<RouterProvider router={router} />)
  return router
}

describe('the public team page', () => {
  it('asks for the project under its heading, with the builder’s box and button', async () => {
    mount()
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Descrivi il progetto, ti proponiamo il team' }),
    ).toBeInTheDocument()
    expect(screen.getByLabelText('Descrizione del progetto')).toHaveAttribute(
      'placeholder',
      'Descrivi il progetto: cosa va fatto, per quanto tempo, dove, con che tecnologie',
    )
    expect(screen.getByRole('button', { name: 'Proponi il team' })).toBeInTheDocument()
  })

  it('ends on the beta box, whose button opens the company wizard with da=team-builder', async () => {
    const user = userEvent.setup()
    const router = mount()
    const beta = await screen.findByRole('complementary', { name: 'Talent cloud' })
    expect(beta).toHaveTextContent(
      'Il team builder è in beta e senza limiti. Le aziende che entrano nel talent cloud vedono i profili per nome, sfogliano tutto il cloud e chiedono i talenti direttamente.',
    )
    const link = screen.getByRole('link', { name: 'Chiedi l’accesso al talent cloud' })
    expect(link).toHaveAttribute('href', '/companies?da=team-builder')

    await user.click(link)
    await waitFor(() => expect(router.state.location.pathname).toBe('/companies'))
    // The wizard reads its origin with `readOrigin`: the parameter must be the one it reads.
    expect(readOrigin(router.state.location.searchStr)).toBe('team-builder')
  })
})
