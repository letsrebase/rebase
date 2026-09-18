import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useEffect, useState, type FormEvent } from 'react'
import { BrandMark } from '@/components/BrandMark'
import { Button } from '@rebase/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@rebase/ui/card'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { api, toProblem, unwrap } from '@/lib/api'
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

const TERMINI = 'https://letsrebase.com/termini'
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
  // Three screens: the email, the owner card (this address already has a space), the name.
  const [screen, setScreen] = useState<'email' | 'owner' | 'name'>('email')
  const [email, setEmail] = useState('')
  const [member, setMember] = useState<Member | null>(null)
  const [linkSent, setLinkSent] = useState(false)
  const [nome, setNome] = useState('')
  const [slug, setSlug] = useState('')
  const [slugTouched, setSlugTouched] = useState(false)
  const [editingSlug, setEditingSlug] = useState(false)
  const [availability, setAvailability] = useState<Availability>({ state: 'idle' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

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
    setNome(value)
    if (!slugTouched) setSlug(slugify(value))
  }

  async function onEmailNext(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setBusy(true)
    try {
      const address = email.trim()
      const answer = await unwrap(api.POST('/api/tenants/membro', { body: { email: address } }))
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
  const canCreate = !busy && (isFree || failed) && nome.trim() !== ''

  const hasSpaces = screen === 'owner'

  function backToEmail() {
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
            {hasSpaces ? 'Hai già uno spazio' : 'Crea il tuo spazio'}
          </CardTitle>
          <CardDescription>
            {hasSpaces
              ? linkSent
                ? 'Controlla la posta: il link per entrare vale 15 minuti.'
                : 'Questa email ha già uno spazio PigroCRM. Ti mandiamo il link per entrare.'
              : screen === 'email'
                ? '1 di 2. Un PigroCRM tutto tuo, con i tuoi dati in un database separato.'
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

export const Route = createFileRoute('/app/registrati')({ component: SignupPage })
