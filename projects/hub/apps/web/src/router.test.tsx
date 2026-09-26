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

  it('answers the availability mail at /team/risposta, public, with the token and the answer', async () => {
    // REB-517: the mail's links are `{hub_url}/team/risposta?t=…&r=si|no`.
    const router = createRouter({
      routeTree,
      history: createMemoryHistory({ initialEntries: ['/team/risposta?t=abc_-1&r=no'] }),
    })
    await router.load()
    expect(router.state.location.pathname).toBe('/team/risposta')
    expect(router.state.matches.map((match) => match.routeId)).toEqual([
      '__root__',
      '/public',
      '/public/team/risposta',
    ])
    expect(router.state.location.search).toEqual({ t: 'abc_-1', r: 'no' })
  })
})

describe('«Richieste team» in the admin area', () => {
  it('has the list at /admin/team and a request at /admin/team/$id, behind the admin guard', async () => {
    for (const [path, leaf] of [
      ['/admin/team', '/signedIn/admin/team'],
      ['/admin/team/r1', '/signedIn/admin/team/$id'],
    ] as const) {
      const router = createRouter({ routeTree, history: createMemoryHistory({ initialEntries: [path] }) })
      await router.load()
      expect(router.state.location.pathname).toBe(path)
      expect(router.state.matches.map((match) => match.routeId)).toEqual([
        '__root__',
        '/signedIn',
        '/signedIn/admin',
        leaf,
      ])
    }
  })
})
