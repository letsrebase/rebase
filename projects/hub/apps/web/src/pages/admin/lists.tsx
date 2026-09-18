import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from '@tanstack/react-router'
import { ArrowLeft, Download } from 'lucide-react'
import { useState } from 'react'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { Textarea } from '@rebase/ui/textarea'
import { admin, type Comment, type Company, type Freelancer, type Signup } from '@/lib/api'
import {
  COMPANY_STATES,
  FREELANCER_LIST_STATES,
  FREELANCER_STATES,
  REMOTO_LABELS,
  STATE_LABELS,
  formatBytes,
  formatDate,
  formatDateTime,
  formatEuro,
} from '@/lib/format'
import { cn } from '@rebase/ui/cn'
import { Comments } from './Comments'

const TONE: Record<string, string> = {
  nuovo: 'bg-[var(--color-royal-gold)]',
  lead: 'bg-muted',
  contattato: 'bg-muted-foreground',
  attivo: 'bg-foreground',
  in_corso: 'bg-foreground',
  scartato: 'bg-[var(--color-watermelon)]',
  chiuso: 'bg-muted-foreground',
}

function StatePill({ stato }: { stato: string }) {
  return (
    <Badge variant="pill" className="gap-1.5">
      <span aria-hidden="true" className={cn('size-1.5 rounded-full', TONE[stato] ?? 'bg-muted')} />
      {STATE_LABELS[stato] ?? stato}
    </Badge>
  )
}

/** Beside the state pill on a card that is missing the CV, the rate, the position or
 *  the remote preference. Either an admin wrote it from a signup and the person has not
 *  finished yet (ORB-155), or they filled the wizard and skipped the CV, which the
 *  wizard lets them do. */
function IncompletePill() {
  return <Badge variant="pill">Da completare</Badge>
}

/** Who the answers on a card come from, as the detail's «Scheda» row says it. */
function ownership(f: Freelancer): string {
  if (f.compilata_da === 'persona') return 'compilata dalla persona'
  return f.completa ? 'scritta dall’admin' : 'scritta dall’admin, da completare'
}

export function Header({ title, count, children }: { title: string; count?: number; children?: React.ReactNode }) {
  return (
    <header className="flex flex-wrap items-center justify-between gap-3 border-b px-6 py-5">
      <h1 className="text-2xl font-semibold tracking-tight">
        {title}
        {count !== undefined && (
          <span className="ml-2 text-base font-normal text-muted-foreground">{count}</span>
        )}
      </h1>
      {children}
    </header>
  )
}

function StateFilter({
  states,
  value,
  onChange,
}: {
  states: readonly string[]
  value: string | undefined
  onChange: (value: string | undefined) => void
}) {
  return (
    <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filtra per stato">
      {[undefined, ...states].map((state) => (
        <Button
          key={state ?? 'tutti'}
          type="button"
          size="sm"
          variant={value === state ? 'default' : 'outline'}
          onClick={() => onChange(state)}
        >
          {state ? STATE_LABELS[state] : 'Tutti'}
        </Button>
      ))}
    </div>
  )
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <p className="px-6 py-10 text-center text-sm text-muted-foreground">{children}</p>
}

/** One number on a stats page («La guida», «Accessi»), inside a `<dl>`. */
export function Figure({ label, value, note }: { label: string; value: number; note?: string }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-1 text-3xl font-semibold tracking-tight">
        {value}
        {note && <span className="ml-2 text-base font-normal text-muted-foreground">{note}</span>}
      </dd>
    </div>
  )
}

// ---- freelancers -----------------------------------------------------------------------

/** One row of the freelancer table: a card, or a lead (a signup with no card, ORB-163),
 *  ordered together by when they arrived. */
type FreelancerRow = { kind: 'card'; at: string; card: Freelancer } | { kind: 'lead'; at: string; lead: Signup }

