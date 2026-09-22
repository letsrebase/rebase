import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, fetchWithRefresh, fieldErrorFrom, toProblem, unwrap } from './api'

// The domain problem document: an RFC 9457 body `domain_error_handler` renders for
// any DomainError (apps/api/src/pigrocrm_api/errors.py) -- application/problem+json,
// `detail` a string, `code`/`field`/`reason`/`expected` alongside it.
const PROBLEM = {
  type: 'https://pigrocrm.dev/errors/validation_failed',
  title: 'Dati non validi',
  status: 422,
  detail: 'customer.partita_iva: deve essere di 11 cifre',
  code: 'validation_failed',
  entity: 'customer',
  field: 'partita_iva',
  reason: 'deve essere di 11 cifre',
  expected: '11 cifre numeriche',
}

// FastAPI's own request-validation error: the request never reached an endpoint at
// all (here, a non-UUID path segment), so there is no domain `code` -- `detail` is
// an array of `{ type, loc, msg }` instead of a string. Declared alongside the
// domain shape for every 422 in the OpenAPI document on purpose (see
// _domain_and_request_validation_response in pigrocrm_api/errors.py); shape below
// pinned to the real `ValidationError` schema openapi-typescript generated from it
// (loc: (string | number)[], msg: string, type: string, plus optional input/ctx).
const FASTAPI_VALIDATION_ERROR = {
  detail: [
    {
      type: 'uuid_parsing',
      loc: ['path', 'customer_id'],
      msg: 'Input non valido: non è uno UUID',
      input: 'non-un-uuid',
    },
  ],
}

// A bare HTTPException, the shape every 401 in this API actually returns (login's
// wrong-credentials check, get_actor's and refresh's "the session is gone" checks --
// see pigrocrm_api/deps.py and pigrocrm_api/routers/auth.py): no `code` at all.
const BARE_UNAUTHENTICATED = { detail: 'Autenticazione richiesta' }

describe('toProblem', () => {
  it('passes a domain problem document through unchanged', () => {
    expect(toProblem(PROBLEM)).toEqual(PROBLEM)
    expect(toProblem(PROBLEM).code).toBe('validation_failed')
  })

  /** REB-294: the role refusal is the one document whose `detail` the client rewrites,
   *  because the server writes it for a log ("issue_invoice requires one of [admin],
   *  actor has readonly") and the person reading the banner is not the log's audience.
   *  The sentence names the reader's own role in Italian, via `roleLabel`. */
  it('turns a permission refusal into an Italian sentence naming both roles', () => {
    const problem = toProblem(
      {
        type: 'https://pigrocrm.dev/errors/permission_denied',
        title: 'Permesso negato',
        status: 403,
        detail: 'issue_invoice requires one of [admin], actor has readonly',
        code: 'permission_denied',
        action: 'issue_invoice',
        required_roles: ['admin'],
        actual_role: 'readonly',
      },
      403,
    )
    expect(problem.detail).toBe(
      'Il tuo ruolo in questo spazio è «sola lettura»: questa azione è riservata a «amministratore».',
    )
    expect(problem.code).toBe('permission_denied')
    expect(problem.status).toBe(403)
  })

  it('names several required roles in Italian order', () => {
    const problem = toProblem(
      {
        detail: 'x requires one of [admin, collaboratore], actor has readonly',
        code: 'permission_denied',
        required_roles: ['admin', 'collaboratore'],
        actual_role: 'readonly',
      },
      403,
    )
    expect(problem.detail).toBe(
      'Il tuo ruolo in questo spazio è «sola lettura»: questa azione è riservata a «amministratore» e «collaboratore».',
    )
  })

  /** The fallback is the honest one: without the two structured fields this is not a
   *  `PermissionDenied` the client can translate, so the server's own words stay. */
  it('keeps the server detail when a refusal carries no structured roles', () => {
    const problem = toProblem({ detail: 'Non permesso', code: 'permission_denied' }, 403)
    expect(problem.detail).toBe('Non permesso')
  })

  it('leaves the agent refusal in the server’s Italian, untouched', () => {
    // `agent_forbidden`'s detail is already written to be read out ("... è un atto che
    // richiede una persona"); rewriting it would replace the one message the backend
    // worded for this exact screen.
    const problem = toProblem(
      {
        detail: 'issue_invoice non è eseguibile da un agente: è un atto che richiede una persona',
        code: 'agent_forbidden',
        action: 'issue_invoice',
      },
      403,
    )
    expect(problem.detail).toContain('richiede una persona')
  })

  it('normalises a FastAPI request-validation array into the same shape', () => {
    const problem = toProblem(FASTAPI_VALIDATION_ERROR)
    expect(problem.code).toBe('validation_failed')
    expect(problem.status).toBe(422)
    expect(problem.field).toBe('customer_id')
    expect(problem.detail).toBe('Input non valido: non è uno UUID')
  })

  it('keeps the real message from a bare HTTPException even without a code', () => {
    const problem = toProblem(BARE_UNAUTHENTICATED)
    expect(problem.detail).toBe('Autenticazione richiesta')
  })

  it('turns an unknown failure into a readable Italian message', () => {
    const problem = toProblem(new Error('network down'))
    expect(problem.code).toBe('unknown')
    expect(problem.detail).toMatch(/errore/i)
  })

  it('turns a garbage object with no recognisable shape into the same fallback', () => {
    const problem = toProblem({ whatever: 'this is not a problem document' })
    expect(problem.code).toBe('unknown')
    expect(problem.detail).toMatch(/errore/i)
  })

  it('never returns undefined for detail', () => {
    expect(toProblem(null).detail).toBeTruthy()
    expect(toProblem(undefined).detail).toBeTruthy()
  })
})

