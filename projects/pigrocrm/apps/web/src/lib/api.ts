import createClient from 'openapi-fetch'
import type { paths } from './api-types'
import { roleLabel } from './roles'
import { tenantPrefix } from './tenant'

export const api = createClient<paths>({
  // Empty, not '/api': every router (apps/api/src/pigrocrm_api/routers/*.py) already
  // declares its own "/api/..." prefix, so `paths` below -- generated straight from
  // the live OpenAPI document -- has full keys like "/api/auth/me", never bare
  // "/auth/me". Vite's dev proxy (vite.config.ts) matches the same "/api" prefix and
  // forwards it unmodified, so the exact same full path also reaches the right
  // route in production, behind whatever serves this build there.
  // A space prepends its `/<slug>` here and nowhere else (lib/tenant.ts); the root's
  // prefix is empty, so every `/api/...` key stays absolute-from-root as before.
  baseUrl: tenantPrefix,
  // The JWT lives in an httpOnly cookie: JavaScript never sees it, and every
  // request carries it automatically.
  credentials: 'include',
  // Every request the client makes goes out through this instead of the bare
  // `globalThis.fetch` it would otherwise capture once, at `createClient()`: that is
  // what gives the whole typed API the second chance after an expired access cookie
  // (see `sendRefreshingSession` below), and, incidentally, what lets a test stub
  // `globalThis.fetch` and intercept any of it at all.
  fetch: sendRefreshingSession,
})

/**
 * The refresh in flight, if any -- shared by every caller that meets a 401 at the same
 * moment.
 *
 * Not a nicety: `POST /api/auth/refresh` rotates the refresh token, and
 * `RefreshTokenService.consume` treats a *replayed* one as a compromised credential
 * and revokes every token the user holds (see `apps/api/src/pigrocrm_api/routers/
 * auth.py`). Two requests whose cookies expired together -- the ordinary case, since
 * a screen loads several at once -- would send the same token twice and log the owner
 * out, a worse outcome than the 401 this whole mechanism exists to absorb. One
 * promise, awaited by all of them.
 */
let refreshing: Promise<boolean> | null = null

/**
 * The name every tab of this application agrees on for the right to refresh.
 *
 * One string, not one per space, and a Web Lock is scoped to the origin. Not because the
 * cookie is: session cookies are path-scoped per space (`cookie_path`,
 * apps/api/src/pigrocrm_api/tenancy.py), so a tab in `/studio/app/` and a tab in `/app/`
 * genuinely hold different refresh cookies. But a root cookie at `Path=/` is sent into a
 * space's paths too, so the jars overlap and two tabs can still be rotating the very
 * same token -- and a per-prefix name would let exactly that pair refresh at once, which
 * is the whole thing being prevented. The cost of the conservative name is that a stalled
 * tab in one space makes another space's tab wait, which is why that wait is bounded
 * (`LOCK_WAIT_MS`) rather than left to the lock's own default of forever.
 */
const REFRESH_LOCK = 'pigrocrm-refresh'

/**
 * How long a tab waits for another tab's refresh before giving up on the lock.
 *
 * `LockManager.request` is otherwise an *unbounded* wait, and `fetch` has no timeout of
 * its own: one tab whose refresh POST stalls on a hung proxy, a captive portal or a
 * sleeping connection holds this lock for as long as its socket stays open, and every
 * other tab of the origin sits inside `await refreshSession()` with its own request
 * unsettled -- a spinner that never resolves and never errors. Before the lock existed
 * each tab failed on its own; a lock that can hang them all together is worse than no
 * lock at all.
 *
 * Ten seconds, and the abort is safe rather than merely bounded: a request aborted while
 * *waiting* never runs its callback, so the fallback below refreshes unlocked -- which
 * the server's own ten-second grace window now absorbs
 * (`RefreshTokenService.REFRESH_GRACE_SECONDS`) instead of reading as a replay.
 */
const LOCK_WAIT_MS = 10_000

/** The refresh request itself. Never throws: a failed refresh is an answer ("no"), and
 *  the caller's own original response is what gets reported. */
function postRefresh(): Promise<boolean> {
  return fetch(`${tenantPrefix}/api/auth/refresh`, {
    method: 'POST',
    credentials: 'include',
  })
    .then((response) => response.ok)
    .catch(() => false)
}

