import { createMemoryHistory, createRouter } from '@tanstack/react-router'
import { describe, expect, it } from 'vitest'
import { routeTree } from './router'

async function land(path: string) {
  const router = createRouter({ routeTree, history: createMemoryHistory({ initialEntries: [path] }) })
  await router.load()
  return router
}

describe('the old /accedi address', () => {
  it('lands on /login with the query string it was opened with', async () => {
    // An outreach mail links /hub/accedi?utm_... (REB-426): the redirect used to drop the
    // query, so the login page never saw which mail the person came from.
    const router = await land('/accedi?utm_source=email&utm_campaign=outreach-2026-09-r2&utm_term=abc1&da=home')
    expect(router.state.location.pathname).toBe('/login')
    const search = new URLSearchParams(router.state.location.searchStr)
    expect(search.get('utm_campaign')).toBe('outreach-2026-09-r2')
    expect(search.get('utm_term')).toBe('abc1')
    expect(search.get('da')).toBe('home')
  })
})

describe('the Italian addresses of the pages that were renamed (2026-11), kept for the mails and bookmarks out there', () => {
  it.each([
    ['/entra?t=tok_-1', '/verify', { t: 'tok_-1' }],
    ['/disiscrizione?t=tok_-1', '/unsubscribe', { t: 'tok_-1' }],
    ['/team/risposta?t=tok_-1&r=si', '/team/answer', { t: 'tok_-1', r: 'si' }],
    ['/team/risposta?t=tok_-1&r=no', '/team/answer', { t: 'tok_-1', r: 'no' }],
    ['/grazie?chi=azienda', '/thanks', { who: 'company' }],
    ['/grazie?chi=freelance', '/thanks', { who: 'freelance' }],
    ['/thanks?chi=azienda', '/thanks', { who: 'company' }],
    ['/thanks?who=company', '/thanks', { who: 'company' }],
  ])('%s opens %s with %j', async (old, now, search) => {
    const router = await land(old)
    expect(router.state.location.pathname).toBe(now)
    expect(router.state.matches.at(-1)!.search).toMatchObject(search)
  })

  it('keeps the whole query string of the company wizard, a campaign link already sent', async () => {
    const router = await land('/aziende?utm_source=linkedin&utm_campaign=q4&rif=ABC123&da=home&perk=guida')
    expect(router.state.location.pathname).toBe('/companies')
    expect(Object.fromEntries(new URLSearchParams(router.state.location.searchStr))).toEqual({
      utm_source: 'linkedin',
      utm_campaign: 'q4',
      'rif': 'ABC123',
      'da': 'home',
      perk: 'guida',
    })
  })

  it.each([
    ['/io', '/me'],
    ['/io/modifica', '/me/edit'],
    ['/io/modifica-azienda', '/me/edit-company'],
    ['/admin/accessi', '/admin/access'],
    ['/admin/agenti', '/admin/agents'],
    ['/admin/amministratori', '/admin/admins'],
    ['/admin/aziende', '/admin/companies'],
    ['/admin/aziende/c1', '/admin/companies/c1'],
    ['/admin/guida', '/admin/guide'],
    ['/admin/talenti', '/admin/talent'],
    ['/admin/talenti/f1', '/admin/talent/f1'],
    ['/admin/team/proposte', '/admin/team/proposals'],
    ['/admin/modelli-ai', '/admin/ai-models'],
    ['/admin/companies/nuova', '/admin/companies/new'],
  ])('%s lands on %s', async (old, now) => {
    const router = await land(old)
    expect(router.state.location.pathname).toBe(now)
  })

  it('carries a saved talent list through /admin/talenti, its filters under their new names', async () => {
    const router = await land('/admin/talenti?stato=contattato&posizione=CTO&remoto=in_sede&tariffa_min=50&con_accessi=true&q=chiuso')
    expect(router.state.location.pathname).toBe('/admin/talent')
    expect(router.state.location.search).toMatchObject({
      status: 'contacted',
      job_title: 'CTO',
      remote: 'on_site',
      min_rate: '50',
      with_logins: true,
      q: 'chiuso',
    })
  })

  it('carries a saved company list through /admin/aziende and the outcome through /admin/team/proposte', async () => {
    const companies = await land('/admin/aziende?stato=in_corso&origine=home&periodo_da=2026-10')
    expect(companies.state.location.search).toMatchObject({ status: 'in_progress', origin: 'home', period_from: '2026-10' })
    const proposals = await land('/admin/team/proposte?esito=richiesta')
    expect(proposals.state.location.search).toEqual({ outcome: 'richiesta' })
  })
})

describe('the team builder’s links made before the 2026-11 rename', () => {
  it.each([
    ['/team?proposal=p1&description=Una%20dashboard&people=3'],
    ['/team?proposta=p1&descrizione=Una%20dashboard&persone=3'],
  ])('%s opens on the same proposal, description and headcount', async (path) => {
    const router = await land(path)
    expect(router.state.matches.at(-1)!.search).toMatchObject({ proposal: 'p1', description: 'Una dashboard', people: 3 })
  })

  it('prefers the new name when a link carries both', async () => {
    const router = await land('/team?proposal=new1&proposta=old1')
    expect(router.state.matches.at(-1)!.search).toMatchObject({ proposal: 'new1' })
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

  it('answers the availability mail at /team/answer, public, with the token and the answer', async () => {
    // REB-517: the mail's links are `{hub_url}/team/answer?t=…&r=yes|no`.
    const router = createRouter({
      routeTree,
      history: createMemoryHistory({ initialEntries: ['/team/answer?t=abc_-1&r=no'] }),
    })
    await router.load()
    expect(router.state.location.pathname).toBe('/team/answer')
    expect(router.state.matches.map((match) => match.routeId)).toEqual([
      '__root__',
      '/public',
      '/public/team/answer',
    ])
    expect(router.state.location.search).toEqual({ t: 'abc_-1', r: 'no' })
  })
})

describe('«Richieste team» in the admin area', () => {
  it('has the list at /admin/team and a request at /admin/team/$id, behind the admin guard', async () => {
    for (const [path, leaf] of [
      ['/admin/team', '/signedIn/admin/team'],
      ['/admin/team/r1', '/signedIn/admin/team/$id'],
      // 0028: the static segment wins over `$id`.
      ['/admin/team/proposals', '/signedIn/admin/team/proposals'],
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

describe('«Valutazione» is gone (REB-842)', () => {
  it('has no route, and its addresses match no page', async () => {
    // The evaluation is a command now (`rebase cards-eval`): nobody corrects or rates in the app.
    const router = createRouter({ routeTree, history: createMemoryHistory({ initialEntries: ['/admin'] }) })
    expect(Object.keys(router.routesById).filter((id) => id.includes('valutazione'))).toEqual([])
    for (const path of ['/admin/valutazione', '/admin/valutazione/p1']) {
      const gone = createRouter({ routeTree, history: createMemoryHistory({ initialEntries: [path] }) })
      await gone.load()
      expect(gone.state.matches.map((match) => match.routeId).join()).not.toContain('valutazione')
    }
  })
})