// Fix round 1: main.py registers an exception handler only for DomainError, so
// FastAPI's own default HTTPException handler is what renders everything else --
// including a 404 for a route nothing matches, and a 405 for a wrong method on a
// route that exists. Both render through the exact same bare `{ detail: "<message>" }`
// shape as every deliberate 401 in this API, with no `code` either way. Reproduced
// live against the real backend: GET /auth/me (missing the /api prefix) -> 404
// {"detail":"Not Found"}; DELETE /api/auth/me -> 405 {"detail":"Method Not Allowed"}.
// Shape alone cannot tell these apart from a real 401 -- only the transport-level
// status can, and toProblem doesn't see it unless told: hence the second parameter.
describe('toProblem — authentication is decided by status, never by body shape alone', () => {
  it('does not call a 404 with this shape "unauthenticated"', () => {
    expect(toProblem({ detail: 'Not Found' }, 404).code).not.toBe('unauthenticated')
  })

  it('does not call a 405 with this shape "unauthenticated"', () => {
    expect(toProblem({ detail: 'Method Not Allowed' }, 405).code).not.toBe('unauthenticated')
  })

  it('calls a real 401 with this shape "unauthenticated"', () => {
    expect(toProblem(BARE_UNAUTHENTICATED, 401).code).toBe('unauthenticated')
  })

  it('gives a 404 an honest generic code instead, without losing its status or message', () => {
    const problem = toProblem({ detail: 'Not Found' }, 404)
    expect(problem.code).toBe('http_error')
    expect(problem.status).toBe(404)
    expect(problem.detail).toBe('Not Found')
  })

  it('does not guess "unauthenticated" when no status is given at all', () => {
    // toProblem(error) alone -- the shape every direct, status-free call site uses
    // (including every other test in this file) -- must not assume 401 just because
    // the body happens to look like one of this API's real 401s.
    expect(toProblem(BARE_UNAUTHENTICATED).code).not.toBe('unauthenticated')
  })
})

describe('fieldErrorFrom', () => {
  it('extracts the offending field from a domain problem document', () => {
    expect(fieldErrorFrom(toProblem(PROBLEM))).toEqual({
      field: 'partita_iva',
      message: 'deve essere di 11 cifre (atteso: 11 cifre numeriche)',
    })
  })

  it('extracts the offending field from a FastAPI validation array, from the last element of loc', () => {
    expect(fieldErrorFrom(toProblem(FASTAPI_VALIDATION_ERROR))).toEqual({
      field: 'customer_id',
      message: 'Input non valido: non è uno UUID',
    })
  })

  it('returns null when the problem is not about a field', () => {
    expect(fieldErrorFrom(toProblem({ ...PROBLEM, code: 'conflict', field: undefined }))).toBeNull()
  })

  it('returns null for the generic fallback, which is never about one specific field', () => {
    expect(fieldErrorFrom(toProblem(new Error('network down')))).toBeNull()
  })
})

