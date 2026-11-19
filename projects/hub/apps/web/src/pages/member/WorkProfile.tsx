import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { PracticeRows } from '@/components/Practices'
import { UsageBadges } from '@/components/TechUsage'
import { ApiError, member, type Me } from '@/lib/api'
import { formatDate } from '@/lib/format'
import { ME_KEY } from '@/lib/me'

type Profile = NonNullable<Me['work_profile']>

const README_URL = 'https://github.com/letsrebase/rebase-verify#readme'
const PRIVACY_URL = 'https://letsrebase.com/privacy'
const PRACTICES_URL = 'https://github.com/letsrebase/rebase-verify/blob/main/README.md#work-practices'

/** The one command a person runs, in a terminal: the tool asks the rest. */
const COMMAND = 'npx @letsrebase/verify'

function Command({ children }: { children: string }) {
  return <code className="whitespace-nowrap border bg-muted px-1.5 py-0.5 font-mono text-sm">{children}</code>
}

/** The command and what it needs, ruled like the rows of «La tua scheda». */
function Steps() {
  return (
    <div className="divide-y border bg-card text-sm">
      <p className="px-4 py-2.5">
        In un terminale scrivi <Command>{COMMAND}</Command>
      </p>
      <p className="px-4 py-2.5 text-muted-foreground">
        Ti servono Node 20.12 o più recente e un agente di coding con cui hai già fatto l&apos;accesso, per esempio
        oh-my-pi, Claude Code o Codex. Il comando ti chiede quale usare e con quale modello, e ti dice cosa invia prima
        di farlo.
      </p>
    </div>
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
        più spesso. Il comando <Command>rebase-verify</Command>, un fork di <Command>apply-new</Command> di Play New
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
          {profile.technologies.length > 0 && (
            <div className="space-y-2 border-t pt-3">
              <h3 className="text-sm font-medium">Le tecnologie nel tuo lavoro</h3>
              <p className="text-sm text-muted-foreground">
                Le leggiamo noi di rebase. Le aziende non le vedono.
              </p>
              <UsageBadges technologies={profile.technologies} />
            </div>
          )}
          {profile.practices.length > 0 && (
            <div className="space-y-2 border-t pt-3">
              <h3 id="member-practices" className="text-sm font-medium">
                Come lavora
              </h3>
              <p className="text-sm text-muted-foreground">
                Per sapere come le contiamo, apri{' '}
                <a
                  className="underline underline-offset-2"
                  href={PRACTICES_URL}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  l&apos;elenco delle pratiche
                </a>
                .
              </p>
              <PracticeRows labelledBy="member-practices" practices={profile.practices} />
            </div>
          )}
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