/**
 * The same refresh, but at most one across every tab of this origin at a time.
 *
 * `refreshing` above covers one tab's own concurrent requests. It cannot cover two
 * tabs, and two tabs is the ordinary case: the refresh cookie belongs to the browser,
 * not to a tab, so two tabs (or one browser restoring a window of them) that wake up
 * past the same fifteen-minute access cookie both post the same rotating token within
 * milliseconds. Serialised by this lock, the second tab's request goes out *after* the
 * first one's response has already replaced the cookie in the shared jar, so it is an
 * ordinary rotation of a current token rather than a simultaneous replay of a dead one.
 *
 * The server no longer punishes the race either -- `RefreshTokenService.rotate` answers
 * a second presentation inside ten seconds with the same successor -- and both halves
 * are wanted: this lock is what keeps the pair of them from *needing* the grace window
 * on every quiet quarter of an hour, and the grace window is what covers what a lock in
 * one browser cannot (a request already in flight when the lock was taken, a second
 * browser profile, `navigator.locks` missing).
 *
 * Missing is a real case, not a hypothetical: `navigator.locks` is undefined in a
 * non-secure context, in older Safari, and in this test suite's jsdom, and
 * `AbortSignal.timeout` is missing in browsers old enough that an unbounded lock is the
 * worse of the two risks. In any of those cases -- and if the lock manager refuses the
 * request, or the wait times out -- the refresh still happens, unlocked, as it did
 * before. A session that cannot be renewed is the fifteen-minute bug this whole
 * mechanism exists to fix; a lock is worth having, never worth failing over.
 */
async function refreshUnderCrossTabLock(): Promise<boolean> {
  const locks = globalThis.navigator?.locks
  // No bound available means no lock: an unbounded wait on another tab is the one
  // failure mode worse than refreshing without the lock at all (see `LOCK_WAIT_MS`).
  const bounded = typeof AbortSignal?.timeout === 'function'
  if (locks === undefined || !bounded) return postRefresh()
  try {
    // Awaited, not returned: `LockManager.request` is typed as resolving to whatever
    // the callback returns *or* a promise of it, and awaiting is what flattens the
    // `boolean | Promise<boolean>` that comes back.
    //
    // The signal only ever cancels the *wait*: a granted lock ignores it, so this can
    // never abort a refresh already in flight, only stop this tab queueing behind one.
    return await locks.request(
      REFRESH_LOCK,
      { signal: AbortSignal.timeout(LOCK_WAIT_MS) },
      postRefresh,
    )
  } catch {
    return postRefresh()
  }
}

/** Whether the session could be renewed -- one answer per tab, and one refresh at a
 *  time across tabs. Never throws, for the reason `postRefresh` does not. */
function refreshSession(): Promise<boolean> {
  refreshing ??= refreshUnderCrossTabLock()
    .catch(() => false)
    .finally(() => {
      refreshing = null
    })
  return refreshing
}

/** The three requests a refresh must never be attached to.
 *
 *  `login` and `refresh` *are* the session's own machinery: their 401 is the answer
 *  ("wrong password", "the refresh cookie is gone too"), not a symptom of an expired
 *  access cookie, and renewing around them would either hide the real message or --
 *  for `refresh` -- recurse. `logout` wants the session ended, so reviving it first
 *  is precisely backwards. */
const NEVER_REFRESHED = ['/api/auth/login', '/api/auth/refresh', '/api/auth/logout']

/**
 * Whether an expired access cookie is even a plausible explanation for this request's
 * 401.
 *
 * The path is compared after parsing, not by string matching, because the two callers
 * arrive with differently-shaped URLs: `fetchWithRefresh` has a root-relative
 * `/<slug>/api/...`, the typed client an absolute one that `Request` has already
 * resolved. `ORIGIN` is a parsing base for the relative form only -- an absolute URL
 * ignores it, and neither is ever fetched from here.
 *
 * An `Authorization` header means the caller brought its own credential -- a personal
 * access token, `Bearer pat_...` -- which the cookie refresh has nothing to do with:
 * there is no session to renew, and its 401 means that token is wrong or revoked.
 */
const ORIGIN = 'http://pigrocrm.invalid'

function couldBeAnExpiredSession(url: string, headers: Headers): boolean {
  if (headers.has('Authorization')) return false
  const { pathname } = new URL(url, ORIGIN)
  return !NEVER_REFRESHED.some((endpoint) => pathname === `${tenantPrefix}${endpoint}`)
}

