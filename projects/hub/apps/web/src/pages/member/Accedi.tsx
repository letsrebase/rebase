import { Link, useLocation, useNavigate } from '@tanstack/react-router'
import { useEffect, useState, type FormEvent } from 'react'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { ApiError } from '@/lib/api'
import { useMe, useRequestLink } from '@/lib/me'
import { loginAttribution, rememberLoginAttribution } from '@/lib/utm'

/** The way in: an address, a link by mail, no password. The page says the same thing
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
export function Accedi() {
  const me = useMe()
  const navigate = useNavigate()
  const requestLink = useRequestLink()
  const searchStr = useLocation({ select: (location) => location.searchStr })
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
    <form onSubmit={submit} className="mx-auto max-w-md space-y-6">
      <div>
        <h1 className="text-3xl font-semibold tracking-tight">Entra nella tua area</h1>
        <p className="mt-2 text-muted-foreground">
          L’indirizzo che ci hai dato: ti mandiamo un link, senza password.
        </p>
      </div>
      <div className="space-y-2">
        <Label htmlFor="email">Email</Label>
        <Input
          id="email"
          type="email"
          required
          autoComplete="email"
          autoFocus
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
  )
}
