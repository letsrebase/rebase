import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { ApiError, member, type Me } from '@/lib/api'
import { formatDate } from '@/lib/format'
import { ME_KEY } from '@/lib/me'

type Profile = NonNullable<Me['work_profile']>

const README_URL = 'https://github.com/letsrebase/work-profile#readme'
const PRIVACY_URL = 'https://letsrebase.com/privacy'

/** The three commands a person runs, in order: the first two once, the third in the agent. */
const COMMANDS = ['git clone https://github.com/letsrebase/work-profile', 'cd work-profile', 'claude'] as const

function Command({ children }: { children: string }) {
  // A short command stays whole; a long one (the clone URL) breaks where the line ends.
  const wrap = children.length > 24 ? 'break-all' : 'whitespace-nowrap'
  return <code className={`${wrap} border bg-muted px-1.5 py-0.5 font-mono text-sm`}>{children}</code>
}

/** The commands as the steps of a list, ruled like the rows of «La tua scheda». */
function Steps() {
  return (
    <ol className="divide-y border bg-card text-sm">
      {COMMANDS.map((command, index) => (
        <li key={command} className="flex items-baseline gap-3 px-4 py-2.5">
          <span className="w-4 shrink-0 text-muted-foreground tabular-nums">{index + 1}</span>
          <span className="min-w-0">
            <Command>{command}</Command>
            {command === 'claude' && (
              <>
                {' '}
                e poi scrivi <Command>/work-profile</Command>
              </>
            )}
          </span>
        </li>
      ))}
    </ol>
  )
}

function Links() {
  return (
    <p className="text-sm">
      <a className="underline underline-offset-2" href={README_URL} target="_blank" rel="noopener noreferrer">
        Leggi il README del comando
      </a>
      {' · '}
      <a className="underline underline-offset-2" href={PRIVACY_URL} target="_blank" rel="noopener noreferrer">
        Informativa sulla privacy
      </a>
    </p>
  )
}

/** Before any submission: what the tool is, what it reads, what stays and what leaves, who
 *  reads it and what approval means, then the commands (spec 2026-11-17 § 7). */
function Explanation() {
  return (
    <div className="space-y-3 text-sm">
      <p>
        Un CV dice cosa hai fatto, non come lavori con gli agenti di coding, che è quello che le aziende ci chiedono
        più spesso. Il comando <Command>work-profile</Command>, un fork di <Command>apply-new</Command> di Play New
        (licenza MIT), lo legge dai log delle tue sessioni, sul tuo computer, e ne scrive un profilo di come lavori.
      </p>
      <p>
        I log, il codice e i nomi dei tuoi progetti e dei tuoi repository restano sul tuo computer. A rebase arriva solo
        il profilo che vedi nell&apos;anteprima, e solo dopo il tuo ok, con un accesso che approvi dal browser.
      </p>
      <p>
        Una persona di rebase lo legge prima che conti qualcosa. Se lo approva, diventi «Verificato da rebase» e le
        aziende con cui lavoriamo possono vedere il riassunto, gli ambiti di lavoro e i settori dei progetti
        rappresentativi del tuo profilo, insieme al tuo nome e alla tua scheda, per esempio nel talent cloud o quando
        ti proponiamo per un progetto. Lo teniamo al massimo 12 mesi e lo puoi eliminare quando vuoi.
      </p>
    </div>
  )
}

