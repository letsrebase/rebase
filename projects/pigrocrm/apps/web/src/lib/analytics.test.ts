import createClient from 'openapi-fetch'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@rebase/analytics/browser', () => ({
  capture: vi.fn(),
  identifyGroup: vi.fn(),
  identifyUser: vi.fn(),
  resetUser: vi.fn(),
}))

import { capture, identifyGroup, identifyUser, resetUser } from '@rebase/analytics/browser'
import type { paths } from './api-types'
import { analyticsMiddleware, eventFor, forgetSession, identifySession } from './analytics'

// Absolute, because a `Request` needs a base to resolve against and the middleware
// reads the pathname off `request.url`, the way it does in a browser where
// `openapi-fetch` resolved `/<slug>/api/...` against the page's origin.
const ORIGIN = 'http://pigrocrm.test'

/** One call through the middleware alone: the request as the client would have built
 *  it, the response as the server answered. */
async function answered(method: string, path: string, status: number, prefix = '') {
  const middleware = analyticsMiddleware(prefix)
  await middleware.onResponse?.({
    request: new Request(`${ORIGIN}${path}`, { method }),
    response: new Response(null, { status }),
  } as never)
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('the funnel table', () => {
  it.each([
    ['POST', '/api/tenants/', 'spazio_creato'],
    ['POST', '/api/auth/verify', 'entrato_con_link'],
    ['POST', '/api/customers', 'cliente_creato'],
    ['POST', '/api/deals', 'deal_creato'],
    ['POST', '/api/documents', 'documento_creato'],
    ['POST', '/api/documents/from-template', 'documento_creato'],
    ['POST', '/api/time-entries', 'ore_registrate'],
    ['POST', '/api/invoices/inv-1/issue', 'fattura_emessa'],
    ['POST', '/api/tokens', 'assistente_collegato'],
    ['PUT', '/api/emitter', 'profilo_emittente_salvato'],
  ])('%s %s on a 2xx is exactly one %s', async (method, path, event) => {
    await answered(method, path, 201)
    expect(capture).toHaveBeenCalledTimes(1)
    expect(capture).toHaveBeenCalledWith(event)
  })

  it('counts an issued invoice, never the draft POST that created it', async () => {
    await answered('POST', '/api/invoices', 201)
    expect(capture).not.toHaveBeenCalled()
    await answered('POST', '/api/invoices/inv-1/issue', 201)
    expect(capture).toHaveBeenCalledTimes(1)
    expect(capture).toHaveBeenCalledWith('fattura_emessa')
  })

  it('never matches a {name} placeholder against an empty segment', async () => {
    await answered('POST', '/api/invoices//issue', 201)
    expect(capture).not.toHaveBeenCalled()
  })

  it.each([400, 401, 409, 422, 500, 503])('a %i is not an event', async (status) => {
    await answered('POST', '/api/customers', status)
    await answered('PUT', '/api/emitter', status)
    expect(capture).not.toHaveBeenCalled()
  })

  it('leaves out everything the table does not name', async () => {
    // A read, an update, a nested write, a sibling route and a wrong method on a
    // listed path: none of them is an activation step. The invoice siblings
    // (`/artifacts`, `/confirm`) and the document siblings (`/restore`,
    // `/versions`) pin that only the listed routes match: a lax trailing segment
    // would double-count their event on every action a document or invoice takes
    // after it already exists.
    await answered('GET', '/api/customers', 200)
    await answered('PUT', '/api/customers/c1', 200)
    await answered('POST', '/api/deals/d1/stage', 200)
    await answered('POST', '/api/invoices/import', 201)
    await answered('POST', '/api/invoices/inv-1/artifacts', 201)
    await answered('POST', '/api/invoices/inv-1/confirm', 200)
    await answered('POST', '/api/documents/doc-1/restore', 200)
    await answered('POST', '/api/documents/doc-1/versions', 201)
    await answered('POST', '/api/emitter', 200)
    await answered('POST', '/api/auth/login', 200)
    await answered('POST', '/api/auth/logout', 200)
    expect(capture).not.toHaveBeenCalled()
  })

  it('reads the path under a space with the prefix removed', async () => {
    await answered('POST', '/studio/api/customers', 201, '/studio')
    expect(capture).toHaveBeenCalledWith('cliente_creato')
  })

  it('does not mistake another space for this one', async () => {
    // Only this space's own prefix is removed: another slug is left in the path and
    // matches nothing, rather than counting as `/api/customers` here.
    await answered('POST', '/studio-rossi/api/customers', 201, '/studio')
    expect(capture).not.toHaveBeenCalled()
  })

  it('ignores a trailing slash either way', () => {
    expect(eventFor('POST', '/api/tenants', '')).toBe('spazio_creato')
    expect(eventFor('POST', '/api/tenants/', '')).toBe('spazio_creato')
    expect(eventFor('POST', '/api/customers/', '')).toBe('cliente_creato')
    expect(eventFor('post', '/studio/api/customers/', '/studio')).toBe('cliente_creato')
  })
})

describe('on the real client', () => {
  it('sees the request as openapi-fetch built it and counts a created customer once', async () => {
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify({ id: 'c1' }), {
        status: 201,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    const client = createClient<paths>({ baseUrl: `${ORIGIN}/studio`, fetch: fetchMock })
    client.use(analyticsMiddleware('/studio'))

    await client.POST('/api/customers', { body: { ragione_sociale: 'Ada' } as never })
    await client.GET('/api/customers')

    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(capture).toHaveBeenCalledTimes(1)
    expect(capture).toHaveBeenCalledWith('cliente_creato')
  })

  it('counts nothing when the API refused', async () => {
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify({ detail: 'Dati non validi' }), {
        status: 422,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    const client = createClient<paths>({ baseUrl: ORIGIN, fetch: fetchMock })
    client.use(analyticsMiddleware(''))

    await client.POST('/api/customers', { body: { ragione_sociale: '' } as never })

    expect(capture).not.toHaveBeenCalled()
  })
})

describe('identifySession', () => {
  const ADA = { id: 'u1', email: 'ada@studio.it', nome: 'Ada', ruolo: 'admin' as const }

  it('names the person with what Ivan chose to see, and the root installation as root', () => {
    identifySession(ADA, '')
    expect(identifyUser).toHaveBeenCalledWith('u1', {
      email: 'ada@studio.it',
      nome: 'Ada',
      ruolo: 'admin',
    })
    expect(identifyGroup).toHaveBeenCalledWith('spazio', 'root')
  })

  it('names a space by its slug, without the slash', () => {
    identifySession(ADA, '/studio')
    expect(identifyGroup).toHaveBeenCalledWith('spazio', 'studio')
  })

  it('is undone by forgetSession', () => {
    forgetSession()
    expect(resetUser).toHaveBeenCalledTimes(1)
  })
})
