import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from '@tanstack/react-router'
import { ArrowLeft } from 'lucide-react'
import { useId, useState, type FormEvent } from 'react'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import { Textarea } from '@rebase/ui/textarea'
import {
  admin,
  ApiError,
  type TeamMember,
  type TeamProposal,
  type TeamRequest,
  type TeamRequestStatus,
  type TeamRequestTalent,
} from '@/lib/api'
import { bandLabel } from '@/lib/bands'
import {
  TALENT_ANSWER_LABELS,
  TALENT_WAITING_LABEL,
  TEAM_ORIGIN_LABELS,
  TEAM_REQUEST_STATE_LABELS,
  formatDate,
  formatDateTime,
  formatDaysPerWeek,
} from '@/lib/format'
import { formatMoney } from '@rebase/ui/format'
import { keys } from '@/lib/query-keys'
import { Empty, Header, Row } from './list-kit'

/** Core's `SUMMARY_MAX_LENGTH` and `PROJECT_MAX_LENGTH` (the note's ceiling). */
const SummaryMax = 1500
const NOTE_MAX = 4000

/** The API's own sentence whenever it gave one (a summary that names the company, a
 *  request gone); ours only when nothing answered at all. */
function sentence(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback
}

/** What the engine read about place in the company's description: whether the work is
 *  on site, and the place it named, if any. */
function placeLabel(location: TeamProposal['location']): string {
  if (location.on_site) return location.place ? `In sede, a ${location.place}` : 'In sede, luogo non indicato'
  return location.place ? `Da remoto · ${location.place}` : 'Da remoto'
}

/** The team's bands per day and per month, as the company read them; «Tariffa da
 *  definire» when a member has no rate. Never asked of an empty team, which has no price
 *  at all rather than one to define. */
function teamBands(economics: TeamProposal['economics']): string {
  if (!economics.day || !economics.month) return 'Tariffa da definire'
  return `${bandLabel(economics.day)} · ${bandLabel(economics.month, 'month')}`
}

/** The answer to the availability mail and when it came (D1); «In attesa» for a talent
 *  who has the mail and has not answered, the empty cell's dash for one who was never
 *  mailed. */
function answer(talent: TeamRequestTalent): string {
  if (talent.answer === null) return talent.mail_sent_at ? TALENT_WAITING_LABEL : '—'
  const label = TALENT_ANSWER_LABELS[talent.answer] ?? talent.answer
  return talent.answered_at ? `${label} · ${formatDateTime(talent.answered_at)}` : label
}

type Move = { status: TeamRequestStatus; action: 'contact' | 'close' | 'reopen' }

const MOVE_LABELS: Record<Move['action'], [idle: string, pending: string]> = {
  contact: ['Segna come contattata', 'Segno…'],
  close: ['Chiudi', 'Chiudo…'],
  reopen: ['Riapri', 'Riapro…'],
}

/** The moves a request in this state offers: a new one can be contacted or closed, a
 *  contacted one closed, and a closed one reopened where it was, after a mis-click. */
function moves(request: TeamRequest): Move[] {
  if (request.status === 'new') {
    return [
      { status: 'contacted', action: 'contact' },
      { status: 'closed', action: 'close' },
    ]
  }
  if (request.status === 'contacted') return [{ status: 'closed', action: 'close' }]
  return [{ status: request.contacted_at ? 'contacted' : 'new', action: 'reopen' }]
}

/**
 * A team request, as the admin works it (REB-514, spec § 3.5): what the company typed,
 * the summary the talents will read (editable here, since it is what D1's mail sends),
 * the place the engine read, the team by name with each talent's own rate and the band
 * the company saw, the contacts, the state and the admin's note. Nothing is anonymised:
 * this is the admin's page. «Contatta i talenti» (D1) mails the team from here, and the
 * answers fill the table's last column.
 */
export function AdminTeamRequest() {
  const { id } = useParams({ from: '/signedIn/admin/team/$id' })
  const row = useQuery({ queryKey: keys.teamRequest(id), queryFn: () => admin.teamRequest(id) })
  if (row.isError) {
    return (
      <Empty>
        {row.error instanceof ApiError && row.error.status === 404
          ? 'Richiesta non trovata.'
          : 'Non riesco a leggere la richiesta.'}
      </Empty>
    )
  }
  if (row.isPending) return <Empty>Caricamento…</Empty>
  return <RequestPage request={row.data} />
}

