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
  type AdminTeamMember,
  type AdminTeamProposal,
  type TeamRequest,
  type TeamRequestStato,
  type TeamRequestTalent,
} from '@/lib/api'
import { bandLabel } from '@/lib/bands'
import {
  TALENT_ANSWER_LABELS,
  TEAM_ORIGIN_LABELS,
  TEAM_REQUEST_STATE_LABELS,
  formatDate,
  formatDateTime,
  formatEuro,
} from '@/lib/format'
import { Empty, Header, Row } from './lists'

/** Core's `RIASSUNTO_MAX_LENGTH` and `PROGETTO_MAX_LENGTH` (the note's ceiling). */
const RIASSUNTO_MAX = 1500
const NOTE_MAX = 4000

/** The API's own sentence whenever it gave one (a summary that names the company, a
 *  request gone); ours only when nothing answered at all. */
function sentence(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback
}

/** What the engine read about place in the company's description: whether the work is
 *  on site, and the place it named, if any. */
function place(luogo: AdminTeamProposal['luogo']): string {
  if (luogo.locale) return luogo.dove ? `In sede, a ${luogo.dove}` : 'In sede, luogo non indicato'
  return luogo.dove ? `Da remoto · ${luogo.dove}` : 'Da remoto'
}

/** The team's bands per day and per month, as the company read them. */
function teamBands(economia: AdminTeamProposal['economia']): string {
  if (!economia.giorno || !economia.mese) return 'Tariffa da definire'
  return `${bandLabel(economia.giorno)} · ${bandLabel(economia.mese, 'mese')}`
}

/** The answer to the availability mail and when it came; «—» until one does (D1). */
function answer(talent: TeamRequestTalent): string {
  if (talent.risposta === null) return '—'
  const label = TALENT_ANSWER_LABELS[talent.risposta] ?? talent.risposta
  return talent.risposta_at ? `${label} · ${formatDateTime(talent.risposta_at)}` : label
}

type Move = { stato: TeamRequestStato; azione: 'contatta' | 'chiudi' | 'riapri' }

const MOVE_LABELS: Record<Move['azione'], [idle: string, pending: string]> = {
  contatta: ['Segna come contattata', 'Segno…'],
  chiudi: ['Chiudi', 'Chiudo…'],
  riapri: ['Riapri', 'Riapro…'],
}

/** The moves a request in this state offers: a new one can be contacted or closed, a
 *  contacted one closed, and a closed one reopened where it was, after a mis-click. */
function moves(request: TeamRequest): Move[] {
  if (request.stato === 'nuova') {
    return [
      { stato: 'contattata', azione: 'contatta' },
      { stato: 'chiusa', azione: 'chiudi' },
    ]
  }
  if (request.stato === 'contattata') return [{ stato: 'chiusa', azione: 'chiudi' }]
  return [{ stato: request.contacted_at ? 'contattata' : 'nuova', azione: 'riapri' }]
}

/**
 * A team request, as the admin works it (REB-514, spec § 3.5): what the company typed,
 * the summary the talents will read (editable here, since it is what D1's mail sends),
 * the place the engine read, the team by name with each talent's own rate and the band
 * the company saw, the contacts, the state and the admin's note. Nothing is anonymised:
 * this is the admin's page. «Contatta i talenti» is D1's and does not appear here.
 */
