import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { BrandMark } from '@/components/BrandMark'
import { Button } from '@rebase/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@rebase/ui/card'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { api, toProblem, unwrap } from '@/lib/api'
import { readAndClearRegisterHandoffEmail } from '@/lib/registerHandoff'
import { slugProblem, slugify } from '@/lib/tenant'

/** The public host the space will answer on, for the preview under the name field. */
function spaceHost(): string {
  return typeof window === 'undefined' ? '' : window.location.host
}

/** What the server last said about a slug -- tagged with the slug it was said about, so
 *  an answer for the previous name never shows under the current one. */
type Availability =
  | { state: 'idle' }
  | { state: 'checking'; slug: string }
  | { state: 'free'; slug: string }
  | { state: 'taken'; slug: string; reason: string }
  | { state: 'failed'; slug: string; reason: string }

/** What the hub and the registry said about the address typed at step 1. */
interface Member {
  membro: boolean
  nome: string | null
  cognome: string | null
  spazi: number
}

const TERMINI = 'https://letsrebase.com/terms'
const PRIVACY = 'https://letsrebase.com/privacy'

/**
 * Two steps and a landing (spec 2026-09-12 §6.4). The email first: the CRM asks the hub
 * whether it knows the address, so a member finds the name already written, and asks
 * its own registry whether a space exists for it, so nobody creates a second one by
 * mistake (a link by mail is offered instead). Then the name of the space, with the
 * address derived and editable on request. No password: the 201 opens the session and
 * the page lands inside the space. `go` is injectable for the tests (jsdom cannot spy on
 * `window.location.assign`).
 */
