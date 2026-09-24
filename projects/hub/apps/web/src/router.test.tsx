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
