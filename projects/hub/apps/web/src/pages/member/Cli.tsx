import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { Link, useSearch } from '@tanstack/react-router'
import { Button } from '@rebase/ui/button'
import { Loader } from '@rebase/ui/loader'
import { ApiError, cli, type CliAuthorization } from '@/lib/api'
import { keys } from '@/lib/query-keys'
import { takeCliCode } from '@/lib/returnTo'

/** `/hub/cli` (REB-1133, spec 2026-11-17 § 2 and § 10): the page a freelancer lands on from
 *  the terminal, signed in, to say yes or no to one CLI. It shows the code to compare with the
 *  terminal's and what the CLI will be able to do («inviare il tuo profilo di lavoro,
 *  nient'altro»), and never a device name or an address: the CLI sends none. States: the
 *  code is pending, the person has no card (the reason, no button), the code is wrong or
 *  expired, authorized, cancelled. A signed-out visitor never gets here: `SignedInLayout`
 *  sends them to the login and `Verify` brings them back with the code. */
export function Cli() {
  const { code } = useSearch({ strict: false }) as { code?: string }
  const client = useQueryClient()
  // The page is open, so the code the login kept for it has done its job: forget it, or a magic
  // link asked for later would bring somebody back here with a spent code.
  useEffect(() => void takeCliCode(), [])
  const authorization = useQuery({
    queryKey: keys.cliAuthorization(code ?? ''),
    queryFn: () => cli.authorization(code ?? ''),
    enabled: Boolean(code),
    retry: false,
    staleTime: Infinity,
  })
  const decide = useMutation({
    mutationFn: (verdict: 'approve' | 'deny') =>
      verdict === 'approve' ? cli.approve(code ?? '') : cli.deny(code ?? ''),
    onSuccess: (next) => client.setQueryData(keys.cliAuthorization(code ?? ''), next),
  })

  if (!code) {
    return (
      <Frame title="Manca il codice">
        <p className="text-muted-foreground">
          Apri di nuovo il link che il terminale ti ha mostrato, o scrivi il codice in fondo
          all&apos;indirizzo: <span className="font-mono">/hub/cli?code=ABCD-EFGH</span>.
        </p>
      </Frame>
    )
  }
  if (authorization.isPending) {
    return (
      <div className="flex items-center justify-center gap-2 p-8 text-sm text-muted-foreground">
        <Loader className="size-8" />
        Un attimo…
      </div>
    )
  }
  if (authorization.isError) {
    return <Unknown error={authorization.error} />
  }

  const read: CliAuthorization = authorization.data
  if (read.state === 'approved' || read.state === 'spent') {
    return (
      <Frame title="Autorizzato">
        <p role="status">Torna al terminale: il comando riparte da solo e invia il tuo profilo di lavoro.</p>
        <p className="text-muted-foreground">Puoi chiudere questa pagina.</p>
      </Frame>
    )
  }
  if (read.state === 'denied') {
    return (
      <Frame title="Annullato">
        <p role="status">Non abbiamo ricevuto nulla. Il terminale ti dice che l&apos;accesso è stato annullato.</p>
        <p className="text-muted-foreground">Se cambi idea, rilancia il comando e apri il nuovo link.</p>
      </Frame>
    )
  }

  return (
    <Frame title="Autorizzi il terminale?">
      <p className="text-muted-foreground">
        Il codice che vedi qui deve essere lo stesso che vedi nel terminale.
      </p>
      <p
        aria-label="Codice"
        className="rounded-md border border-border bg-muted px-4 py-3 text-center font-mono text-3xl font-semibold tracking-widest tabular-nums"
      >
        {read.user_code}
      </p>
      <p>
        Con il tuo ok il comando potrà fare una cosa sola: inviare il tuo profilo di lavoro,
        nient&apos;altro. L&apos;accesso vale quindici minuti e un solo invio.
      </p>
      {read.can_authorize ? (
        <div className="flex flex-col gap-2 sm:flex-row-reverse">
          <Button type="button" disabled={decide.isPending} onClick={() => decide.mutate('approve')}>
            Autorizza
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={decide.isPending}
            onClick={() => decide.mutate('deny')}
          >
            Annulla
          </Button>
        </div>
      ) : (
        <div className="space-y-3">
          <p role="alert">{read.blocked_reason}</p>
          <Link to="/freelance" className="text-sm underline underline-offset-2">
            Candidati a rebase
          </Link>
        </div>
      )}
      {decide.isError && (
        <p role="alert" className="text-sm text-destructive">
          {decide.error instanceof ApiError ? decide.error.message : 'Non ci siamo riusciti. Riprova.'}
        </p>
      )}
    </Frame>
  )
}

/** A code that names nothing live: wrong, expired or already used, or too many typed. */
function Unknown({ error }: { error: unknown }) {
  const tooMany = error instanceof ApiError && error.status === 429
  const notFound = error instanceof ApiError && error.status === 404
  return (
    <Frame title={tooMany ? 'Troppi tentativi' : 'Questo codice non vale'}>
      <p role="alert">
        {error instanceof ApiError && (tooMany || notFound)
          ? error.message
          : 'Non siamo riusciti a controllare il codice. Riprova tra un minuto.'}
      </p>
      <p className="text-muted-foreground">
        Il codice dura dieci minuti. Rilancia il comando nel terminale per averne uno nuovo.
      </p>
    </Frame>
  )
}

function Frame({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mx-auto w-full max-w-md space-y-4 p-6">
      <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
      {children}
    </div>
  )
}