describe('unwrap', () => {
  it('resolves with the data on success', async () => {
    const response = new Response(null, { status: 200 })
    await expect(
      unwrap(Promise.resolve({ data: { id: '1' }, response })),
    ).resolves.toEqual({ id: '1' })
  })

  it('attaches the real HTTP status even to a body that does not embed one, and correctly calls it unauthenticated', async () => {
    // The exact shape login/me/refresh return for a 401: openapi-fetch's parsed
    // `error` never carries the response's own status code (see api.ts's docstring
    // on unwrap), so without this, queryClient's retry policy could never see 401
    // for the one family of responses that matters most for it.
    const response = new Response(null, { status: 401 })
    await expect(
      unwrap(Promise.resolve({ error: BARE_UNAUTHENTICATED, response })),
    ).rejects.toMatchObject({ status: 401, code: 'unauthenticated', detail: 'Autenticazione richiesta' })
  })

  // Fix round 1: reproduces, through the actual unwrap() call path, the two live
  // responses the reviewer found -- a 404 for a mistyped path and a 405 for a wrong
  // method, both rendered by FastAPI's own default handler through the identical
  // bare-detail shape every 401 uses. Neither may come out "unauthenticated": only
  // unwrap has the real response.status, so this is where the distinction must hold.
  it('does not call a 404 "unauthenticated" -- GET /auth/me (no /api prefix) reproduced live', async () => {
    const response = new Response(null, { status: 404 })
    await expect(
      unwrap(Promise.resolve({ error: { detail: 'Not Found' }, response })),
    ).rejects.toMatchObject({ status: 404, code: 'http_error' })
  })

  it('does not call a 405 "unauthenticated" -- DELETE /api/auth/me reproduced live', async () => {
    const response = new Response(null, { status: 405 })
    await expect(
      unwrap(Promise.resolve({ error: { detail: 'Method Not Allowed' }, response })),
    ).rejects.toMatchObject({ status: 405, code: 'http_error' })
  })

  it('normalises a network-level failure into a ProblemDetail too, not a raw error', async () => {
    await expect(unwrap(Promise.reject(new TypeError('Failed to fetch')))).rejects.toMatchObject({
      code: 'unknown',
    })
  })
})

/**
 * `fetchWithRefresh` exists because two callers cannot use `openapi-fetch` at all:
 * `downloadInvoiceArtifact` and `downloadDocument` want a `Blob` and the server's own
 * `Content-Disposition`, which the typed client has no way to express. They were raw
 * `fetch(..., {credentials:'include'})` calls, and the fifteen-minute access cookie
 * meant the PDF button answered «Autenticazione richiesta» after any quiet spell.
 */