export function SignupPage({ go = (url) => window.location.assign(url) }: { go?: (url: string) => void }) {
  const navigate = useNavigate()
  // AppShell's sidebar (REB-482, REB-488) already knows who is asking and that they
  // hold a space right now: it hands its own email to this page through
  // sessionStorage, read once and cleared immediately, never a URL (a `?email=` sits
  // in browser history and an analytics pageview capture, and cannot be cross-checked
  // against a session either way: PigroCRM's access/refresh cookies are scoped
  // `path=/<slug>/`, so a request from the unprefixed `/app/register` never carries
  // the space's own session to compare against -- confirmed against `tenancy.py`'s
  // `cookie_path` and `routers/tenants.py`'s `path=f"/{tenant.slug}/"`, Greptile,
  // PR #425). sessionStorage is what actually closes the threat a `?email=` opened: a
  // crafted link cannot write to this origin's storage at all, only this origin's own
  // script can, so the only way a value lands here is this exact click, in this exact
  // tab. `readAndClearRegisterHandoffEmail` also bounds how long a written value is
  // honoured, so a navigation that never completed -- and so was never read -- cannot
  // sit in a shared tab's storage to be picked up by whoever opens this page in it
  // next, on a kiosk or after the first person logged out (Greptile, PR #425, on the
  // version with no expiry). The chooser has no such email to hand over in the first
  // place (`IdentitySpace`'s own "an unproven email learns a number, never a list"),
  // so this fast path exists only from inside an authenticated space, never from the
  // chooser.
  const [handoffEmail] = useState(readAndClearRegisterHandoffEmail)

  // Three screens: the email, the owner card (this address already has a space), the
  // name. `screenState`/`emailState` are the state machine's own, mutated only by an
  // explicit transition (`onEmailNext`, `backToEmail`); `screen`/`email` below are
  // what the rest of this component reads, the fast path folded in without an effect.
  const [screenState, setScreen] = useState<'email' | 'owner' | 'name'>('email')
  const [emailState, setEmail] = useState('')
  const [member, setMember] = useState<Member | null>(null)
  const [linkSent, setLinkSent] = useState(false)
  const [nome, setNome] = useState('')
  const [slug, setSlug] = useState('')
  const [slugTouched, setSlugTouched] = useState(false)
  const [editingSlug, setEditingSlug] = useState(false)
  const [availability, setAvailability] = useState<Availability>({ state: 'idle' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Whether the fast path's background member lookup (below) has settled, one way or
  // the other: the create button must not read a still-null `member` as a confirmed
  // non-member and send `membro: false` for someone who really is one, and a lookup
  // that fails outright must not block the button forever either (Greptile P1,
  // PR #425). Irrelevant outside the fast path -- `canCreate` below only consults it
  // while `fastPathActive` is true.
  const [memberSettled, setMemberSettled] = useState(false)
  // Whether `nome` carries anything the person (or this page) put there, read fresh by
  // the fast path's background lookup below -- unlike `nome === ''`, which a one-time
  // mount effect would see through the closure it was created with (empty, always),
  // never what is on screen once the request actually answers.
  const nomeTouched = useRef(false)
  // Set once the person explicitly leaves the fast path (`backToEmail`), including
  // while its background lookup is still in flight. Two flags, not one: `left` is
  // read during render (below), which a ref may never be (`react-hooks/refs`);
  // `cancelled` is read only inside the background lookup's own async callback,
  // where a ref is exactly right and state would risk a stale closure. Both are set
  // together, and the fast path never re-activates for the rest of this visit even
  // though `screenState`/`emailState` return to the same values it started at.
  const [fastPathLeft, setFastPathLeft] = useState(false)
  const fastPathCancelled = useRef(false)

  // Computed at render time rather than committed by an effect (no cascading
  // setState, `react-hooks/set-state-in-effect`): active for as long as there was a
  // handoff and the state machine is still sitting at its untouched defaults, which
  // is also exactly what makes it stop being active the moment either changes.
  const fastPathActive = handoffEmail !== null && !fastPathLeft && screenState === 'email' && emailState === ''
  const screen = fastPathActive ? 'name' : screenState
  const email = fastPathActive ? (handoffEmail as string) : emailState

  // The local grammar check needs no round-trip and no state: a malformed name never
  // leaves the browser.
  const localProblem = slug === '' ? null : slugProblem(slug)

  // Ask the server whether a well-formed address is free, a moment after typing stops.
  useEffect(() => {
    if (screen !== 'name' || slug === '' || localProblem) return
    const asked = slug
    let stale = false
    const handle = window.setTimeout(() => {
      setAvailability({ state: 'checking', slug: asked })
      void api
        .GET('/api/tenants/{slug}/disponibile', { params: { path: { slug: asked } } })
        .then(({ data, error: apiError, response }) => {
          if (stale) return
          if (data) {
            setAvailability(
              data.disponibile
                ? { state: 'free', slug: asked }
                : { state: 'taken', slug: asked, reason: data.motivo ?? 'questo nome è già in uso' },
            )
            return
          }
          // The probe has its own rate limit (REB-228), separate from the create
          // request's: a throttled check must not leave the wizard stuck on
          // "checking" forever with no explanation, so a 429 drops back to idle and
          // says why on the same alert `onCreate` uses.
          if (response.status === 429) {
            setAvailability({ state: 'idle' })
            setError(toProblem(apiError, response.status).detail)
            return
          }
          // Any other failure (a 5xx, a malformed answer) must not leave the button
          // disabled with a "still checking" hint that never resolves either
          // (REB-236): the hint shows the API's own sentence and the person can submit
          // anyway, since POST /api/tenants/ validates the slug again server-side.
          setAvailability({ state: 'failed', slug: asked, reason: toProblem(apiError, response.status).detail })
        })
        .catch((networkError: unknown) => {
          if (stale) return
          setAvailability({ state: 'failed', slug: asked, reason: toProblem(networkError).detail })
        })
    }, 350)
    // A response for a slug that is no longer current (superseded by a further edit,
    // or the effect re-running for any other reason) must never write: `current`
    // gates on `availability.slug === slug`, so a stale write for a slug nobody is
    // asking about anymore leaves the hint stuck on the "still checking" ellipsis
    // with nothing left to retry (REB-265).
    return () => {
      stale = true
      window.clearTimeout(handle)
    }
  }, [screen, slug, localProblem])

  // The name proposes the address until the person edits the address by hand.
  function onNomeChange(value: string) {
    nomeTouched.current = value !== ''
    setNome(value)
    if (!slugTouched) setSlug(slugify(value))
  }

  // The fast path skips straight to the name step, but the hub's answer (the
  // proposed name, the `membro` flag the create call sends) still matters: fetched
  // here in the background rather than blocking the form on it. `nomeTouched` is
  // read fresh at resolution time so a name the person already started typing is
  // never clobbered by a slow answer landing after them, and `memberSettled` keeps
  // the create button off until this settles one way or the other.
  useEffect(() => {
    if (!fastPathActive || !handoffEmail) return
    const address = handoffEmail
    void api
      .POST('/api/tenants/member', { body: { email: address } })
      .then(({ data }) => {
        if (!data || fastPathCancelled.current) return
        setMember(data)
        if (nomeTouched.current) return
        const proposed = [data.nome, data.cognome].filter(Boolean).join(' ')
        if (proposed) onNomeChange(proposed)
      })
      .catch(() => {})
      .finally(() => setMemberSettled(true))
    // eslint-disable-next-line react-hooks/exhaustive-deps -- onNomeChange is a plain function redefined every render; this must fire only when the fast path (re)activates or the lookup's own email changes, not on every render
  }, [fastPathActive, handoffEmail])

  async function onEmailNext(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setBusy(true)
    try {
      const address = email.trim()
      const answer = await unwrap(api.POST('/api/tenants/member', { body: { email: address } }))
      setEmail(address)
      setMember(answer)
      // The member's name proposes the space's name, whichever screen comes next: whoever
      // owns a space and still wants another finds it written too.
      const proposed = [answer.nome, answer.cognome].filter(Boolean).join(' ')
      if (proposed && nome === '') onNomeChange(proposed)
      setScreen(answer.spazi > 0 ? 'owner' : 'name')
    } catch (caught) {
      setError(toProblem(caught).detail)
    } finally {
      setBusy(false)
    }
  }

  async function onSendLink() {
    setError(null)
    setBusy(true)
    try {
      await unwrap(api.POST('/api/auth/link', { body: { email } }))
      setLinkSent(true)
    } catch (caught) {
      setError(toProblem(caught).detail)
    } finally {
      setBusy(false)
    }
  }

  async function onCreate(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setBusy(true)
    try {
      const tenant = await unwrap(
        api.POST('/api/tenants/', {
          body: { slug, nome: nome.trim(), email, membro: member?.membro ?? false },
        }),
      )
      // The 201 set the space's cookies: a different basepath is a different application
      // instance, so this is a navigation, not a router push.
      go(`/${tenant.slug}/app/`)
    } catch (caught) {
      setError(toProblem(caught).detail)
      setBusy(false)
    }
  }

  // Only an answer about *this* slug counts; anything else is still pending.
  const current = availability.state !== 'idle' && availability.slug === slug ? availability : null
  // A "taken" reason is a real problem with the slug; a "failed" one is not -- the probe
  // itself could not tell, so it never marks the field invalid, only explains itself.
  const problem = localProblem ?? (current?.state === 'taken' ? current.reason : null)
  const failed = current?.state === 'failed'
  const isFree = current?.state === 'free'
  // A failed probe never blocks the button: the server validates the slug again on
  // POST /api/tenants/, so there is a real answer either way instead of a dead end.
  // Outside the fast path `memberSettled` never matters (`!fastPathActive` alone
  // clears the gate): the ordinary flow already awaits the member lookup before the
  // name screen ever shows.
  const canCreate = !busy && (isFree || failed) && nome.trim() !== '' && (memberSettled || !fastPathActive)

  const hasSpaces = screen === 'owner'

  function backToEmail() {
    fastPathCancelled.current = true
    setFastPathLeft(true)
    setScreen('email')
    setMember(null)
    setLinkSent(false)
    setError(null)
    setNome('')
    setSlug('')
    setSlugTouched(false)
    setEditingSlug(false)
  }

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle className="inline-flex items-center text-2xl">
            <BrandMark className="mr-2.5 size-3.5" />
            {hasSpaces ? 'Hai già uno spazio' : screen === 'email' ? 'Crea il tuo spazio' : fastPathActive ? 'Crea un nuovo spazio' : 'Crea il tuo spazio'}
          </CardTitle>
          <CardDescription>
            {hasSpaces
              ? linkSent
                ? 'Controlla la posta: il link per entrare vale 15 minuti.'
                : 'Questa email ha già uno spazio PigroCRM. Ti mandiamo il link per entrare.'
              : screen === 'email'
                ? '1 di 2. Un PigroCRM tutto tuo, con i tuoi dati in un database separato.'
                : fastPathActive
                  ? member?.membro
                    ? `Sei dei nostri${member.nome ? `: ciao ${member.nome}` : ''}.`
                    : 'Come si chiama il nuovo spazio?'
                  : member?.membro
                    ? `2 di 2. Sei dei nostri${member.nome ? `: ciao ${member.nome}` : ''}.`
                    : '2 di 2. Come si chiama il tuo spazio?'}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {hasSpaces ? (
            <div className="space-y-3">
              {!linkSent && (
                <Button className="w-full" disabled={busy} onClick={() => void onSendLink()}>
                  {busy ? 'Invio in corso…' : 'Mandami il link per entrare'}
                </Button>
              )}
              {error && (
                <p className="text-destructive text-sm" role="alert">
                  {error}
                </p>
              )}
              <Button
                type="button"
                variant="ghost"
                className="w-full"
                onClick={() => {
                  setScreen('name')
                  setLinkSent(false)
                  setError(null)
                }}
              >
                Vuoi crearne un altro?
              </Button>
              <Button type="button" variant="link" className="w-full" onClick={backToEmail}>
                Cambia email
              </Button>
            </div>
          ) : screen === 'email' ? (
            <form onSubmit={onEmailNext} className="space-y-4" noValidate>
              <div className="space-y-2">
                <Label htmlFor="email">Con quale email ti conosciamo?</Label>
                <Input
                  id="email"
                  type="email"
                  autoComplete="email"
                  required
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                />
                <p className="text-muted-foreground text-sm">
                  Se sei nella community rebase, usa la stessa. È anche il tuo modo di entrare:
                  niente password, un link via mail.
                </p>
              </div>
              {error && (
                <p className="text-destructive text-sm" role="alert">
                  {error}
                </p>
              )}
              <Button type="submit" className="w-full" disabled={busy || email.trim() === ''}>
                {busy ? 'Un momento…' : 'Avanti'}
              </Button>
              <Button
                type="button"
                variant="ghost"
                className="w-full"
                onClick={() => void navigate({ to: '/app/login' })}
              >
                Ho già uno spazio: entra
              </Button>
            </form>
          ) : (
            <form onSubmit={onCreate} className="space-y-4" noValidate>
              <div className="space-y-2">
                <Label htmlFor="nome">Come si chiama il tuo spazio?</Label>
                <Input
                  id="nome"
                  autoComplete="name"
                  required
                  value={nome}
                  onChange={(event) => onNomeChange(event.target.value)}
                />
                {editingSlug ? (
                  <div className="space-y-2">
                    <Label htmlFor="slug">Indirizzo dello spazio</Label>
                    <Input
                      id="slug"
                      autoComplete="off"
                      value={slug}
                      aria-invalid={problem ? true : undefined}
                      aria-describedby="slug-hint"
                      onChange={(event) => {
                        setSlugTouched(true)
                        setSlug(event.target.value.toLowerCase())
                      }}
                    />
                  </div>
                ) : null}
                <p id="slug-hint" className="text-muted-foreground text-sm" role="status">
                  {problem ??
                    (current?.state === 'failed'
                      ? `${spaceHost()}/${slug}: ${current.reason}`
                      : slug === ''
                        ? 'L’indirizzo lo ricaviamo dal nome.'
                        : isFree
                          ? `${spaceHost()}/${slug} è libero.`
                          : `${spaceHost()}/${slug} …`)}
                  {!editingSlug && slug !== '' && (
                    <>
                      {' '}
                      <button
                        type="button"
                        className="underline underline-offset-4"
                        onClick={() => setEditingSlug(true)}
                      >
                        cambia
                      </button>
                    </>
                  )}
                </p>
              </div>
              {member && !member.membro && (
                <p className="text-muted-foreground text-sm">
                  Non sei ancora nella community rebase? Puoi entrare comunque: nella mail ti
                  raccontiamo cos’è.
                </p>
              )}
              {error && (
                <p className="text-destructive text-sm" role="alert">
                  {error}
                </p>
              )}
              <Button type="submit" className="w-full" disabled={!canCreate}>
                {busy ? 'Creazione in corso…' : 'Crea lo spazio'}
              </Button>
              <p className="text-muted-foreground text-center text-xs">
                Creando lo spazio accetti i{' '}
                <a className="underline" href={TERMINI}>
                  termini
                </a>{' '}
                e la{' '}
                <a className="underline" href={PRIVACY}>
                  privacy
                </a>
                .
              </p>
              <Button type="button" variant="ghost" className="w-full" onClick={backToEmail}>
                Indietro
              </Button>
            </form>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

export const Route = createFileRoute('/app/register')({ component: SignupPage })
