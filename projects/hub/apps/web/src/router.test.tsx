import { createMemoryHistory, createRouter } from '@tanstack/react-router'
import { describe, expect, it } from 'vitest'
import { routeTree } from './router'

describe('the old /accedi address', () => {
  it('lands on /login with the query string it was opened with', async () => {
    // An outreach mail links /hub/accedi?utm_... (REB-426): the redirect used to drop the
    // query, so the login page never saw which mail the person came from.
    const router = createRouter({
      routeTree,
      history: createMemoryHistory({
        initialEntries: ['/accedi?utm_source=email&utm_campaign=outreach-2026-09-r2&utm_term=abc1'],
      }),
    })
    await router.load()
    expect(router.state.location.pathname).toBe('/login')
    expect(new URLSearchParams(router.state.location.searchStr).get('utm_campaign')).toBe(
      'outreach-2026-09-r2',
    )
    expect(new URLSearchParams(router.state.location.searchStr).get('utm_term')).toBe('abc1')
  })
})

describe('the bare /admin address', () => {
  it('lands on /admin/talent instead of an empty panel (ORB-106)', async () => {
    // session.js's landingRoute() sends an admin's "Accedi" click straight to
    // /hub/admin: this router must have a real page there, not AdminGuard's own
    // <Outlet /> matching nothing.
    const router = createRouter({
      routeTree,
      history: createMemoryHistory({ initialEntries: ['/admin'] }),
    })
    await router.load()
    expect(router.state.location.pathname).toBe('/admin/talent')
  })
})

describe('the team builder', () => {
  it('is /team, under the public layout with the wizards’ chrome', async () => {
    const router = createRouter({
      routeTree,
      history: createMemoryHistory({ initialEntries: ['/team'] }),
    })
    await router.load()
    expect(router.state.location.pathname).toBe('/team')
    expect(router.state.matches.map((match) => match.routeId)).toEqual(['__root__', '/public', '/public/team'])
  })
})
