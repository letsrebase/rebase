import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Button } from '@rebase/ui/button'
import { Label } from '@rebase/ui/label'
import { ApiError, member, type Me } from '@/lib/api'
import { formatDate } from '@/lib/format'
import { ME_KEY } from '@/lib/me'

type Scope = 'public' | 'private'

/** What `/me?github=<reason>` says about the link the person just tried. Unknown reasons say nothing. */
const NOTICES: Record<string, string> = {
  linked: 'Account GitHub collegato.',
  taken: 'Questo account GitHub è già collegato a un’altra scheda.',
  failed: 'GitHub non ha risposto: riprova tra poco.',
  denied: 'Hai annullato su GitHub: non abbiamo collegato niente.',
  expired: 'Il collegamento è scaduto: riprova.',
  off: 'Il collegamento con GitHub non è attivo.',
  limited: 'Troppi tentativi: riprova tra qualche minuto.',
  no_card: 'Per collegare GitHub serve una scheda da freelance.',
}

function ScopeChoice({ label }: { label: string }) {
  const [scope, setScope] = useState<Scope>('public')
  return (
    <div className="space-y-3">
      <div role="radiogroup" aria-label="Che cosa condividere" className="space-y-2">
        {(
          [
            ['public', 'Solo l’attività pubblica'],
            ['private', 'Anche i conteggi delle attività private: GitHub ti chiede di autorizzare la lettura del tuo profilo'],
          ] as const
        ).map(([value, text]) => (
          <div key={value} className="flex items-start gap-2">
            <input
              type="radio"
              id={`github-scope-${value}`}
              name="github-scope"
              value={value}
              checked={scope === value}
              onChange={() => setScope(value)}
              className="mt-0.5 size-4"
            />
            <Label htmlFor={`github-scope-${value}`} className="text-sm font-normal">
              {text}
            </Label>
          </div>
        ))}
      </div>
      <Button type="button" size="sm" onClick={() => window.location.assign(member.githubLinkUrl(scope))}>
        {label}
      </Button>
    </div>
  )
}

function Unlink() {
  const client = useQueryClient()
  const [asking, setAsking] = useState(false)
  const unlink = useMutation({
    mutationFn: () => member.unlinkGithub(),
    onSuccess: (me) => client.setQueryData(ME_KEY, me),
  })
  if (!asking) {
    return (
      <Button type="button" variant="outline" size="sm" onClick={() => setAsking(true)}>
        Scollega
      </Button>
    )
  }
  return (
    <div className="space-y-2">
      <p className="text-sm">
        Scolleghiamo l’account e cancelliamo subito quello che abbiamo letto. L’autorizzazione che hai dato a rebase
        su GitHub la togli dalle impostazioni di GitHub, alla voce Applications.
      </p>
      <div className="flex flex-wrap gap-2">
        <Button type="button" size="sm" disabled={unlink.isPending} onClick={() => unlink.mutate()}>
          {unlink.isPending ? 'Scollego…' : 'Sì, scollega'}
        </Button>
        <Button type="button" variant="outline" size="sm" disabled={unlink.isPending} onClick={() => setAsking(false)}>
          Annulla
        </Button>
      </div>
      {unlink.isError && (
        <p role="alert" className="text-sm text-destructive">
          {unlink.error instanceof ApiError ? unlink.error.message : 'Non siamo riusciti a scollegare. Riprova.'}
        </p>
      )}
    </div>
  )
}

function Linked({ github }: { github: NonNullable<Me['github']> }) {
  const [relinking, setRelinking] = useState(false)
  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <p className="text-sm">
          Collegato come <span className="font-medium">{github.login}</span> il {formatDate(github.linked_at)}
        </p>
        <p className="text-sm text-muted-foreground">
          {github.scope === 'private'
            ? 'Condividi anche i conteggi delle attività private.'
            : 'Condividi solo l’attività pubblica.'}
        </p>
      </div>
      {relinking && <ScopeChoice label="Collega GitHub" />}
      <div className="flex flex-wrap items-start gap-2">
        {!relinking && (
          <Button type="button" variant="outline" size="sm" onClick={() => setRelinking(true)}>
            Collega di nuovo
          </Button>
        )}
        <Unlink />
      </div>
    </div>
  )
}

/**
 * «GitHub» in the member area, below «Profilo di lavoro» (REB-1166): the link that lets the
 * people reading the work profile compare it with what GitHub recorded. `notice` is the
 * `github` query parameter the server comes back with.
 */
export function MemberGithub({ github, notice }: { github: Me['github']; notice: string | null }) {
  // `?github=linked` stays in the address after an unlink or a reload: it says nothing then.
  const message = notice && !(notice === 'linked' && !github) ? NOTICES[notice] : undefined
  return (
    <section aria-labelledby="me-github" className="space-y-3">
      <h2 id="me-github" className="text-lg font-semibold tracking-tight">
        GitHub
      </h2>
      {message &&
        (notice === 'linked' ? (
          <p role="status" className="text-sm">
            {message}
          </p>
        ) : (
          <p role="alert" className="text-sm text-destructive">
            {message}
          </p>
        ))}
      <div className="space-y-3 border bg-card p-4">
        {github ? (
          <Linked github={github} />
        ) : (
          <>
            <p className="text-sm text-muted-foreground">
              Se colleghi il tuo account GitHub, chi in rebase legge il tuo profilo di lavoro può confrontarlo con
              l’attività che GitHub ha registrato: commit, pull request, revisioni e issue per mese negli ultimi 24
              mesi, i linguaggi dei tuoi repository pubblici e le pull request che indichi nel profilo. Leggiamo solo
              numeri, mai il codice né i nomi dei repository privati. Li leggiamo una volta, quando colleghi
              l’account, e non teniamo l’accesso.
            </p>
            <ScopeChoice label="Collega GitHub" />
          </>
        )}
      </div>
    </section>
  )
}