/**
 * One 401, one refresh, one retry -- the whole policy, in the one place both the typed
 * client and the raw-`fetch` callers below go through.
 *
 * `replay` is called at most once, and only after a refresh that actually succeeded.
 * When the refresh fails the **original** response is returned untouched (its body
 * still unread), because what failed from the user's point of view is the request they
 * made, not a mechanism they never asked for: the caller parses that body and gets
 * `code: 'unauthenticated'` from `toProblem`, which is the honest "your session is
 * gone" -- and what makes `AuthProvider` show the login screen. A retry that comes
 * back 401 as well is returned exactly the same way: never a second refresh, never a
 * loop.
 */
async function retryOnceAfterRefresh(
  first: Response,
  request: { url: string; headers: Headers; replay: () => Promise<Response> },
): Promise<Response> {
  if (first.status !== 401) return first
  if (!couldBeAnExpiredSession(request.url, request.headers)) return first
  if (!(await refreshSession())) return first
  return request.replay()
}

/**
 * The typed client's `fetch`: the request as `openapi-fetch` built it, plus the second
 * chance after the fifteen-minute access cookie (`access_token_minutes`) has expired
 * while the hundred-and-eighty-day refresh cookie is still perfectly good.
 *
 * Without this, every screen in the application answered «Autenticazione richiesta»
 * after any quiet quarter of an hour -- reported from production -- and stayed that
 * way until the owner reloaded the page, because nothing in the client ever asked for
 * the renewal the API was waiting to give it.
 *
 * The clone is taken *before* the first attempt on purpose: `fetch` consumes a
 * request's body stream, so a POST's body is gone by the time its 401 comes back and
 * the retry has to send a copy made while it was still intact. `Request.clone()` is
 * what makes that exact for both shapes this codebase sends -- a serialised JSON
 * string and (through `fetchWithRefresh`) a `FormData` with its boundary.
 *
 * `fetch` is looked up on the global at call time rather than captured at module
 * load, which is what `openapi-fetch`'s own default (`fetch: baseFetch =
 * globalThis.fetch`, read once inside `createClient()`) does not do -- and the only
 * reason a test can stub it.
 */
function sendRefreshingSession(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  // `openapi-fetch` always hands its `fetch` a `Request` it constructed itself (see
  // `coreFetch` in its source); the guard is for the type, not for a case that happens.
  if (!(input instanceof Request)) return fetch(input, init)
  const replay = input.clone()
  return fetch(input, init).then((first) =>
    retryOnceAfterRefresh(first, {
      url: input.url,
      headers: input.headers,
      replay: () => fetch(replay, init),
    }),
  )
}

/**
 * `fetch` with the same second chance, for the three callers that cannot use
 * `openapi-fetch` at all: `downloadInvoiceArtifact` (features/invoices/queries.ts) and
 * `downloadDocument` (features/documents/queries.ts) want a `Blob` and the server's own
 * `Content-Disposition` filename, and `putVersion` (features/documents/queries.ts) has
 * a `FormData` body for a multipart route -- none of which the typed client has any way
 * to express. They were plain `fetch(..., {credentials: 'include'})` calls, so after a
 * quiet quarter of an hour the owner pressed "PDF" (or dropped a file) and was told
 * «Autenticazione richiesta» -- with a perfectly good refresh cookie in the jar --
 * until some other request happened to renew the session.
 *
 * `tenantPrefix` is applied here for the same reason `api`'s `baseUrl` applies it: a
 * space's SPA is served under `/<slug>/app/` and its API lives under `/<slug>/api/...`
 * (lib/tenant.ts). These raw fetches spelled `/api/...` absolute-from-root and so
 * addressed the *root* installation from inside a space.
 *
 * The same `init` -- and so the same `FormData` object -- is handed to both attempts:
 * unlike a `Request`, a plain init is not consumed by being sent, and `fetch`
 * serialises it afresh each time.
 */
export async function fetchWithRefresh(path: string, init?: RequestInit): Promise<Response> {
  const url = `${tenantPrefix}${path}`
  const send = () => fetch(url, { credentials: 'include', ...init })
  const first = await send()
  return retryOnceAfterRefresh(first, {
    url,
    headers: new Headers(init?.headers),
    replay: send,
  })
}

export interface ProblemDetail {
  type: string
  title: string
  status: number
  detail: string
  code: string
  entity?: string
  field?: string
  reason?: string
  expected?: string
  [key: string]: unknown
}

const GENERIC: ProblemDetail = {
  type: 'about:blank',
  title: 'Errore',
  status: 0,
  detail: 'Si è verificato un errore imprevisto. Riprova.',
  code: 'unknown',
}