function mergeRows(items: Freelancer[], leads: Signup[]): FreelancerRow[] {
  const rows: FreelancerRow[] = [
    ...items.map((card): FreelancerRow => ({ kind: 'card', at: card.created_at, card })),
    ...leads.map((lead): FreelancerRow => ({ kind: 'lead', at: lead.created_at, lead })),
  ]
  return rows.sort((a, b) => b.at.localeCompare(a.at))
}

export function AdminFreelancers() {
  const [stato, setStato] = useState<string | undefined>(undefined)
  const list = useQuery({ queryKey: ['freelancers', stato], queryFn: () => admin.freelancers(stato) })
  const rows = list.data ? mergeRows(list.data.items, list.data.lead) : []
  return (
    <>
      <Header title="Developer e CTO" count={list.data ? list.data.totale + list.data.totale_lead : undefined}>
        <StateFilter states={FREELANCER_LIST_STATES} value={stato} onChange={setStato} />
      </Header>
      {list.isError ? (
        <Empty>Non riesco a leggere la lista.</Empty>
      ) : list.isPending ? (
        <Empty>Caricamento…</Empty>
      ) : rows.length === 0 ? (
        <Empty>Nessun profilo qui.</Empty>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-muted-foreground">
            <tr className="border-b">
              <th className="px-6 py-2 font-medium">Chi</th>
              <th className="px-3 py-2 font-medium">Provenienza</th>
              <th className="px-3 py-2 font-medium">Posizione</th>
              <th className="px-3 py-2 text-right font-medium">Tariffa</th>
              <th className="px-3 py-2 font-medium">Dove</th>
              <th className="px-3 py-2 font-medium">Stato</th>
              <th className="px-3 py-2 font-medium">Ultimo accesso</th>
              <th className="px-6 py-2 text-right font-medium">Quando</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) =>
              row.kind === 'lead' ? (
                <LeadRow key={`lead-${row.lead.id}`} lead={row.lead} />
              ) : (
              <tr key={row.card.id} className="border-b last:border-0 hover:bg-muted">
                <td className="px-6 py-2.5">
                  <Link to="/admin/freelance/$id" params={{ id: row.card.id }} className="font-medium hover:underline">
                    {row.card.nome} {row.card.cognome}
                  </Link>
                  <p className="text-xs text-muted-foreground">{row.card.email}</p>
                </td>
                <td className="px-3 py-2.5 text-muted-foreground">{row.card.provenienza}</td>
                <td className="px-3 py-2.5">{row.card.posizione ?? '—'}</td>
                <td className="px-3 py-2.5 text-right tabular-nums">
                  {row.card.tariffa_giornaliera === null ? '—' : formatEuro(row.card.tariffa_giornaliera)}
                </td>
                <td className="px-3 py-2.5">{row.card.remoto ? REMOTO_LABELS[row.card.remoto] : '—'}</td>
                <td className="px-3 py-2.5">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <StatePill stato={row.card.stato} />
                    {!row.card.completa && <IncompletePill />}
                  </div>
                </td>
                <td className="px-3 py-2.5 text-muted-foreground">
                  {row.card.ultimo_accesso === null ? '—' : formatDateTime(row.card.ultimo_accesso)}
                </td>
                <td className="px-6 py-2.5 text-right text-muted-foreground">{formatDate(row.card.created_at)}</td>
              </tr>
              ),
            )}
          </tbody>
        </table>
      )}
    </>
  )
}

/** A signup with no card (ORB-163): what the landing knows, a «Lead» pill, dashes for
 *  everything a card would carry, and no link, since there is no card to open. */
function LeadRow({ lead }: { lead: Signup }) {
  const name = [lead.nome, lead.cognome].filter(Boolean).join(' ')
  return (
    <tr className="border-b last:border-0 hover:bg-muted">
      <td className="px-6 py-2.5">
        <p className="font-medium">{name || '—'}</p>
        <p className="text-xs text-muted-foreground">{lead.email}</p>
      </td>
      <td className="px-3 py-2.5 text-muted-foreground">form</td>
      <td className="px-3 py-2.5">—</td>
      <td className="px-3 py-2.5 text-right">—</td>
      <td className="px-3 py-2.5">—</td>
      <td className="px-3 py-2.5"><StatePill stato="lead" /></td>
      <td className="px-3 py-2.5 text-muted-foreground">—</td>
      <td className="px-6 py-2.5 text-right text-muted-foreground">{formatDate(lead.created_at)}</td>
    </tr>
  )
}

