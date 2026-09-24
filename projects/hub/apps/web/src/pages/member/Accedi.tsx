import { Link, useLocation } from '@tanstack/react-router'
import { useState, type FormEvent } from 'react'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { ApiError } from '@/lib/api'
import { useRequestLink } from '@/lib/me'
import { resolveAttribution } from '@/lib/utm'

/** The way in: an address, a link by mail, no password. The page says the same thing
 *  whether the address is known or not, as the API does. The campaign the page was
 *  opened from goes with the address, so the login it leads to says which mail or ad
 *  brought the person back (REB-426), the same attribution the wizards send. */
export function Accedi() {
  const requestLink = useRequestLink()
  const searchStr = useLocation({ select: (location) => location.searchStr })
  const [email, setEmail] = useState('')
  const [sent, setSent] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function submit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    requestLink.mutate({ email: email.trim(), utm: resolveAttribution(searchStr) }, {
      onSuccess: () => setSent(true),
      onError: (failure) =>
        setError(
          failure instanceof ApiError
            ? failure.message
            : 'Non siamo riusciti a mandarti il link. Riprova.',
        ),
    })
  }

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