describe('fetchWithRefresh', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function unauthenticated() {
    return new Response(JSON.stringify({ detail: 'Autenticazione richiesta' }), { status: 401 })
  }

  it('sends the session cookie and passes a 2xx straight through', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response('ok', { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    const response = await fetchWithRefresh('/api/x')

    expect(response.status).toBe(200)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({ credentials: 'include' })
  })

  it('refreshes once and retries the original request on a 401', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(unauthenticated())
      .mockResolvedValueOnce(new Response(null, { status: 200 }))
      .mockResolvedValueOnce(new Response('ok', { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    const response = await fetchWithRefresh('/api/x')

    expect(response.status).toBe(200)
    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      '/api/x',
      '/api/auth/refresh',
      '/api/x',
    ])
    expect(fetchMock.mock.calls[1]?.[1]).toMatchObject({ method: 'POST', credentials: 'include' })
  })

  it('returns the original 401 when the refresh fails, so the caller says "session gone"', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(unauthenticated())
      .mockResolvedValueOnce(unauthenticated())
    vi.stubGlobal('fetch', fetchMock)

    const response = await fetchWithRefresh('/api/x')

    expect(response.status).toBe(401)
    // The original response's body is untouched and still readable: the caller parses
    // it into a problem document.
    expect(await response.json()).toEqual({ detail: 'Autenticazione richiesta' })
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('refreshes once for two requests that expire together', async () => {
    // A rotating refresh token is single-use, and `RefreshTokenService.consume`
    // treats a replay as a compromise and revokes *every* token the user holds
    // (apps/api/src/pigrocrm_api/routers/auth.py). Two downloads that both meet a
    // 401 must therefore share one refresh, or the pair logs the owner out.
    let refreshes = 0
    const fetchMock = vi.fn<typeof fetch>().mockImplementation((input) => {
      const url = String(input)
      if (url.endsWith('/api/auth/refresh')) {
        refreshes += 1
        return Promise.resolve(new Response(null, { status: 200 }))
      }
      return Promise.resolve(
        refreshes === 0 ? unauthenticated() : new Response('ok', { status: 200 }),
      )
    })
    vi.stubGlobal('fetch', fetchMock)

    const [a, b] = await Promise.all([fetchWithRefresh('/api/a'), fetchWithRefresh('/api/b')])

    expect([a.status, b.status]).toEqual([200, 200])
    expect(refreshes).toBe(1)
  })
})

/**
 * The same second chance, for every request that goes through the typed client --
 * which is all of them but the two downloads above.
 *
 * The bug this pins: the access cookie lasts fifteen minutes
 * (`access_token_minutes`), the refresh cookie a hundred and eighty days, and a
 * `POST /api/auth/refresh` renews the pair. Nothing in the client asked for that
 * renewal, so after a quiet quarter of an hour *every* screen answered
 * «Autenticazione richiesta» -- with a good refresh cookie in the jar -- until the
 * owner reloaded the page. Reported from production.
 *
 * `baseUrl` is passed per call throughout: in a browser `new Request('/api/auth/me')`
 * resolves against the document, but the `Request` this test environment provides is
 * Node's, which rejects a relative URL outright. The production client keeps its own
 * empty/`/<slug>` base (see `api` in lib/api.ts) -- only these tests need to spell an
 * origin.
 */
describe('the typed client', () => {
  const BASE = 'http://localhost:3000'

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function unauthenticated() {
    return new Response(JSON.stringify({ detail: 'Autenticazione richiesta' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  function json(payload: unknown) {
    return new Response(JSON.stringify(payload), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  /** Whatever each attempt actually addressed, Request or plain string alike. */
  function urls(mock: ReturnType<typeof vi.fn<typeof fetch>>): string[] {
    return mock.mock.calls.map(([input]) =>
      input instanceof Request ? input.url : String(input),
    )
  }

  const ME = {
    id: '11111111-1111-1111-1111-111111111111',
    email: 'titolare@studio.it',
    nome: 'Titolare',
    ruolo: 'admin',
    attivo: true,
  }

  /** A second, *differently shaped* payload, so a test asserting on the response of
   *  the second call in a burst cannot pass by accident on the first call's. */
  const CUSTOMERS = {
    items: [{ id: '22222222-2222-2222-2222-222222222222', ragione_sociale: 'Rossi SRL' }],
    next_cursor: null,
  }

  it('refreshes once on a 401 and returns the data the retry produced', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(unauthenticated())
      .mockResolvedValueOnce(new Response(null, { status: 200 }))
      .mockResolvedValueOnce(json(ME))
    vi.stubGlobal('fetch', fetchMock)

    const user = await unwrap(api.GET('/api/auth/me', { baseUrl: BASE }))

    expect(user).toMatchObject({ email: 'titolare@studio.it' })
    expect(urls(fetchMock)).toEqual([
      `${BASE}/api/auth/me`,
      '/api/auth/refresh',
      `${BASE}/api/auth/me`,
    ])
    expect(fetchMock.mock.calls[1]?.[1]).toMatchObject({ method: 'POST', credentials: 'include' })
  })

  it('refreshes exactly once for two calls whose cookie expired together', async () => {
    // A rotating refresh token is single-use, and `RefreshTokenService.consume`
    // treats a replay as a compromised credential and revokes every token the user
    // holds (apps/api/src/pigrocrm_api/routers/auth.py). Two screens loading at the
    // same moment is the ordinary case, not a corner one, so a second refresh here
    // would log the owner out for real.
    let refreshes = 0
    const fetchMock = vi.fn<typeof fetch>().mockImplementation((input) => {
      const url = input instanceof Request ? input.url : String(input)
      if (url.endsWith('/api/auth/refresh')) {
        refreshes += 1
        return Promise.resolve(new Response(null, { status: 200 }))
      }
      if (refreshes === 0) return Promise.resolve(unauthenticated())
      return Promise.resolve(json(url.endsWith('/api/customers') ? CUSTOMERS : ME))
    })
    vi.stubGlobal('fetch', fetchMock)

    const [a, b] = await Promise.all([
      unwrap(api.GET('/api/auth/me', { baseUrl: BASE })),
      unwrap(api.GET('/api/customers', { baseUrl: BASE })),
    ])

    expect(refreshes).toBe(1)
    expect(a).toMatchObject({ email: 'titolare@studio.it' })
    // Both callers must get *their own* retried payload, not merely something
    // truthy: a shared refresh that returned the first caller's response to the
    // second would satisfy `toBeTruthy()` and be a bug in every screen at once.
    expect(b).toMatchObject({ items: [{ ragione_sociale: 'Rossi SRL' }], next_cursor: null })
  })

  it('refreshes once for a burst that mixes the typed client and fetchWithRefresh', async () => {
    // The ordinary shape of a page opening after a quiet quarter of an hour: a screen
    // loads through the typed client while a PDF button (or a document upload) goes out
    // through `fetchWithRefresh`. They share one in-flight refresh or they do not, and
    // nothing else in this file exercises the two together -- a second refresh here is
    // the same replayed token that used to log the owner out.
    let refreshes = 0
    const fetchMock = vi.fn<typeof fetch>().mockImplementation((input) => {
      const url = input instanceof Request ? input.url : String(input)
      if (url.endsWith('/api/auth/refresh')) {
        refreshes += 1
        return Promise.resolve(new Response(null, { status: 200 }))
      }
      if (refreshes === 0) return Promise.resolve(unauthenticated())
      return Promise.resolve(url.endsWith('/pdf') ? new Response('%PDF', { status: 200 }) : json(ME))
    })
    vi.stubGlobal('fetch', fetchMock)

    const [me, pdf] = await Promise.all([
      unwrap(api.GET('/api/auth/me', { baseUrl: BASE })),
      fetchWithRefresh('/api/documents/d1/pdf'),
    ])

    expect(refreshes).toBe(1)
    expect(me).toMatchObject({ email: 'titolare@studio.it' })
    expect(pdf.status).toBe(200)
    expect(await pdf.text()).toBe('%PDF')
  })

  it('gives up after a failed refresh, surfacing "unauthenticated" and no retry', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(unauthenticated())
      .mockResolvedValueOnce(unauthenticated())
    vi.stubGlobal('fetch', fetchMock)

    // What AuthProvider's `me` query sees on the login page: it catches this and
    // renders the login form. One refresh attempt, then the truth -- never a loop.
    await expect(unwrap(api.GET('/api/auth/me', { baseUrl: BASE }))).rejects.toMatchObject({
      code: 'unauthenticated',
      status: 401,
    })
    expect(urls(fetchMock)).toEqual([`${BASE}/api/auth/me`, '/api/auth/refresh'])
  })

  it('surfaces a 401 from the retry itself instead of refreshing again', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(unauthenticated())
      .mockResolvedValueOnce(new Response(null, { status: 200 }))
      .mockResolvedValueOnce(unauthenticated())
    vi.stubGlobal('fetch', fetchMock)

    await expect(unwrap(api.GET('/api/auth/me', { baseUrl: BASE }))).rejects.toMatchObject({
      code: 'unauthenticated',
    })
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })

  it.each([
    ['/api/auth/login' as const, { email: 'a@b.it', password: 'x' }],
    ['/api/auth/logout' as const, undefined],
    ['/api/auth/refresh' as const, undefined],
  ])('never refreshes for %s, whose own 401 is the answer', async (path, body) => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(unauthenticated())
    vi.stubGlobal('fetch', fetchMock)

    await expect(
      unwrap(api.POST(path, { baseUrl: BASE, body } as never)),
    ).rejects.toMatchObject({ code: 'unauthenticated' })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('never refreshes a request that carries its own Bearer credential', async () => {
    // A personal access token is not the session cookie: there is nothing to
    // renew, and the caller (a script, an integration) owns its own credential.
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(unauthenticated())
    vi.stubGlobal('fetch', fetchMock)

    await expect(
      unwrap(
        api.GET('/api/auth/me', { baseUrl: BASE, headers: { Authorization: 'Bearer pat_abc' } }),
      ),
    ).rejects.toMatchObject({ code: 'unauthenticated' })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('replays the same method and body, byte for byte, on the retry', async () => {
    // `fetch` consumes a request's body stream, so the retry cannot re-send the
    // request that already went out: a copy has to be taken while it is intact --
    // which means *before* the first attempt, not after its 401 has come back.
    //
    // Each mocked attempt reads its own request's body here, exactly as a real
    // `fetch` does. That is what makes the ordering bite: a clone taken after the
    // first send would throw «body stream already read», where a mock that never
    // touches the stream leaves both orders passing and pins nothing. The bodies are
    // asserted from what the mock actually received, since after this the streams
    // are legitimately gone.
    const sent: string[] = []
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(async (input) => {
      if (!(input instanceof Request)) return new Response(null, { status: 200 }) // the refresh
      sent.push(await input.text())
      return sent.length === 1 ? unauthenticated() : json({ id: 'c1' })
    })
    vi.stubGlobal('fetch', fetchMock)

    const body = {
      ragione_sociale: 'Rossi SRL',
      nazione: 'IT',
      pagamento_fine_mese: false,
      custom_fields: {},
    }
    await unwrap(api.POST('/api/customers', { baseUrl: BASE, body }))

    const [first, , retried] = fetchMock.mock.calls.map(([input]) => input as Request)
    expect(retried?.method).toBe('POST')
    expect(retried?.url).toBe(first?.url)
    expect(retried?.headers.get('Content-Type')).toBe('application/json')
    expect(sent).toEqual([JSON.stringify(body), JSON.stringify(body)])
  })
})

/** The one multipart upload cannot go through the typed client either (openapi-fetch
 *  has no way to send a `FormData` body for a multipart route), so it shares the
 *  downloads' `fetchWithRefresh` -- same fifteen-minute cliff, same fix. */
describe('fetchWithRefresh with a FormData body', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('re-sends the very same FormData on the retry', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(new Response(null, { status: 200 }))
      .mockResolvedValueOnce(new Response(null, { status: 201 }))
    vi.stubGlobal('fetch', fetchMock)

    const form = new FormData()
    form.append('file', new Blob(['contenuto']), 'contratto.pdf')
    const response = await fetchWithRefresh('/api/documents/d1/versions', {
      method: 'POST',
      body: form,
    })

    expect(response.status).toBe(201)
    expect(fetchMock.mock.calls[2]?.[1]).toMatchObject({ method: 'POST', body: form })
  })
})

/**
 * Two tabs are two module instances, not two calls into one.
 *
 * The in-flight promise above is per tab by construction -- it is a module-level
 * variable -- and the refresh cookie is per *browser*, so two tabs waking up past the
 * same fifteen-minute access cookie both post the same rotating token. The server
 * treats a replayed refresh token as a stolen one, so that pair used to revoke every
 * token the owner held and drop them on the login screen in both tabs at once (now
 * softened by `RefreshTokenService`'s ten-second grace window, which this lock is what
 * keeps from being needed every quarter of an hour).
 *
 * `vi.resetModules()` before each import is what makes the two tabs real: each gets its
 * own `refreshing`, sees the other only through the shared fake `navigator.locks`, and
 * so the serialisation being asserted is the lock's doing and nothing else. Every other
 * test in this file runs with no `navigator.locks` at all -- jsdom does not implement it
 * -- which is the fallback path, already covered by all of them passing.
 */
describe('one session refresh across tabs', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.resetModules()
  })

  function unauthenticated() {
    return new Response(JSON.stringify({ detail: 'Autenticazione richiesta' }), { status: 401 })
  }

  /** A fresh instance of `lib/api`: one tab's worth of module state. */
  async function openTab() {
    vi.resetModules()
    return await import('./api')
  }

  it('serialises two tabs through one named lock instead of racing them', async () => {
    // A `navigator.locks` that actually queues, which is the only property of the real
    // one this depends on: the second `request` for a name waits for the first to
    // release before its callback runs.
    const timeline: string[] = []
    let queue: Promise<unknown> = Promise.resolve()
    const request = vi.fn(
      (name: string, _options: LockOptions, callback: () => Promise<boolean>) => {
        const granted = queue.then(async () => {
          timeline.push(`held ${name}`)
          try {
            return await callback()
          } finally {
            timeline.push('released')
          }
        })
        queue = granted.then(
          () => undefined,
          () => undefined,
        )
        return granted
      },
    )
    vi.stubGlobal('navigator', { locks: { request } })

    let refreshes = 0
    const fetchMock = vi.fn<typeof fetch>().mockImplementation((input) => {
      const url = input instanceof Request ? input.url : String(input)
      if (url.endsWith('/api/auth/refresh')) {
        refreshes += 1
        timeline.push('refresh')
        return Promise.resolve(new Response(null, { status: 200 }))
      }
      return Promise.resolve(
        refreshes === 0 ? unauthenticated() : new Response('ok', { status: 200 }),
      )
    })
    vi.stubGlobal('fetch', fetchMock)

    const tabA = await openTab()
    const tabB = await openTab()
    const [a, b] = await Promise.all([
      tabA.fetchWithRefresh('/api/a'),
      tabB.fetchWithRefresh('/api/b'),
    ])

    expect([a.status, b.status]).toEqual([200, 200])
    // Twice, because two tabs really do have two sessions' worth of state to renew --
    // and one at a time, which is the whole point: the second request goes out after
    // the first response has already replaced the cookie in the shared jar, so it
    // rotates a current token instead of replaying a dead one.
    expect(request).toHaveBeenCalledTimes(2)
    expect(request.mock.calls.map(([name]) => name)).toEqual([
      'pigrocrm-refresh',
      'pigrocrm-refresh',
    ])
    // Every wait is bounded, including the ordinary one nobody has to wait long for.
    for (const [, options] of request.mock.calls) {
      expect(options.signal).toBeInstanceOf(AbortSignal)
      expect(options.signal?.aborted).toBe(false)
    }
    expect(timeline).toEqual([
      'held pigrocrm-refresh',
      'refresh',
      'released',
      'held pigrocrm-refresh',
      'refresh',
      'released',
    ])
  })

  it('gives up on the lock when the tab holding it never lets go', async () => {
    // The failure a lock introduces that no lock had: `LockManager.request` waits
    // forever by default and `fetch` has no timeout, so one tab whose refresh POST
    // stalls on a hung proxy holds this lock for as long as its socket lives -- and
    // every other tab sits inside `await refreshSession()` with its own request
    // unsettled, a spinner that never resolves and never errors. This tab must renew
    // its session anyway, unlocked, which the server's own grace window absorbs.
    // The callback the production code passes as the third argument is deliberately
    // not even a parameter here: this fake could not run it if it wanted to.
    const request = vi.fn(
      (_name: string, options: LockOptions) =>
        new Promise<boolean>((_resolve, reject) => {
          // The lock is held elsewhere and never released, so the callback is never
          // invoked and the only way out of this promise is the signal -- exactly as
          // the real LockManager rejects a request aborted while still waiting.
          options.signal?.addEventListener('abort', () =>
            reject(options.signal?.reason ?? new Error('AbortError')),
          )
        }),
    )
    vi.stubGlobal('navigator', { locks: { request } })

    // The ten-second bound, shortened to a macrotask: the production code asks for
    // `AbortSignal.timeout(LOCK_WAIT_MS)` and this is what answers it, so the test
    // proves the wait ends without waiting ten seconds for it to.
    const asked: number[] = []
    vi.stubGlobal('AbortSignal', {
      timeout: (ms: number) => {
        asked.push(ms)
        const controller = new AbortController()
        setTimeout(() => controller.abort(), 0)
        return controller.signal
      },
    })

    let refreshes = 0
    const fetchMock = vi.fn<typeof fetch>().mockImplementation((input) => {
      const url = input instanceof Request ? input.url : String(input)
      if (url.endsWith('/api/auth/refresh')) {
        refreshes += 1
        return Promise.resolve(new Response(null, { status: 200 }))
      }
      return Promise.resolve(
        refreshes === 0 ? unauthenticated() : new Response('ok', { status: 200 }),
      )
    })
    vi.stubGlobal('fetch', fetchMock)

    const tab = await openTab()
    const response = await tab.fetchWithRefresh('/api/x')

    // Resolved at all is half the assertion: an unbounded wait hangs this line.
    expect(response.status).toBe(200)
    expect(asked).toEqual([10_000])
    expect(request).toHaveBeenCalledTimes(1)
    // Renewed regardless, and necessarily through the unlocked fallback: this fake
    // never invokes the callback the lock was requested with.
    expect(refreshes).toBe(1)
  })

  it('skips the lock entirely where the wait cannot be bounded', async () => {
    // A browser with Web Locks but no `AbortSignal.timeout` would only be offered an
    // unbounded wait, which is the one failure worse than no lock at all: refresh
    // unlocked instead, the same path jsdom and older Safari take.
    const request = vi.fn()
    vi.stubGlobal('navigator', { locks: { request } })
    vi.stubGlobal('AbortSignal', {})

    let refreshes = 0
    const fetchMock = vi.fn<typeof fetch>().mockImplementation((input) => {
      const url = input instanceof Request ? input.url : String(input)
      if (url.endsWith('/api/auth/refresh')) {
        refreshes += 1
        return Promise.resolve(new Response(null, { status: 200 }))
      }
      return Promise.resolve(
        refreshes === 0 ? unauthenticated() : new Response('ok', { status: 200 }),
      )
    })
    vi.stubGlobal('fetch', fetchMock)

    const tab = await openTab()
    const response = await tab.fetchWithRefresh('/api/x')

    expect(response.status).toBe(200)
    expect(refreshes).toBe(1)
    expect(request).not.toHaveBeenCalled()
  })
})

/**
 * A space's SPA is served under `/<slug>/app/` and its API lives under `/<slug>/api/...`
 * (lib/tenant.ts): `tenantPrefix` is read from `window.location.pathname` once, at
 * module load, so under jsdom's own `/` every other test in this file exercises the
 * root installation and nothing exercises a space at all. A prefix dropped from the
 * refresh URL would send a space's renewal to the *root* installation -- a 401 there,
 * and the owner logged out inside their own space; a prefix dropped from the exclusion
 * comparison would make the client refresh around its own refresh call and recurse.
 *
 * Hence `vi.resetModules()` plus a stubbed `location` *before* the import: the constant
 * is derived at load time, so it cannot be changed afterwards -- which is exactly why
 * it needs its own module instance.
 */
describe("a tab inside a space carries the space's prefix", () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.resetModules()
  })

  function unauthenticated() {
    return new Response(JSON.stringify({ detail: 'Autenticazione richiesta' }), { status: 401 })
  }

  async function openTabAt(pathname: string) {
    vi.resetModules()
    vi.stubGlobal('location', { pathname })
    return await import('./api')
  }

  it('refreshes at the space’s own /api/auth/refresh, not the root one', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(unauthenticated())
      .mockResolvedValueOnce(new Response(null, { status: 200 }))
      .mockResolvedValueOnce(new Response('%PDF', { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    const tab = await openTabAt('/spazio-x/app/documents/d1')
    const response = await tab.fetchWithRefresh('/api/documents/d1/pdf')

    expect(response.status).toBe(200)
    expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
      '/spazio-x/api/documents/d1/pdf',
      '/spazio-x/api/auth/refresh',
      '/spazio-x/api/documents/d1/pdf',
    ])
  })

  it('excludes the session endpoints by their prefixed path, not the bare one', async () => {
    // `/spazio-x/api/auth/refresh` is the refresh endpoint of this space. Compared
    // against the bare `/api/auth/refresh` it matches nothing, the 401 looks like an
    // expired access cookie, and the client refreshes around its own refresh.
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(unauthenticated())
    vi.stubGlobal('fetch', fetchMock)

    const tab = await openTabAt('/spazio-x/app/login')
    const response = await tab.fetchWithRefresh('/api/auth/refresh')

    expect(response.status).toBe(401)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe('/spazio-x/api/auth/refresh')
  })
})
