import { useMutation, useQueryClient } from '@tanstack/react-query'
import { CircleAlert, ShieldCheck } from 'lucide-react'
import { useId, useState, type ReactNode } from 'react'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { Label } from '@rebase/ui/label'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@rebase/ui/sheet'
import { Textarea } from '@rebase/ui/textarea'
import { admin, type WorkProfileAdmin } from '@/lib/api'
import { failureMessage } from '@/lib/adminList'
import { VETTED_LABEL, formatDate } from '@/lib/format'
import { keys } from '@/lib/query-keys'
import { GUARD_RULES, guardField, isDecidable, profileStandingWord } from './profileState'

/**
 * «Approva» and «Rifiuta» as one piece of state shared by the rail card and the phone's sheet:
 * one note, optional for the first (it stays with the admins), required for the second (the
 * person reads it), and the override of the anonymity guard that approving over a hit needs.
 */
function useDecision(profile: WorkProfileAdmin, freelancerId: string, onDecided?: () => void) {
  const client = useQueryClient()
  const [note, setNote] = useState('')
  const [override, setOverride] = useState(false)
  const hits = profile.guard_hits.length > 0
  const onDone = (read: WorkProfileAdmin) => {
    client.setQueryData(keys.workProfile(freelancerId), read)
    void client.invalidateQueries({ queryKey: keys.freelancer(freelancerId) })
    void client.invalidateQueries({ queryKey: keys.audit('freelancers', freelancerId) })
    void client.invalidateQueries({ queryKey: keys.talents() })
    onDecided?.()
  }
  const approve = useMutation({
    mutationFn: () => admin.approveWorkProfile(freelancerId, note.trim() || null, profile.submitted_at, override),
    onSuccess: onDone,
  })
  const reject = useMutation({
    mutationFn: () => admin.rejectWorkProfile(freelancerId, note.trim(), profile.submitted_at),
    onSuccess: onDone,
  })
  return {
    note,
    setNote,
    override,
    setOverride,
    hits,
    approve,
    reject,
    failure: failureMessage(approve.error ?? reject.error, 'Non riesco a salvare la decisione.'),
    busy: approve.isPending || reject.isPending,
  }
}

type Decision = ReturnType<typeof useDecision>

/** What the anonymity guards found in the three fields a company reads: nothing, or each hit
 *  with the field and the rule (never the text). */
export function AnonymityResult({ profile }: { profile: Pick<WorkProfileAdmin, 'guard_hits'> }) {
  if (profile.guard_hits.length === 0) {
    return (
      <span className="inline-flex items-center gap-1.5">
        <ShieldCheck className="size-4" aria-hidden="true" />
        Nessun controllo scattato
      </span>
    )
  }
  return (
    <ul className="space-y-1">
      {profile.guard_hits.map((hit) => (
        <li key={`${hit.field}-${hit.rule}`} className="flex items-start gap-1.5 text-destructive">
          <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <span>
            Controllo di anonimato scattato su {guardField(hit.field)}: {GUARD_RULES[hit.rule] ?? hit.rule}
          </span>
        </li>
      ))}
    </ul>
  )
}

/** The checks an admin weighs before deciding: how much of the prose the data backs, the
 *  anonymity guards' result, the tool's version and when the profile was sent and expires. */
function Checks({ profile }: { profile: WorkProfileAdmin }) {
  const unsupported = profile.unsupported_anchors.length
  return (
    <dl className="space-y-2 text-sm">
      <div>
        <dt className="text-xs text-muted-foreground">Testo verificabile</dt>
        <dd>
          <span className="font-medium tabular-nums">{profile.groundedness}%</span>{' '}
          <span className="text-muted-foreground">
            ({unsupported === 0 ? 'nessuna affermazione senza dati' : `${unsupported} da verificare`})
          </span>
        </dd>
      </div>
      <div>
        <dt className="text-xs text-muted-foreground">Controllo di anonimato</dt>
        <dd>
          <AnonymityResult profile={profile} />
        </dd>
      </div>
      <div>
        <dt className="text-xs text-muted-foreground">Versione dello strumento</dt>
        <dd>{profile.tool_version ?? '—'}</dd>
      </div>
      <div>
        <dt className="text-xs text-muted-foreground">Inviato</dt>
        <dd className="tabular-nums">{formatDate(profile.submitted_at)}</dd>
      </div>
      <div>
        <dt className="text-xs text-muted-foreground">{profile.expired ? 'Scaduto il' : 'Scade il'}</dt>
        <dd className="tabular-nums">{formatDate(profile.expires_at)}</dd>
      </div>
    </dl>
  )
}