interface FastApiValidationError {
  loc: (string | number)[]
  msg: string
  type: string
}

/** FastAPI's own request-validation shape: `{ detail: [{ type, loc, msg }, ...] }`.
 *  Structural, not a Content-Type sniff -- openapi-fetch parses every non-2xx body
 *  with a blind `JSON.parse` regardless of header (see its source), so the only way
 *  to tell this apart from the domain problem document below is the shape of
 *  `detail` itself: an array here, a string there.
 *
 *  Return type is a non-empty tuple, not a plain array: under `noUncheckedIndexedAccess`
 *  (tsconfig.json), destructuring element 0 of `T[]` still types as `T | undefined` --
 *  the tuple form is what lets the caller destructure `[first]` and get `T` back,
 *  since a non-empty check was already made right here, once. */
function asFastApiValidationErrors(
  value: object,
): [FastApiValidationError, ...FastApiValidationError[]] | null {
  const detail = (value as { detail?: unknown }).detail
  if (!Array.isArray(detail) || detail.length === 0) return null
  const [first] = detail as unknown[]
  if (
    typeof first !== 'object' ||
    first === null ||
    !Array.isArray((first as { loc?: unknown }).loc) ||
    typeof (first as { msg?: unknown }).msg !== 'string'
  ) {
    return null
  }
  return detail as [FastApiValidationError, ...FastApiValidationError[]]
}

/**
 * The one rendering of the server's role refusal (REB-294), or `null` to leave the
 * server's own words alone.
 *
 * `PermissionDenied` carries a structured problem document -- `required_roles` and
 * `actual_role` beside the `code` -- and a `detail` written for a log, not a person:
 * `issue_invoice requires one of [admin], actor has readonly`. Once the table in
 * `lib/permissions.ts` hides the controls a role may not press, a 403 that still
 * arrives means the two have diverged (a tab the SPA forgot, a role changed in the
 * last thirty seconds), and the interface must say that in Italian and name the
 * person's own role rather than quote English. `agent_forbidden` needs nothing here:
 * its detail is already Italian, written to be read out.
 *
 * The fallback is deliberate: a `permission_denied` without the two structured fields
 * is not a `PermissionDenied` this function can honestly translate, and the server's
 * own (Italian) sentence stays.
 */
function permissionDeniedSentence(problem: ProblemDetail): string | null {
  if (problem.code !== 'permission_denied') return null
  const required = Array.isArray(problem.required_roles)
    ? problem.required_roles.filter((role): role is string => typeof role === 'string')
    : []
  const actual = problem.actual_role
  if (required.length === 0 || typeof actual !== 'string') return null
  // Italian lists take no Oxford comma and bind the last pair with `e`.
  const names = required.map((role) => `«${roleLabel(role)}»`)
  const reserved =
    names.length === 1 ? names[0] : `${names.slice(0, -1).join(', ')} e ${names[names.length - 1]}`
  return `Il tuo ruolo in questo spazio è «${roleLabel(actual)}»: questa azione è riservata a ${reserved}.`
}