function RequestPage({ request }: { request: TeamRequest }) {
  const client = useQueryClient()
  const key = ['team-request', request.id] as const
  function apply(updated: TeamRequest) {
    client.setQueryData<TeamRequest>(key, updated)
    void client.invalidateQueries({ queryKey: keys.teamRequests() })
  }
  const members = new Map<string, TeamMember>(
    (request.proposal?.team ?? []).flatMap((member) =>
      member.freelancer_id ? [[member.freelancer_id, member] as const] : [],
    ),
  )
  const teamId = useId()
  const contactsId = useId()
  return (
    <>
      <Header title={request.company} />
      <div className="grid gap-6 p-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          <dl className="space-y-3 text-sm">
            <Row label="Origine">{TEAM_ORIGIN_LABELS[request.origin] ?? request.origin}</Row>
            <Row label="Arrivata">{formatDateTime(request.created_at)}</Row>
            <Row label="Descrizione">
              {request.description ? <p className="whitespace-pre-wrap">{request.description}</p> : '—'}
            </Row>
            <Row label="Luogo">{request.proposal ? placeLabel(request.proposal.location) : '—'}</Row>
            {request.proposal && request.proposal.team.length > 0 && (
              <Row label="Fascia del team">{teamBands(request.proposal.economics)}</Row>
            )}
          </dl>
          <SummaryEditor request={request} onSaved={apply} />
        </div>
        <div className="space-y-6">
          <StateBox request={request} onMoved={apply} />
          <NoteEditor request={request} onSaved={apply} />
        </div>
      </div>
      <section aria-labelledby={teamId} className="space-y-3 px-6 pb-6">
        <h2 id={teamId} className="text-sm font-medium">
          Il team
        </h2>
        <ContactTalents request={request} onContacted={apply} />
        {request.talents.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nessun talento in questa richiesta.</p>
        ) : (
          <div className="overflow-x-auto overflow-y-hidden border border-border bg-card">
            <Table scrollLabel="Talenti del team">
              <TableHeader>
                <TableRow>
                  <TableHead>Talento</TableHead>
                  <TableHead>Ruolo</TableHead>
                  <TableHead>Tariffa</TableHead>
                  <TableHead>Fascia cliente</TableHead>
                  <TableHead>Risposta</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {request.talents.map((talent) => (
                  <TalentRow key={talent.freelancer_id} talent={talent} member={members.get(talent.freelancer_id)} />
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </section>
      <section aria-labelledby={contactsId} className="space-y-3 px-6 pb-6">
        <h2 id={contactsId} className="text-sm font-medium">
          Contatti
        </h2>
        <dl className="space-y-3 text-sm">
          <Row label="Azienda">{request.company}</Row>
          <Row label="Email">
            <a className="underline underline-offset-2" href={`mailto:${request.email}`}>
              {request.email}
            </a>
          </Row>
          <Row label="Telefono">
            {request.phone ? (
              <a className="underline underline-offset-2" href={`tel:${request.phone.replace(/[^\d+]/g, '')}`}>
                {request.phone}
              </a>
            ) : (
              '—'
            )}
          </Row>
        </dl>
      </section>
      <p className="px-6 pb-6">
        <Link to="/admin/team" className="inline-flex items-center gap-1 text-sm underline-offset-2 hover:underline">
          <ArrowLeft className="size-4" /> Tutte le richieste
        </Link>
      </p>
    </>
  )
}

function TalentRow({ talent, member }: { talent: TeamRequestTalent; member: TeamMember | undefined }) {
  return (
    <TableRow>
      <TableCell>
        <Link to="/admin/freelance/$id" params={{ id: talent.freelancer_id }} className="font-medium hover:underline">
          {`${talent.first_name} ${talent.last_name}`}
        </Link>
      </TableCell>
      <TableCell className="min-w-64 space-y-1 whitespace-normal">
        <p>{talent.role}</p>
        {member && <p className="text-xs text-muted-foreground">{member.reasoning}</p>}
        {member?.week_days ? (
          <p className="text-xs text-muted-foreground">{formatDaysPerWeek(member.week_days)}</p>
        ) : null}
      </TableCell>
      <TableCell>{talent.daily_rate === null ? '—' : formatMoney(talent.daily_rate)}</TableCell>
      <TableCell>{talent.band ? bandLabel(talent.band) : '—'}</TableCell>
      <TableCell>{answer(talent)}</TableCell>
    </TableRow>
  )
}

/** «Contatta i talenti» while nobody has the availability mail (D1, spec § 3.5, § 3.6),
 *  then «Rimanda a chi non ha risposto», which writes only to the silent, so a talent
 *  who answered is never mailed again, with a fresh link that retires the one they
 *  had. Nothing once every talent who can be written to answered (a card deleted or
 *  turned down cannot), or on a closed request. The mails leave after the API's
 *  answer, which already shows every talent contacted; a refusal is the API's sentence
 *  as it stands, the summary that names the company among them. */
function ContactTalents({ request, onContacted }: { request: TeamRequest; onContacted: (updated: TeamRequest) => void }) {
  const hintId = useId()
  const contact = useMutation({
    mutationFn: (onlySilent: boolean) => admin.contactTeamTalents(request.id, onlySilent),
    onSuccess: onContacted,
  })
  const mailed = request.talents.some((talent) => talent.mail_sent_at !== null)
  const silent = request.talents.some((talent) => talent.answer === null && talent.contactable)
  if (request.status === 'closed' || !silent) return null
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">
        Ogni talento riceve il riassunto, il ruolo proposto, la sua tariffa e due bottoni per rispondere.
      </p>
      {contact.isError && (
        <p role="alert" className="text-sm text-destructive">
          {sentence(contact.error, 'Non riesco a scrivere ai talenti.')}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <Button
          type="button"
          size="sm"
          disabled={contact.isPending}
          aria-describedby={mailed ? hintId : undefined}
          onClick={() => contact.mutate(mailed)}
        >
          {contact.isPending ? 'Invio…' : mailed ? 'Rimanda a chi non ha risposto' : 'Contatta i talenti'}
        </Button>
        {contact.isSuccess && (
          <span role="status" className="text-sm text-muted-foreground">
            Mail in partenza: le risposte arrivano in questa tabella.
          </span>
        )}
      </div>
      {mailed && (
        <p id={hintId} className="text-sm text-muted-foreground">
          I link delle mail precedenti smettono di valere.
        </p>
      )}
    </div>
  )
}

/** «Salva il riassunto»: the proposal's summary, which D1's mail sends the talents. A
 *  request of one talent from the cloud has no proposal, so nothing to edit. */
function SummaryEditor({ request, onSaved }: { request: TeamRequest; onSaved: (updated: TeamRequest) => void }) {
  const headingId = useId()
  const hintId = useId()
  const [draft, setDraft] = useState(request.summary ?? '')
  const [problem, setProblem] = useState<string | null>(null)
  const save = useMutation({
    mutationFn: (summary: string) => admin.setTeamRequestSummary(request.id, summary),
    onSuccess: (updated) => {
      onSaved(updated)
      setDraft(updated.summary ?? '')
    },
  })
  function submit(event: FormEvent) {
    event.preventDefault()
    const summary = draft.trim()
    if (!summary) {
      setProblem('Il riassunto non può essere vuoto.')
      return
    }
    save.mutate(summary)
  }
  const failure = problem ?? (save.isError ? sentence(save.error, 'Non riesco a salvare il riassunto.') : null)
  return (
    <section aria-labelledby={headingId} className="space-y-3">
      <h2 id={headingId} className="text-sm font-medium">
        Riassunto
      </h2>
      {request.summary === null ? (
        <p className="text-sm text-muted-foreground">Una richiesta per un talento solo: non c’è un riassunto.</p>
      ) : (
        <form onSubmit={submit} className="space-y-3">
          <p id={hintId} className="text-sm text-muted-foreground">
            È il testo che leggeranno i talenti: non deve nominare l’azienda.
          </p>
          <Textarea
            aria-label="Riassunto"
            aria-describedby={hintId}
            value={draft}
            maxLength={SummaryMax}
            rows={4}
            onChange={(event) => {
              setDraft(event.target.value)
              setProblem(null)
              if (!save.isPending) save.reset()
            }}
          />
          {failure && (
            <p role="alert" className="text-sm text-destructive">
              {failure}
            </p>
          )}
          <div className="flex flex-wrap items-center gap-3">
            <Button type="submit" size="sm" disabled={save.isPending}>
              {save.isPending ? 'Salvo…' : 'Salva il riassunto'}
            </Button>
            {save.isSuccess && (
              <span role="status" className="text-sm text-muted-foreground">
                Riassunto salvato.
              </span>
            )}
          </div>
        </form>
      )}
    </section>
  )
}

/** Where the request stands, when it was contacted and closed, and the moves from here. */
function StateBox({ request, onMoved }: { request: TeamRequest; onMoved: (updated: TeamRequest) => void }) {
  const headingId = useId()
  const move = useMutation({
    mutationFn: ({ status }: Move) => admin.setTeamRequestStatus(request.id, status),
    onSuccess: onMoved,
  })
  return (
    <section aria-labelledby={headingId} className="space-y-3 border p-4">
      <h2 id={headingId} className="text-sm font-medium">
        Stato
      </h2>
      <Badge variant="pill">{TEAM_REQUEST_STATE_LABELS[request.status] ?? request.status}</Badge>
      {(request.contacted_at || request.closed_at) && (
        <div className="space-y-1 text-sm text-muted-foreground">
          {request.contacted_at && <p>{`Contattata il ${formatDate(request.contacted_at)}`}</p>}
          {request.closed_at && <p>{`Chiusa il ${formatDate(request.closed_at)}`}</p>}
        </div>
      )}
      {move.isError && (
        <p role="alert" className="text-sm text-destructive">
          {sentence(move.error, 'Non riesco a cambiare lo stato.')}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        {moves(request).map((next) => {
          const [idle, pending] = MOVE_LABELS[next.action]
          return (
            <Button
              key={next.action}
              type="button"
              size="sm"
              variant={next.action === 'close' ? 'outline' : 'default'}
              disabled={move.isPending}
              onClick={() => move.mutate(next)}
            >
              {move.isPending && move.variables?.action === next.action ? pending : idle}
            </Button>
          )
        })}
      </div>
    </section>
  )
}

/** The admin's own note; an empty box clears it. */
function NoteEditor({ request, onSaved }: { request: TeamRequest; onSaved: (updated: TeamRequest) => void }) {
  const headingId = useId()
  const [draft, setDraft] = useState(request.note ?? '')
  const save = useMutation({
    mutationFn: (note: string | null) => admin.setTeamRequestNote(request.id, note),
    onSuccess: (updated) => {
      onSaved(updated)
      setDraft(updated.note ?? '')
    },
  })
  function submit(event: FormEvent) {
    event.preventDefault()
    save.mutate(draft.trim() || null)
  }
  return (
    <section aria-labelledby={headingId} className="space-y-3 border p-4">
      <h2 id={headingId} className="text-sm font-medium">
        Nota
      </h2>
      <form onSubmit={submit} className="space-y-3">
        <Textarea
          aria-label="Nota"
          value={draft}
          maxLength={NOTE_MAX}
          rows={3}
          placeholder="Una nota per chi rileggerà questa richiesta"
          onChange={(event) => {
            setDraft(event.target.value)
            if (!save.isPending) save.reset()
          }}
        />
        {save.isError && (
          <p role="alert" className="text-sm text-destructive">
            {sentence(save.error, 'Non riesco a salvare la nota.')}
          </p>
        )}
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" size="sm" disabled={save.isPending}>
            {save.isPending ? 'Salvo…' : 'Salva la nota'}
          </Button>
          {save.isSuccess && (
            <span role="status" className="text-sm text-muted-foreground">
              Nota salvata.
            </span>
          )}
        </div>
      </form>
    </section>
  )
}
