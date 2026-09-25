import { BrandMark } from '@/components/BrandMark'
import { createFileRoute, useNavigate, useSearch } from '@tanstack/react-router'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { toast } from '@rebase/ui/sonner'
import { defaultDashboardSearch } from '@/features/dashboard/search'
import { Button } from '@rebase/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@rebase/ui/card'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { api, toProblem, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { useAuth } from '@/lib/auth'
import { roleLabel } from '@/lib/roles'
import { safeAppRedirect, tenantPrefix } from '@/lib/tenant'

type IdentitySpace = components['schemas']['IdentitySpace']

export function LoginPage({
  go = (url) => window.location.assign(url),
}: { go?: (url: string) => void } = {}) {
  const { user, login } = useAuth()
  const navigate = useNavigate()
  const { redirect } = useSearch({ from: '/app/login' })
  // Read once, for the one-time root-detection effect below: that effect intentionally
  // never re-runs, so it closes over the `redirect` this page was mounted with rather
  // than depending on a value it would otherwise have to re-fire on.
  const initialRedirect = useRef(redirect).current
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  // Email-first since ORB-172 (spec 2026-09-12 §6.2): the way in is a link by mail, as
  // in the community. The password form stays as the second way for the accounts that
  // have one (the root, the spaces of the first days, the e2e admin) and is opened on
  // purpose; nobody is asked for a password by default any more.
  const [mode, setMode] = useState<'link' | 'password'>('link')
  const [sent, setSent] = useState(false)
  const [linkError, setLinkError] = useState<string | null>(null)
  // Signup is offered by the root only. The root is the unprefixed page -- or the page
  // under the root's own space name (PIGROCRM_ROOT_SLUG), which only the API knows.
  const [isRoot, setIsRoot] = useState(tenantPrefix === '')
  // The root's own name, when it has one: where a login on the bare page sends the
  // person afterwards. `null` until the API has answered, `''` when there is none.
  const [rootSlug, setRootSlug] = useState<string | null>(null)
  // REB-377 (design §3): the chooser this page shows instead of the form once the
  // identity cookie resolves to at least one space. A 401 (no cookie) or a 200 with
  // none leaves this empty, which is exactly the fallback to the form below -- no
  // separate error branch needed for either.
  const [spaces, setSpaces] = useState<IdentitySpace[]>([])
  const [enteringSlug, setEnteringSlug] = useState<string | null>(null)
  useEffect(() => {
    void api
      .GET('/api/identity/spaces')
      .then(({ data }) => {
        if (Array.isArray(data) && data.length > 0) setSpaces(data)
      })
      .catch(() => {})
  }, [])
  useEffect(() => {
    void api.GET('/api/tenants/root').then(({ data }) => {
      const slug = data?.slug ?? ''
      setRootSlug(slug)
      if (slug === '') return
      if (tenantPrefix === '') {
        setIsRoot(true)
        return
      }
      if (`/${slug}` !== tenantPrefix) return
      // The login is nobody's page (decision 2026-09-09): a person who has just logged
      // out, or whose session ran out, must not read a space's name in the address. The
      // root's login lives at the bare `/app/login`, which nginx leaves alone and which
      // sets the root's cookies at `/`, the one jar this page and `/<slug>/app` both
      // read. A replace, not a push: the aliased address is not worth a history entry.
      // A `redirect` this page arrived with names a deep link, still meant for the
      // root's login once it gets there, so it rides along on the query string.
      window.location.replace(
        initialRedirect ? `/app/login?redirect=${encodeURIComponent(initialRedirect)}` : '/app/login',
      )
    }).catch(() => setRootSlug(''))
    // eslint-disable-next-line react-hooks/exhaustive-deps -- initialRedirect is a ref read once by design
  }, [])

  /**
   * The redirect is driven by the *session*, never by "the login call returned".
   *
   * `await login(...); await navigate({ to: '/app' })` -- what this was -- looks
   * equivalent and is not. `AuthProvider.login` publishes the new user by writing it
   * into react-query's cache, and react-query delivers that write to its subscribers
   * through `notifyManager`, which batches onto a later microtask. So the navigation
   * issued on the very next line runs a render of `/app`'s layout (`routes/app.tsx`)
   * in which `useAuth().user` is still `null` -- and that layout's own
   * "unauthenticated visitors go to the login page" effect immediately pushes back to
   * /app/login. Confirmed live, deterministically, on this machine: `history` recorded
   * exactly `push /app` followed by `push /app/login`, with a 200 from
   * `POST /api/auth/login` and both session cookies set, so a correct login landed the
   * user back on the login form and `e2e/auth.spec.ts`'s third test failed on «Ciao
   * E2E» with no other symptom. Waiting for `user` to actually be non-null removes the
   * race by construction rather than by ordering luck.
   *
   * It also gives an already-authenticated visitor who lands on /app/login the same
   * treatment, which is the behaviour that screen should have had anyway: the login
   * form is not a page a live session has any use for.
   */
  useEffect(() => {
    if (!user) return
    // A login on the bare page of a root that has a name continues under that name:
    // `/studiorossi/app` is where the CRM lives, and a different basepath is a
    // different application instance, so this is a navigation, not a router push. The
    // cookies are the root's, at `/`, and travel with it. Waits for the root endpoint
    // rather than guessing: a push to `/app` first and a hop afterwards would flash the
    // bare home for a moment.
    if (tenantPrefix === '' && rootSlug === null) return
    // A deep link interrupted by the guard (routes/app.tsx) names its own return path
    // in `redirect`; a session that reached this page any other way (typed the
    // address, followed a bookmark) has none, and gets the dashboard as before.
    // `safeAppRedirect` (lib/tenant.ts) is the one gate on it: a value it accepts is
    // basepath-relative, so it composes with a root name the same way `/app/` itself
    // does below.
    const safeRedirect = safeAppRedirect(redirect)
    if (tenantPrefix === '' && rootSlug !== '') {
      window.location.assign(safeRedirect ? `/${rootSlug}${safeRedirect}` : `/${rootSlug}/app/`)
      return
    }
    if (safeRedirect) {
      void navigate({ href: safeRedirect })
      return
    }
    // `/app/` declares `validateSearch` since slice 6, so its search params are part of
    // its type and this redirect has to name them. A fresh login has no period in mind,
    // which is what `defaultDashboardSearch()` answers -- and landing with the month
    // already in the URL means the first thing the user could screenshot or paste to a
    // colleague already says which period it is about (§4).
    void navigate({ to: '/app', search: defaultDashboardSearch() })
  }, [user, navigate, rootSlug, redirect])

  async function onSubmit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    try {
      await login(email, password)
    } catch (error) {
      // The API deliberately does not say whether it was the email or the password.
      toast.error(toProblem(error).detail)
    } finally {
      setBusy(false)
    }
  }

  async function onSendLink(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setLinkError(null)
    try {
      // 202 whether the address is known or not: the page cannot tell, by design.
      await unwrap(api.POST('/api/auth/link', { body: { email } }))
      setSent(true)
    } catch (error) {
      // The one honest failure: no sender on this installation (503), with the API's
      // sentence, which already says to use the password instead.
      setLinkError(toProblem(error).detail)
    } finally {
      setBusy(false)
    }
  }

  /** Opens the chosen space with no second proof (design §3): the identity cookie
   *  already is one. A different basepath is a different application instance, so
   *  landing there is a full navigation, the same shape every other cross-space move
   *  on this page already uses. */
  async function onEnter(slug: string) {
    setEnteringSlug(slug)
    try {
      await unwrap(api.POST('/api/identity/enter/{slug}', { params: { path: { slug } } }))
      go(`/${slug}/app/`)
    } catch (error) {
      toast.error(toProblem(error).detail)
      setEnteringSlug(null)
    }
  }

  /** Where "Crea un nuovo spazio"/"Crea il tuo spazio" lands, whether this identity
   *  already has spaces or not: the signup page lives at the unprefixed root, so a
   *  visitor on any other basepath gets a full navigation, the same shape every
   *  other cross-space move on this page already uses. */
  function goToRegister() {
    if (tenantPrefix === '') {
      void navigate({ to: '/app/register' })
      return
    }
    window.location.assign('/app/register')
  }

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle className="inline-flex items-center text-2xl">
            <BrandMark className="mr-2.5 size-3.5" />
            Pigro<span className="text-[var(--color-watermelon)]">CRM</span>
          </CardTitle>
          <CardDescription>Il CRM che lavora al posto tuo.</CardDescription>
        </CardHeader>
        <CardContent>
          {spaces.length > 0 ? (
            <div className="space-y-2">
              <p className="text-muted-foreground text-sm">
                Questa email apre già questi spazi.
              </p>
              {spaces.map((space) => (
                <Button
                  key={space.slug}
                  type="button"
                  variant="outline"
                  className="w-full justify-between"
                  disabled={enteringSlug !== null}
                  onClick={() => void onEnter(space.slug)}
                >
                  <span>{space.slug}</span>
                  <span className="text-muted-foreground text-xs">{roleLabel(space.ruolo)}</span>
                </Button>
              ))}
              <Button
                type="button"
                variant="ghost"
                className="w-full"
                disabled={enteringSlug !== null}
                onClick={goToRegister}
              >
                Crea un nuovo spazio
              </Button>
            </div>
          ) : (
            <>
              {mode === 'link' && sent ? (
                <div className="space-y-4">
                  <p className="text-sm" role="status">
                    Controlla la posta: il link per entrare vale 15 minuti. Se non arriva, guarda
                    nello spam.
                  </p>
                  <Button
                    type="button"
                    variant="ghost"
                    className="w-full"
                    onClick={() => {
                      setSent(false)
                      setEmail('')
                    }}
                  >
                    Usa un&apos;altra email
                  </Button>
                </div>
              ) : (
                <form
                  onSubmit={mode === 'link' ? onSendLink : onSubmit}
                  className="space-y-4"
                  noValidate={mode === 'link'}
                >
                  <div className="space-y-2">
                    <Label htmlFor="email">Email</Label>
                    <Input
                      id="email"
                      type="email"
                      required
                      autoComplete="username"
                      value={email}
                      onChange={(event) => setEmail(event.target.value)}
                    />
                  </div>
                  {mode === 'password' && (
                    <div className="space-y-2">
                      <Label htmlFor="password">Password</Label>
                      <Input
                        id="password"
                        type="password"
                        required
                        autoComplete="current-password"
                        value={password}
                        onChange={(event) => setPassword(event.target.value)}
                      />
                    </div>
                  )}
                  {linkError && (
                    <p className="text-destructive text-sm" role="alert">
                      {linkError}
                    </p>
                  )}
                  {mode === 'link' ? (
                    <Button type="submit" className="w-full" disabled={busy || email === ''}>
                      {busy ? 'Invio in corso…' : 'Mandami il link'}
                    </Button>
                  ) : (
                    <Button type="submit" className="w-full" disabled={busy}>
                      {busy ? 'Accesso in corso…' : 'Accedi'}
                    </Button>
                  )}
                  {mode === 'link' ? (
                    <Button
                      type="button"
                      variant="link"
                      className="w-full"
                      onClick={() => {
                        setMode('password')
                        setLinkError(null)
                      }}
                    >
                      Hai una password? Accedi con la password
                    </Button>
                  ) : (
                    <Button
                      type="button"
                      variant="link"
                      className="w-full"
                      onClick={() => setMode('link')}
                    >
                      Torna al link via email
                    </Button>
                  )}
                </form>
              )}
              {/* This form's own empty state still offers signup only on the root: the
                  logged-out visitor here has proven no email yet, so there is nothing to
                  scope "another space" to. An authenticated sidebar (AppShell) and this
                  page's own chooser above do offer it from any space, since 2026-09-25 --
                  the 2026-09-08 §6 "no space creates spaces" rule stops at signed-out. */}
              {isRoot && (
                <Button
                  type="button"
                  variant="outline"
                  className="mt-4 w-full"
                  onClick={goToRegister}
                >
                  Crea il tuo spazio
                </Button>
              )}
            </>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

export const Route = createFileRoute('/app/login')({
  component: LoginPage,
  validateSearch: (search: Record<string, unknown>): { redirect?: string } => ({
    redirect: typeof search.redirect === 'string' ? search.redirect : undefined,
  }),
})
