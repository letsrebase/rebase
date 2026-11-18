import { Link, useLocation, useNavigate } from '@tanstack/react-router'
import { useEffect, useState, type FormEvent } from 'react'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { ApiError, member } from '@/lib/api'
import { useAuthProviders, useMe, useRequestLink } from '@/lib/me'
import { peekCliCode, takeCliCode } from '@/lib/returnTo'
import { loginAttribution, rememberLoginAttribution } from '@/lib/utm'

/** What the sign-in with a provider says when it sends the visitor back here with
 *  `?oauth=<reason>` (REB-1137). Every one ends with the way that always works. */
const OAUTH_MESSAGES: Record<string, string> = {
  denied: 'Hai annullato l’accesso. Puoi riprovare, oppure entrare con il link via mail.',
  expired:
    'L’accesso è scaduto, o è stato aperto in un altro browser. Riprova da qui, oppure entra con il link via mail.',
  unverified:
    'Non abbiamo ricevuto un indirizzo verificato. Verificalo dal tuo account, oppure entra con il link via mail.',
  failed: 'Non siamo riusciti a completare l’accesso. Riprova, oppure entra con il link via mail.',
  limited: 'Troppi tentativi ravvicinati. Aspetta un minuto e riprova.',
  off: 'Questo accesso non è attivo. Entra con il link via mail.',
}

/** The way in: «Accedi con GitHub» and «Accedi con Google» when the deployment has them,
 *  and always an address and a link by mail, no password. The page says the same thing
 *  whether the address is known or not, as the API does. The campaign in this page's own
 *  URL goes with the address, so the login it leads to says which mail brought the
 *  person back (REB-426), and the page remembers it for the tab, so a detour through the
 *  home or the area back to a bare `/login` still sends it (REB-455). A campaign the tab
 *  remembers from the landing or a wizard does not.
 *
 *  A visitor who already carries a session -- a bookmark, a shared link, `Thanks.tsx`'s
 *  own `<Link to="/login">`, or `/hub/login` reached before the marketing site's
 *  `session.js` ever got a chance to point the click at `/hub/me` or `/hub/admin` --
 *  has no use for the request-link form: `SignedInLayout` and PigroCRM's own
 *  `/app/login` both send an already-authenticated visitor straight to their own area,
 *  and this page does the same, to the same two doors `session.js`'s `landingRoute()`
 *  already computes. */
export function Login() {
  const me = useMe()
  const navigate = useNavigate()
  const requestLink = useRequestLink()
  const searchStr = useLocation({ select: (location) => location.searchStr })
  const providers = useAuthProviders()
  const [email, setEmail] = useState('')
  const [sent, setSent] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // On arrival, not at submit: the person may leave before asking for the link.
  useEffect(() => rememberLoginAttribution(searchStr), [searchStr])

  useEffect(() => {
    if (me.data) void navigate({ to: me.data.role === 'admin' ? '/admin' : '/me', replace: true })
  }, [me.data, navigate])

  function submit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    requestLink.mutate({ email: email.trim(), utm: loginAttribution(searchStr) }, {
      onSuccess: () => setSent(true),
      onError: (failure) =>
        setError(
          failure instanceof ApiError
            ? failure.message
            : 'Non siamo riusciti a mandarti il link. Riprova.',
        ),
    })
  }

  const oauthReason = new URLSearchParams(searchStr).get('oauth')
  const oauthMessage = oauthReason ? (OAUTH_MESSAGES[oauthReason] ?? OAUTH_MESSAGES.failed) : null
  const offered = (['github', 'google'] as const).filter((name) => providers.data?.[name])

  /** The start link carries the campaign this page remembers and, for a visitor sent here by
   *  `/hub/cli`, the code to come back to: the provider's redirect is a full navigation, so
   *  the page's own state does not survive it. */
  function startHref(name: 'github' | 'google') {
    const extra: Record<string, string> = { ...loginAttribution(searchStr) }
    const code = peekCliCode()
    if (code) extra.next = `/cli?code=${code}`
    return member.oauthStartUrl(name, extra)
  }

  // Nothing to show while the session is still resolving (the pending state every
  // bookmark, shared link or direct hit to this page starts from, with no `me` query
  // already cached) or once one is found: the effect above either has not decided yet
  // or is already navigating away. Rendering the form in between would flash it at an
  // already-signed-in visitor for exactly as long as `GET /api/hub/me` takes.
  if (me.isPending || me.data) return null

  if (sent) {
    return (
      <div className="mx-auto max-w-xl space-y-4 text-center">
        <h1 className="text-3xl font-semibold tracking-tight">Controlla la posta</h1>
        <p className="text-muted-foreground">
          Se sei dentro, ti abbiamo scritto: apri la mail e segui il link. Vale quindici minuti.
        </p>
        <p className="text-sm text-muted-foreground">
          Non arriva? Guarda nello spam, oppure{' '}
          <button
            type="button"
            className="underline underline-offset-2"
            onClick={() => setSent(false)}
          >
            chiedine un altro
          </button>
          .
        </p>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-md space-y-6">
      <div>
        <h1 className="text-3xl font-semibold tracking-tight">Entra nella tua area</h1>
        <p className="mt-2 text-muted-foreground">
          {offered.length > 0
            ? 'Con il tuo account GitHub o Google, oppure con un link per mail, senza password.'
            : 'L’indirizzo che ci hai dato: ti mandiamo un link, senza password.'}
        </p>
      </div>
      {oauthMessage && (
        <p role="alert" className="text-sm text-destructive">
          {oauthMessage}
        </p>
      )}
      {offered.length > 0 && (
        <div className="space-y-3">
          {offered.map((name) => (
            <Button key={name} asChild variant="outline" className="w-full">
              <a href={startHref(name)} onClick={() => void takeCliCode()}>
                {name === 'github' ? 'Accedi con GitHub' : 'Accedi con Google'}
              </a>
            </Button>
          ))}
          <p className="text-sm text-muted-foreground">
            Funziona se l’indirizzo verificato del tuo account è quello che ci hai dato.
          </p>
          <div role="separator" className="flex items-center gap-3 text-xs text-muted-foreground">
            <span className="h-px flex-1 bg-border" />
            oppure
            <span className="h-px flex-1 bg-border" />
          </div>
        </div>
      )}
      <form onSubmit={submit} className="space-y-6">
        <div className="space-y-2">
          <Label htmlFor="email">Email</Label>
          <Input
            id="email"
            type="email"
            required
            autoComplete="email"
            autoFocus={offered.length === 0}
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </div>
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        <Button type="submit" className="w-full" disabled={requestLink.isPending}>
          {requestLink.isPending ? 'Un attimo…' : 'Mandami il link'}
        </Button>
        <p className="text-sm text-muted-foreground">
          Non sei ancora dentro?{' '}
          <Link to="/freelance" className="underline underline-offset-2">
            Raccontaci chi sei
          </Link>
          .
        </p>
      </form>
    </div>
  )
}