/**
 * Normalises every shape an API call can fail with into one `ProblemDetail`, so
 * every consumer -- a toast, a form field, the query retry policy -- reads the same
 * three or four properties no matter which of these produced it:
 *
 *  - the domain problem document (RFC 9457, `application/problem+json`): a
 *    `DomainError` rendered by `domain_error_handler` -- `code`/`detail`/`field`/...
 *    already at the top level, passed through unchanged (still allowing `status` to
 *    be corrected below, since the transport layer is never wrong about it). The one
 *    exception is the role refusal: see `permissionDeniedSentence`.
 *  - FastAPI's own request-validation error (`application/json`): the request never
 *    reached an endpoint at all (a non-UUID path segment, a malformed body), so
 *    there is no domain `code` -- `detail` is an array of `{ type, loc, msg }`
 *    instead of a string. Both shapes are declared for the same 422 in the OpenAPI
 *    document on purpose (see `_domain_and_request_validation_response` in
 *    `apps/api/src/pigrocrm_api/errors.py`) precisely so this function has
 *    something real to normalise. The field pydantic actually complained about is
 *    always the *last* segment of `loc` -- everything before it is the path to get
 *    there, e.g. `["body", "customer", "partita_iva"]`.
 *  - a bare `HTTPException` (`application/json`, `{ detail: "<message>" }`, no
 *    `code`): this is where a real 401 lands (login's credentials check, and
 *    get_actor's/refresh's "the session is gone" checks -- see `pigrocrm_api/deps.py`
 *    and `pigrocrm_api/routers/auth.py`), but it is *not exclusive to 401* --
 *    `apps/api/src/pigrocrm_api/main.py` registers an exception handler only for
 *    `DomainError`, so FastAPI's own default `HTTPException` handler renders
 *    *everything else* through this identical shape too: a 404 for a path nothing
 *    matches, a 405 for a wrong method on a path that exists, with no `code` either
 *    way (reproduced live: `GET /auth/me` -> 404 `{"detail":"Not Found"}`; `DELETE
 *    /api/auth/me` -> 405 `{"detail":"Method Not Allowed"}`). Shape alone cannot
 *    tell a real 401 apart from these -- only the transport-level status can, which
 *    is why this function takes one as a second, optional argument: `status === 401`
 *    is the *only* thing that earns `code: 'unauthenticated'` here. Any other status
 *    with this same shape gets `code: 'http_error'` instead -- honest about not
 *    knowing more, never a guess dressed up as a fact. Omitting `status` entirely
 *    (every direct call in this file's own tests, and any consumer working from an
 *    already-thrown value with no `Response` in reach) never yields
 *    `'unauthenticated'` either, for the same reason.
 *  - anything else -- a thrown network `Error`, `null`, garbage -- becomes the
 *    generic Italian fallback. `detail` is never undefined from this function.
 *
 * `status`, when given, always wins for the returned `.status`: it comes straight
 * from the transport layer and cannot be wrong, unlike anything a body might (or
 * might not) self-report.
 */
export function toProblem(error: unknown, status?: number): ProblemDetail {
  if (!error || typeof error !== 'object') {
    return status === undefined ? GENERIC : { ...GENERIC, status }
  }

  const validationErrors = asFastApiValidationErrors(error)
  if (validationErrors) {
    const [first] = validationErrors
    const field = first.loc.length > 0 ? String(first.loc[first.loc.length - 1]) : undefined
    return {
      ...GENERIC,
      title: 'Dati non validi',
      status: status ?? 422,
      detail: first.msg,
      code: 'validation_failed',
      field,
      reason: first.msg,
    }
  }

  const err = error as { code?: unknown; detail?: unknown }
  if (typeof err.code === 'string' && typeof err.detail === 'string') {
    const problem = error as ProblemDetail
    const sentence = permissionDeniedSentence(problem)
    const normalised = sentence === null ? problem : { ...problem, detail: sentence }
    return status === undefined ? normalised : { ...normalised, status }
  }
  if (typeof err.detail === 'string') {
    return {
      ...GENERIC,
      status: status ?? GENERIC.status,
      detail: err.detail,
      code: status === 401 ? 'unauthenticated' : 'http_error',
    }
  }

  return status === undefined ? GENERIC : { ...GENERIC, status }
}

/** The API already decided what is wrong and why. The UI only points at it. */
export function fieldErrorFrom(problem: ProblemDetail): { field: string; message: string } | null {
  if (problem.code !== 'validation_failed' || !problem.field) return null
  const reason = problem.reason ?? problem.detail
  const message = problem.expected ? `${reason} (atteso: ${problem.expected})` : reason
  return { field: problem.field, message }
}

/**
 * Throws the problem document itself, so every consumer gets structured data --
 * never a raw `Response` or an untyped `unknown`.
 *
 * Always passes `response.status` into `toProblem`: openapi-fetch's parsed `error`
 * body never carries the real HTTP status itself (it comes from a blind
 * `JSON.parse` of the response text -- see its source), so this is the one place
 * that number is never ambiguous. It is also the *only* place `toProblem` can
 * safely decide a bare `{ detail: "<message>" }` body means "the session is gone"
 * (`code: 'unauthenticated'`) rather than "some other HTTPException" (a 404, a
 * 405, ...) -- see `toProblem`'s own docstring for why shape alone cannot tell
 * those apart. This is also what makes `queryClient`'s retry policy able to ask
 * "was this a 401?" at all -- silently losing the status would make that check
 * never fire for the exact case it exists for.
 */
export async function unwrap<T>(
  promise: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  const { data, error, response } = await promise.catch((networkError: unknown) => {
    // fetch() itself throws for a true network failure (offline, DNS, CORS) --
    // openapi-fetch never gets a chance to run, so there is no `response` at all
    // here, unlike every other branch below.
    throw toProblem(networkError)
  })
  if (error !== undefined) throw toProblem(error, response.status)
  return data as T
}