/** What the state is, in the person's own words. */
function Standing({ profile, vetted }: { profile: Profile; vetted: boolean }) {
  if (profile.expired) {
    return (
      <>
        <Badge variant="pill">Scaduto</Badge>
        <p className="text-sm">
          Il profilo che avevi inviato è scaduto il {formatDate(profile.expires_at)} e non conta più. Per averne uno
          nuovo, rilancia il comando.
        </p>
      </>
    )
  }
  if (profile.state === 'approved') {
    return (
      <>
        <Badge variant="pill" dot="accent">
          Approvato fino al {formatDate(profile.expires_at)}
        </Badge>
        <p className="text-sm">
          {vetted ? 'Sei «Verificato da rebase» e le' : 'Le'} aziende con cui lavoriamo possono vedere il riassunto,
          gli ambiti di lavoro e i settori dei progetti rappresentativi del tuo profilo, insieme al tuo nome e alla tua
          scheda, per esempio nel talent cloud o quando ti proponiamo per un progetto.
        </p>
      </>
    )
  }
  if (profile.state === 'rejected') {
    return (
      <>
        <Badge variant="pill">Non approvato</Badge>
        {profile.decision_note && (
          <blockquote className="whitespace-pre-line border-l-(length:--line-strong) border-accent pl-3 text-sm">
            {profile.decision_note}
          </blockquote>
        )}
        <p className="text-sm">
          Il profilo non compare da nessuna parte. Se vuoi, sistemalo e rilancia il comando: lo rileggiamo.
        </p>
      </>
    )
  }
  return (
    <>
      <Badge variant="pill">In revisione</Badge>
      <p className="text-sm">
        Una persona di rebase lo sta leggendo. Finché non lo approva non conta niente: nessuno fuori da rebase lo vede.
      </p>
    </>
  )
}

/** «Elimina il profilo», behind an inline second click in place of a browser `confirm()`. */
function Delete() {
  const client = useQueryClient()
  const [asking, setAsking] = useState(false)
  const remove = useMutation({
    mutationFn: () => member.deleteWorkProfile(),
    onSuccess: (me) => client.setQueryData(ME_KEY, me),
  })
  if (!asking) {
    return (
      <Button type="button" variant="outline" size="sm" onClick={() => setAsking(true)}>
        Elimina il profilo
      </Button>
    )
  }
  return (
    <div className="space-y-2">
      <p className="text-sm">
        Lo eliminiamo subito. La tua scheda anonima tiene quello che ha imparato dal profilo finché non viene
        riscritta. Il «Verificato da rebase» resta: lo toglie, se vuole, chi lo ha dato.
      </p>
      <div className="flex flex-wrap gap-2">
        <Button type="button" size="sm" disabled={remove.isPending} onClick={() => remove.mutate()}>
          {remove.isPending ? 'Elimino…' : 'Sì, elimina'}
        </Button>
        <Button type="button" variant="outline" size="sm" disabled={remove.isPending} onClick={() => setAsking(false)}>
          Annulla
        </Button>
      </div>
      {remove.isError && (
        <p role="alert" className="text-sm text-destructive">
          {remove.error instanceof ApiError ? remove.error.message : 'Non siamo riusciti a eliminarlo. Riprova.'}
        </p>
      )}
    </div>
  )
}

/**
 * «Profilo di lavoro» in the member area, below «La tua scheda» (REB-1127, spec 2026-11-17
 * § 7). Before any submission it explains the tool and gives the three commands, with the
 * README and the privacy notice; after, it says where the profile stands (`In revisione`,
 * `Approvato fino al <data>`, `Non approvato` with the admin's note, or expired), when it
 * was sent, and offers «Elimina il profilo», which does not touch «Verificato».
 */
export function MemberWorkProfile({ profile, vetted }: { profile: Me['work_profile']; vetted: boolean }) {
  return (
    <section aria-labelledby="me-work-profile" className="space-y-3">
      <h2 id="me-work-profile" className="text-lg font-semibold tracking-tight">
        Profilo di lavoro
      </h2>
      {profile === null || profile === undefined ? (
        <>
          <Explanation />
          <Steps />
          <Links />
        </>
      ) : (
        <div className="space-y-3 border bg-card p-4">
          <div className="space-y-2">
            <Standing profile={profile} vetted={vetted} />
          </div>
          <p className="text-sm text-muted-foreground">Inviato il {formatDate(profile.submitted_at)}.</p>
          {(profile.state === 'rejected' || profile.expired) && (
            <>
              <Steps />
              <Links />
            </>
          )}
          <Delete />
        </div>
      )}
    </section>
  )
}