export function AdminRichiestaTeam() {
  const { id } = useParams({ from: '/signedIn/admin/team/$id' })
  const row = useQuery({ queryKey: ['team-request', id], queryFn: () => admin.teamRequest(id) })
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
    void client.invalidateQueries({ queryKey: ['team-requests'] })
  }
  const members = new Map<string, AdminTeamMember>(
    (request.proposal?.team ?? []).map((member) => [member.freelancer_id, member]),
  )
  const teamId = useId()
  const contactsId = useId()
  return (
    <>
      <Header title={request.azienda} />
      <div className="grid gap-6 p-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          <dl className="space-y-3 text-sm">
            <Row label="Origine">{TEAM_ORIGIN_LABELS[request.origine] ?? request.origine}</Row>
            <Row label="Arrivata">{formatDateTime(request.created_at)}</Row>
            <Row label="Descrizione">
              {request.descrizione ? <p className="whitespace-pre-wrap">{request.descrizione}</p> : '—'}
            </Row>
            <Row label="Luogo">{request.proposal ? place(request.proposal.luogo) : '—'}</Row>
            {request.proposal && <Row label="Fascia del team">{teamBands(request.proposal.economia)}</Row>}
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
        {request.talenti.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nessun talento in questa richiesta.</p>
        ) : (
          <div className="overflow-x-auto overflow-y-hidden border border-border bg-card">
            <Table>
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
                {request.talenti.map((talent) => (
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
          <Row label="Azienda">{request.azienda}</Row>
          <Row label="Email">
            <a className="underline underline-offset-2" href={`mailto:${request.email}`}>
              {request.email}
            </a>
          </Row>
          <Row label="Telefono">
            {request.telefono ? (
              <a className="underline underline-offset-2" href={`tel:${request.telefono.replace(/[^\d+]/g, '')}`}>
                {request.telefono}
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

function TalentRow({ talent, member }: { talent: TeamRequestTalent; member: AdminTeamMember | undefined }) {
  return (
    <TableRow>
      <TableCell>
        <Link to="/admin/freelance/$id" params={{ id: talent.freelancer_id }} className="font-medium hover:underline">
          {`${talent.nome} ${talent.cognome}`}
        </Link>
      </TableCell>
      <TableCell className="min-w-64 space-y-1 whitespace-normal">
        <p>{talent.ruolo}</p>
        {member && <p className="text-xs text-muted-foreground">{member.motivazione}</p>}
        {member?.giorni_settimana ? (
          <p className="text-xs text-muted-foreground">{`${member.giorni_settimana} giorni a settimana`}</p>
        ) : null}
      </TableCell>
      <TableCell>{talent.tariffa_giornaliera === null ? '—' : formatEuro(talent.tariffa_giornaliera)}</TableCell>
      <TableCell>{talent.fascia ? bandLabel(talent.fascia) : '—'}</TableCell>
      <TableCell>{answer(talent)}</TableCell>
    </TableRow>
  )
}

/** «Salva il riassunto»: the proposal's summary, which D1's mail sends the talents. A
 *  request of one talent from the cloud has no proposal, so nothing to edit. */
function SummaryEditor({ request, onSaved }: { request: TeamRequest; onSaved: (updated: TeamRequest) => void }) {
  const headingId = useId()
  const hintId = useId()
  const [draft, setDraft] = useState(request.riassunto ?? '')
  const [problem, setProblem] = useState<string | null>(null)
  const save = useMutation({
    mutationFn: (riassunto: string) => admin.setTeamRequestSummary(request.id, riassunto),
    onSuccess: (updated) => {
      onSaved(updated)
      setDraft(updated.riassunto ?? '')
    },
  })
  function submit(event: FormEvent) {
    event.preventDefault()
    const riassunto = draft.trim()
    if (!riassunto) {
      setProblem('Il riassunto non può essere vuoto.')
      return
    }
    save.mutate(riassunto)
  }
  const failure = problem ?? (save.isError ? sentence(save.error, 'Non riesco a salvare il riassunto.') : null)
  return (
    <section aria-labelledby={headingId} className="space-y-3">
      <h2 id={headingId} className="text-sm font-medium">
        Riassunto
      </h2>
      {request.riassunto === null ? (
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
            maxLength={RIASSUNTO_MAX}
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
    mutationFn: ({ stato }: Move) => admin.setTeamRequestStatus(request.id, stato),
    onSuccess: onMoved,
  })
  return (
    <section aria-labelledby={headingId} className="space-y-3 border p-4">
      <h2 id={headingId} className="text-sm font-medium">
        Stato
      </h2>
      <Badge variant="pill">{TEAM_REQUEST_STATE_LABELS[request.stato] ?? request.stato}</Badge>
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
          const [idle, pending] = MOVE_LABELS[next.azione]
          return (
            <Button
              key={next.azione}
              type="button"
              size="sm"
              variant={next.azione === 'chiudi' ? 'outline' : 'default'}
              disabled={move.isPending}
              onClick={() => move.mutate(next)}
            >
              {move.isPending && move.variables?.azione === next.azione ? pending : idle}
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