/** The note, the guard override when a check fired, the API's refusal and the two buttons. */
function DecisionForm({ decision, vettedAt }: { decision: Decision; vettedAt: string | null }) {
  const noteId = useId()
  const { note, hits, override, approve, reject, busy } = decision
  return (
    <div className="space-y-2">
      <Label htmlFor={noteId}>Nota</Label>
      <Textarea
        id={noteId}
        value={note}
        maxLength={2000}
        onChange={(event) => decision.setNote(event.target.value)}
        aria-describedby={`${noteId}-help`}
      />
      <p id={`${noteId}-help`} className="text-xs text-muted-foreground">
        Per rifiutare serve una nota, e la persona la legge nella sua area. Per approvare è facoltativa e la leggono solo
        gli admin.
        {vettedAt === null && ` Approvare segna anche la persona come «${VETTED_LABEL}».`}
      </p>
      {hits && (
        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            className="mt-1"
            checked={override}
            onChange={(event) => decision.setOverride(event.target.checked)}
          />
          <span>
            Un controllo di anonimato è scattato su ciò che leggono le aziende: non si può approvare. Per approvare
            comunque spunta qui e scrivi nella nota perché va bene: la nota finisce nel registro.
          </span>
        </label>
      )}
      {decision.failure && (
        <p role="alert" className="text-sm text-destructive">
          {decision.failure}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="outline" disabled={busy || note.trim() === ''} onClick={() => reject.mutate()}>
          {reject.isPending ? 'Rifiuto…' : 'Rifiuta'}
        </Button>
        <Button
          type="button"
          disabled={busy || (hits && (!override || note.trim() === ''))}
          onClick={() => approve.mutate()}
        >
          {approve.isPending ? 'Approvo…' : 'Approva'}
        </Button>
      </div>
    </div>
  )
}

function RailCard({ title, aside, children }: { title: string; aside?: ReactNode; children: ReactNode }) {
  return (
    <section aria-label={title} className="space-y-3 border p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-medium">{title}</h2>
        {aside}
      </div>
      {children}
    </section>
  )
}

/** A profile already decided or past its 12 months: a compact status, no form. */
function ProfileStatus({ profile }: { profile: WorkProfileAdmin }) {
  return (
    <RailCard title="Profilo di lavoro" aside={<Badge variant="pill">{profileStandingWord(profile)}</Badge>}>
      <dl className="space-y-2 text-sm">
        {profile.decided_at && (
          <div>
            <dt className="text-xs text-muted-foreground">Deciso</dt>
            <dd>
              {formatDate(profile.decided_at)}
              {profile.decided_by_name ? ` da ${profile.decided_by_name}` : ''}
            </dd>
          </div>
        )}
        {profile.decision_note && (
          <div>
            <dt className="text-xs text-muted-foreground">Nota</dt>
            <dd className="break-words whitespace-pre-line">{profile.decision_note}</dd>
          </div>
        )}
        <div>
          <dt className="text-xs text-muted-foreground">{profile.expired ? 'Scaduto il' : 'Scade il'}</dt>
          <dd className="tabular-nums">{formatDate(profile.expires_at)}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Inviato</dt>
          <dd className="tabular-nums">{formatDate(profile.submitted_at)}</dd>
        </div>
      </dl>
    </RailCard>
  )
}

/**
 * The profile's card in the page's right rail (REB-1158): for a draft an admin can still
 * decide, the checks and «Rifiuta» / «Approva» (note required to reject, the guard override
 * to approve over a hit); for any other profile its status. Nothing without a profile.
 */
export function ProfileRailCard({
  profile,
  freelancerId,
  vettedAt,
}: {
  profile: WorkProfileAdmin
  freelancerId: string
  vettedAt: string | null
}) {
  const decision = useDecision(profile, freelancerId)
  if (!isDecidable(profile)) return <ProfileStatus profile={profile} />
  return (
    <RailCard
      title="Decisione sul profilo"
      aside={
        <Badge variant="pill" className="gap-1">
          <CircleAlert aria-hidden="true" />
          {profileStandingWord(profile)}
        </Badge>
      }
    >
      <Checks profile={profile} />
      <div className="border-t pt-3">
        <DecisionForm decision={decision} vettedAt={vettedAt} />
      </div>
    </RailCard>
  )
}

/**
 * On a phone the rail is gone: the decision is a bar at the foot of the page, «Rifiuta» and
 * «Approva…», and either opens a sheet with the checks, the note and the final buttons.
 */
export function ProfileDecisionBar({
  profile,
  freelancerId,
  vettedAt,
}: {
  profile: WorkProfileAdmin
  freelancerId: string
  vettedAt: string | null
}) {
  const [open, setOpen] = useState(false)
  const decision = useDecision(profile, freelancerId, () => setOpen(false))
  if (!isDecidable(profile)) return null
  return (
    <>
      <div className="sticky bottom-0 z-10 flex gap-2 border-t bg-background p-3">
        <Button type="button" variant="outline" className="flex-1" onClick={() => setOpen(true)}>
          Rifiuta
        </Button>
        <Button type="button" className="flex-1" onClick={() => setOpen(true)}>
          Approva…
        </Button>
      </div>
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent side="bottom" className="gap-0 p-0 data-[side=bottom]:max-h-[90dvh]">
          <SheetHeader className="border-b p-4 pr-14">
            <SheetTitle>Decisione sul profilo</SheetTitle>
            <SheetDescription>Controlla, scrivi la nota e decidi.</SheetDescription>
          </SheetHeader>
          <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
            <Checks profile={profile} />
            <div className="border-t pt-3">
              <DecisionForm decision={decision} vettedAt={vettedAt} />
            </div>
          </div>
        </SheetContent>
      </Sheet>
    </>
  )
}