function StatusEditor({
  states,
  stato,
  note,
  onSave,
  saving,
}: {
  states: readonly string[]
  stato: string
  note: string | null
  onSave: (stato: string, note: string) => void
  saving: boolean
}) {
  const [draftState, setDraftState] = useState(stato)
  const [draftNote, setDraftNote] = useState(note ?? '')
  return (
    <div className="space-y-3 rounded-2xl border bg-muted/40 p-4">
      <p className="text-sm font-medium">Stato e note</p>
      <StateFilter states={states} value={draftState} onChange={(value) => value && setDraftState(value)} />
      <Textarea
        aria-label="Note"
        value={draftNote}
        onChange={(event) => setDraftNote(event.target.value)}
        rows={3}
        placeholder="Una nota per chi rileggerà questa scheda"
      />
      <Button size="sm" onClick={() => onSave(draftState, draftNote)} disabled={saving}>
        {saving ? 'Salvo…' : 'Salva'}
      </Button>
    </div>
  )
}

export function AdminFreelancerDetail() {
  const { id } = useParams({ from: '/admin/freelance/$id' })
  const client = useQueryClient()
  const row = useQuery({ queryKey: ['freelancer', id], queryFn: () => admin.freelancer(id) })
  const move = useMutation({
    mutationFn: ({ stato, note }: { stato: string; note: string }) =>
      admin.moveFreelancer(id, stato, note.trim() || null),
    onSuccess: (updated: Freelancer) => {
      client.setQueryData(['freelancer', id], updated)
      void client.invalidateQueries({ queryKey: ['freelancers'] })
    },
  })
  // The thread lives on the detail row, so a new comment goes into the same cache entry
  // and nothing is fetched twice.
  const onCommentAdded = (created: Comment) =>
    client.setQueryData<Freelancer>(['freelancer', id], (current) =>
      current && { ...current, commenti: [created, ...current.commenti] },
    )
  if (row.isError) return <Empty>Scheda non trovata.</Empty>
  if (row.isPending) return <Empty>Caricamento…</Empty>
  const f = row.data
  return (
    <>
      <Header title={`${f.nome} ${f.cognome}`}>
        <div className="flex flex-wrap items-center gap-2">
          <StatePill stato={f.stato} />
          {!f.completa && <IncompletePill />}
          {f.cv_filename !== null && f.cv_size !== null && (
            <Button asChild variant="outline" size="sm">
              <a href={admin.cvUrl(f.id)}>
                <Download className="mr-2 size-4" />
                CV · {formatBytes(f.cv_size)}
              </a>
            </Button>
          )}
        </div>
      </Header>
      <div className="grid gap-6 p-6 lg:grid-cols-3">
        <dl className="space-y-3 text-sm lg:col-span-2">
          <Row label="Email"><a className="underline underline-offset-2" href={`mailto:${f.email}`}>{f.email}</a></Row>
          <Row label="Posizione">{f.posizione ?? '—'}</Row>
          <Row label="Tariffa a giornata">
            {f.tariffa_giornaliera === null ? '—' : formatEuro(f.tariffa_giornaliera)}
          </Row>
          <Row label="Modalità">{f.remoto ? REMOTO_LABELS[f.remoto] : '—'}</Row>
          <Row label="LinkedIn">
            {f.linkedin_url ? <a className="underline underline-offset-2" href={f.linkedin_url} target="_blank" rel="noreferrer">{f.linkedin_url}</a> : '—'}
          </Row>
          <Row label="Link">
            {f.links.length ? (
              <ul className="space-y-1">
                {f.links.map((link) => (
                  <li key={link}><a className="underline underline-offset-2" href={link} target="_blank" rel="noreferrer">{link}</a></li>
                ))}
              </ul>
            ) : '—'}
          </Row>
          <Row label="Arrivato">{formatDate(f.created_at)}{f.utm_source ? ` · da ${f.utm_source}` : ''}{f.origine ? ` · pagina ${f.origine}` : ''}</Row>
          <Row label="Provenienza">{f.provenienza}</Row>
          <Row label="Accessi">
            {f.accessi === 0 || f.ultimo_accesso === null
              ? 'Mai entrato'
              : `${f.accessi} · ultimo ${formatDateTime(f.ultimo_accesso)}`}
          </Row>
          <Row label="Scheda">{ownership(f)}</Row>
        </dl>
        <StatusEditor
          states={FREELANCER_STATES}
          stato={f.stato}
          note={f.note}
          saving={move.isPending}
          onSave={(stato, note) => move.mutate({ stato, note })}
        />
      </div>
      <Comments kind="freelancers" id={f.id} comments={f.commenti} onAdded={onCommentAdded} />
      <p className="px-6 pb-6">
        <Link to="/admin/freelance" className="inline-flex items-center gap-1 text-sm underline-offset-2 hover:underline">
          <ArrowLeft className="size-4" /> Tutti i developer e CTO
        </Link>
      </p>
    </>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[10rem_1fr] gap-3 border-b pb-3 last:border-0">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  )
}

// ---- companies -------------------------------------------------------------------------

export function AdminCompanies() {
  const [stato, setStato] = useState<string | undefined>(undefined)
  const list = useQuery({ queryKey: ['companies', stato], queryFn: () => admin.companies(stato) })
  return (
    <>
      <Header title="Aziende" count={list.data?.totale}>
        <StateFilter states={COMPANY_STATES} value={stato} onChange={setStato} />
      </Header>
      {list.isError ? (
        <Empty>Non riesco a leggere la lista.</Empty>
      ) : list.isPending ? (
        <Empty>Caricamento…</Empty>
      ) : list.data.items.length === 0 ? (
        <Empty>Nessuna richiesta qui.</Empty>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-muted-foreground">
            <tr className="border-b">
              <th className="px-6 py-2 font-medium">Azienda</th>
              <th className="px-3 py-2 font-medium">Progetto</th>
              <th className="px-3 py-2 font-medium">Periodo</th>
              <th className="px-3 py-2 text-right font-medium">Budget</th>
              <th className="px-3 py-2 font-medium">Stato</th>
              <th className="px-6 py-2 text-right font-medium">Quando</th>
            </tr>
          </thead>
          <tbody>
            {list.data.items.map((item) => (
              <tr key={item.id} className="border-b last:border-0 hover:bg-muted">
                <td className="px-6 py-2.5">
                  <Link to="/admin/aziende/$id" params={{ id: item.id }} className="font-medium hover:underline">
                    {item.nome_azienda}
                  </Link>
                  <p className="text-xs text-muted-foreground">{item.referente} · {item.email}</p>
                </td>
                <td className="max-w-xs truncate px-3 py-2.5">{item.progetto}</td>
                <td className="px-3 py-2.5">dal {formatDate(item.periodo_da)}, {item.durata}</td>
                <td className="px-3 py-2.5 text-right tabular-nums">{formatEuro(item.budget_giornaliero)}</td>
                <td className="px-3 py-2.5"><StatePill stato={item.stato} /></td>
                <td className="px-6 py-2.5 text-right text-muted-foreground">{formatDate(item.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  )
}

export function AdminCompanyDetail() {
  const { id } = useParams({ from: '/admin/aziende/$id' })
  const client = useQueryClient()
  const row = useQuery({ queryKey: ['company', id], queryFn: () => admin.company(id) })
  const move = useMutation({
    mutationFn: ({ stato, note }: { stato: string; note: string }) =>
      admin.moveCompany(id, stato, note.trim() || null),
    onSuccess: (updated: Company) => {
      client.setQueryData(['company', id], updated)
      void client.invalidateQueries({ queryKey: ['companies'] })
    },
  })
  const onCommentAdded = (created: Comment) =>
    client.setQueryData<Company>(['company', id], (current) =>
      current && { ...current, commenti: [created, ...current.commenti] },
    )
  if (row.isError) return <Empty>Richiesta non trovata.</Empty>
  if (row.isPending) return <Empty>Caricamento…</Empty>
  const c = row.data
  return (
    <>
      <Header title={c.nome_azienda}><StatePill stato={c.stato} /></Header>
      <div className="grid gap-6 p-6 lg:grid-cols-3">
        <dl className="space-y-3 text-sm lg:col-span-2">
          <Row label="Referente">{c.referente} · <a className="underline underline-offset-2" href={`mailto:${c.email}`}>{c.email}</a></Row>
          <Row label="Progetto"><p className="whitespace-pre-wrap">{c.progetto}</p></Row>
          <Row label="Periodo">dal {formatDate(c.periodo_da)}, {c.durata}</Row>
          <Row label="Budget a giornata">{formatEuro(c.budget_giornaliero)}</Row>
          <Row label="Arrivata">{formatDate(c.created_at)}{c.utm_source ? ` · da ${c.utm_source}` : ''}{c.origine ? ` · pagina ${c.origine}` : ''}</Row>
        </dl>
        <StatusEditor
          states={COMPANY_STATES}
          stato={c.stato}
          note={c.note}
          saving={move.isPending}
          onSave={(stato, note) => move.mutate({ stato, note })}
        />
      </div>
      <Comments kind="companies" id={c.id} comments={c.commenti} onAdded={onCommentAdded} />
      <p className="px-6 pb-6">
        <Link to="/admin/aziende" className="inline-flex items-center gap-1 text-sm underline-offset-2 hover:underline">
          <ArrowLeft className="size-4" /> Tutte le aziende
        </Link>
      </p>
    </>
  )
}

// ---- signups ---------------------------------------------------------------------------

export function AdminSignups() {
  const list = useQuery({ queryKey: ['signups'], queryFn: () => admin.signups() })
  return (
    <>
      <Header title="Iscrizioni" count={list.data?.totale} />
      {list.isError ? (
        <Empty>Non riesco a leggere la lista.</Empty>
      ) : list.isPending ? (
        <Empty>Caricamento…</Empty>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-muted-foreground">
            <tr className="border-b">
              <th className="px-6 py-2 font-medium">Chi</th>
              <th className="px-3 py-2 font-medium">LinkedIn</th>
              <th className="px-3 py-2 font-medium">Scheda</th>
              <th className="px-3 py-2 font-medium">Da</th>
              <th className="px-6 py-2 text-right font-medium">Quando</th>
            </tr>
          </thead>
          <tbody>
            {list.data.iscrizioni.map((item) => (
              <tr key={item.id} className="border-b last:border-0 hover:bg-muted">
                <td className="px-6 py-2.5">
                  <p className="font-medium">{[item.nome, item.cognome].filter(Boolean).join(' ') || '—'}</p>
                  <p className="text-xs text-muted-foreground">{item.email}</p>
                </td>
                <td className="px-3 py-2.5">
                  {item.linkedin_url ? <a className="underline underline-offset-2" href={item.linkedin_url} target="_blank" rel="noreferrer">profilo</a> : '—'}
                </td>
                <td className="px-3 py-2.5">
                  {item.freelancer_id ? (
                    <Link to="/admin/freelance/$id" params={{ id: item.freelancer_id }} className="underline underline-offset-2">
                      apri
                    </Link>
                  ) : '—'}
                </td>
                <td className="px-3 py-2.5 text-muted-foreground">{item.utm_source ?? '—'}</td>
                <td className="px-6 py-2.5 text-right text-muted-foreground">{formatDate(item.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  )
}
