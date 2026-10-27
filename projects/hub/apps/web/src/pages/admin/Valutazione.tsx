import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from '@tanstack/react-router'
import { useState, type FormEvent } from 'react'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import { Textarea } from '@rebase/ui/textarea'
import {
  admin,
  ApiError,
  type CardEval,
  type EvalField,
  type EvalProfile,
  type EvalRatings,
  type EvalRun,
  type Scheda,
} from '@/lib/api'
import { SENIORITY_LABELS, formatDateTime, formatExperience } from '@/lib/format'
import { Empty, Header } from './lists'
import { Field } from './SchedaAnonima'

const KEY = ['card-eval'] as const
const FIELDS: { field: EvalField; label: string }[] = [
  { field: 'ruolo', label: 'Ruolo' },
  { field: 'competenze', label: 'Competenze' },
  { field: 'sintesi', label: 'Sintesi' },
]
const SCALE = [
  { value: 1, label: 'No' },
  { value: 2, label: 'Così così' },
  { value: 3, label: 'Sì' },
] as const

function problem(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback
}

function yesNo(value: boolean | null): string {
  return value === null ? '—' : value ? 'Sì' : 'No'
}

function StatusBadge({ status }: { status: EvalProfile['status'] }) {
  return status === 'accettata' ? <Badge>Accettata</Badge> : <Badge variant="outline">Bozza</Badge>
}

/** The set the card writer is measured against, and the runs that measured it (REB-836). */
export function AdminValutazione() {
  const read = useQuery({ queryKey: KEY, queryFn: () => admin.cardEval() })
  return (
    <div>
      <Header title="Valutazione" count={read.data?.profiles.length} />
      {read.isError && <Empty>Non riesco a leggere la valutazione. Riprova tra poco.</Empty>}
      {read.data && <Sections data={read.data} />}
    </div>
  )
}

function Sections({ data }: { data: CardEval }) {
  return (
    <>
      <section aria-label="Profili" className="space-y-3 py-5">
        <h2 className="px-6 text-sm font-medium">Schede attese</h2>
        {data.profiles.length === 0 ? (
          <Empty>Nessun profilo nel set. Si aggiungono da riga di comando con rebase cards-eval-draft.</Empty>
        ) : (
          <Table scrollLabel="Profili del set di valutazione">
            <TableHeader>
              <TableRow>
                <TableHead>Persona</TableHead>
                <TableHead>Caso</TableHead>
                <TableHead>Stato</TableHead>
                <TableHead>Corretta da</TableHead>
                <TableHead>Aggiornata</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.profiles.map((profile) => (
                <TableRow key={profile.freelancer_id}>
                  <TableCell>
                    <Link
                      to="/admin/valutazione/$id"
                      params={{ id: profile.freelancer_id }}
                      className="font-medium hover:underline"
                    >
                      {profile.nome} {profile.cognome}
                    </Link>
                  </TableCell>
                  <TableCell>{profile.label}</TableCell>
                  <TableCell>
                    <StatusBadge status={profile.status} />
                  </TableCell>
                  <TableCell>{profile.corrected_by_nome ?? '—'}</TableCell>
                  <TableCell>{formatDateTime(profile.updated_at)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </section>
      <section aria-label="Prove" className="space-y-4 py-2">
        <h2 className="px-6 text-sm font-medium">Prove</h2>
        {data.runs.length === 0 ? (
          <Empty>Nessuna prova ancora. Si lancia con rebase cards-eval.</Empty>
        ) : (
          data.runs.map((run) => <Run key={run.id} run={run} />)
        )}
      </section>
    </>
  )
}

function Run({ run }: { run: EvalRun }) {
  const client = useQueryClient()
  const [ratings, setRatings] = useState<EvalRatings>(() =>
    Object.fromEntries(run.results.map((result) => [result.freelancer_id, { ...result.ratings }])),
  )
  const save = useMutation({
    mutationFn: () => admin.cardEvalRate(run.id, ratings),
    onSuccess: () => client.invalidateQueries({ queryKey: KEY }),
  })
  function rate(freelancerId: string, field: EvalField, value: number) {
    setRatings((current) => ({
      ...current,
      [freelancerId]: { ...current[freelancerId], [field]: value },
    }))
  }
  return (
    <article aria-label={`Prova ${run.model}`} className="space-y-3 border-t px-6 py-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-sm font-medium">
            {run.provider} · {run.model}
            {run.effort ? ` · ${run.effort}` : ''}
          </p>
          <p className="text-xs text-muted-foreground">
            {formatDateTime(run.created_at)} · prompt v{run.prompt_version}
            {run.created_by_nome ? ` · ${run.created_by_nome}` : ''}
          </p>
        </div>
        <Button size="sm" onClick={() => save.mutate()} disabled={save.isPending}>
          {save.isPending ? 'Salvo…' : 'Salva i giudizi'}
        </Button>
      </div>
      {save.isError && (
        <p role="alert" className="text-sm text-destructive">
          {problem(save.error, 'Non riesco a salvare i giudizi.')}
        </p>
      )}
      <Table scrollLabel="Risultati della prova">
        <TableHeader>
          <TableRow>
            <TableHead>Caso</TableHead>
            <TableHead>Anni</TableHead>
            <TableHead>Seniority</TableHead>
            <TableHead>Guardia</TableHead>
            <TableHead>Tempo</TableHead>
            <TableHead>Costo</TableHead>
            {FIELDS.map(({ field, label }) => (
              <TableHead key={field}>{label}</TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {run.results.map((result) => (
            <TableRow key={result.freelancer_id}>
              <TableCell>
                <Link
                  to="/admin/valutazione/$id"
                  params={{ id: result.freelancer_id }}
                  className="font-medium hover:underline"
                >
                  {result.label}
                </Link>
                {result.failure && <p className="text-xs text-destructive">{result.failure}</p>}
              </TableCell>
              <TableCell>{yesNo(result.anni_ok)}</TableCell>
              <TableCell>{yesNo(result.seniority_ok)}</TableCell>
              <TableCell>{yesNo(result.guard_clean)}</TableCell>
              <TableCell>{result.latency_ms == null ? '—' : `${result.latency_ms} ms`}</TableCell>
              <TableCell>{result.cost_eur ?? '—'}</TableCell>
              {FIELDS.map(({ field, label }) => (
                <TableCell key={field}>
                  {result.card ? (
                    <div role="group" aria-label={`${label} di ${result.label}`} className="flex gap-1">
                      {SCALE.map(({ value, label: text }) => (
                        <Button
                          key={value}
                          type="button"
                          size="sm"
                          variant={ratings[result.freelancer_id]?.[field] === value ? 'default' : 'outline'}
                          aria-pressed={ratings[result.freelancer_id]?.[field] === value}
                          onClick={() => rate(result.freelancer_id, field, value)}
                        >
                          {text}
                        </Button>
                      ))}
                    </div>
                  ) : (
                    '—'
                  )}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </article>
  )
}

export function AdminValutazioneProfilo() {
  const { id } = useParams({ from: '/signedIn/admin/valutazione/$id' })
  return <ProfileEditor id={id} />
}

function list(text: string): string[] {
  return text
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
}

const EMPTY: Scheda = {
  ruolo: '',
  seniority: 'mid',
  anni: null,
  competenze: [],
  settori: [],
  lingue: [],
  luogo: null,
  sintesi: '',
}

/** «Scheda attesa»: the editor the admin corrects the drafted card in, then accepts. */
export function ProfileEditor({ id }: { id: string }) {
  const read = useQuery({ queryKey: KEY, queryFn: () => admin.cardEval() })
  const profile = read.data?.profiles.find((item) => item.freelancer_id === id)
  return (
    <div>
      <Header title="Scheda attesa">
        <Button asChild variant="outline" size="sm">
          <Link to="/admin/valutazione">Torna alla valutazione</Link>
        </Button>
      </Header>
      {read.isError && <Empty>Non riesco a leggere la valutazione. Riprova tra poco.</Empty>}
      {read.data && !profile && <Empty>Questo profilo non è nel set di valutazione.</Empty>}
      {profile && (
        <ProfileForm
          key={`${profile.updated_at}-${profile.status}`}
          profile={profile}
          generated={read.data?.runs[0]?.results.find((result) => result.freelancer_id === id)?.card ?? null}
        />
      )}
    </div>
  )
}

function ProfileForm({ profile, generated }: { profile: EvalProfile; generated: Scheda | null }) {
  const client = useQueryClient()
  const start = profile.expected ?? EMPTY
  const [ruolo, setRuolo] = useState(start.ruolo)
  const [seniority, setSeniority] = useState<Scheda['seniority']>(start.seniority)
  const [anni, setAnni] = useState(start.anni == null ? '' : String(start.anni))
  const [competenze, setCompetenze] = useState(start.competenze.join(', '))
  const [settori, setSettori] = useState(start.settori.join(', '))
  const [lingue, setLingue] = useState(start.lingue.join(', '))
  const [luogo, setLuogo] = useState(start.luogo ?? '')
  const [sintesi, setSintesi] = useState(start.sintesi)
  const years = anni.trim() === '' ? null : Number(anni)
  const invalid = years !== null && (!Number.isInteger(years) || years < 0 || years > 60)
  const after = () => client.invalidateQueries({ queryKey: KEY })
  const save = useMutation({
    mutationFn: (card: Scheda) => admin.cardEvalSetExpected(profile.freelancer_id, card),
    onSuccess: after,
  })
  const accept = useMutation({ mutationFn: () => admin.cardEvalAccept(profile.freelancer_id), onSuccess: after })
  function submit(event: FormEvent) {
    event.preventDefault()
    if (invalid) return
    save.mutate({
      ruolo: ruolo.trim(),
      seniority,
      anni: years,
      competenze: list(competenze),
      settori: list(settori),
      lingue: list(lingue),
      luogo: luogo.trim() || null,
      sintesi: sintesi.trim(),
    })
  }
  return (
    <div className="space-y-6 px-6 py-5">
      <div className="flex flex-wrap items-center gap-3">
        <p className="text-sm font-medium">
          {profile.nome} {profile.cognome} · {profile.label}
        </p>
        <StatusBadge status={profile.status} />
        {profile.corrected_by_nome && (
          <span className="text-xs text-muted-foreground">Corretta da {profile.corrected_by_nome}</span>
        )}
      </div>
      <form onSubmit={submit} className="grid max-w-2xl gap-4">
        <div className="space-y-1">
          <Label htmlFor="ev-ruolo">Ruolo</Label>
          <Input id="ev-ruolo" value={ruolo} onChange={(event) => setRuolo(event.target.value)} required />
        </div>
        <div className="flex flex-wrap gap-4">
          <div className="space-y-1">
            <Label htmlFor="ev-seniority">Seniority</Label>
            <Select value={seniority} onValueChange={(value) => setSeniority(value as Scheda['seniority'])}>
              <SelectTrigger id="ev-seniority" className="w-44">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Object.entries(SENIORITY_LABELS).map(([value, label]) => (
                  <SelectItem key={value} value={value}>
                    {label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="ev-anni">Anni</Label>
            <Input
              id="ev-anni"
              type="number"
              min={0}
              max={60}
              className="w-28"
              value={anni}
              onChange={(event) => setAnni(event.target.value)}
              aria-invalid={invalid}
            />
          </div>
        </div>
        {invalid && <p className="text-sm text-destructive">Gli anni sono un numero intero da 0 a 60.</p>}
        <div className="space-y-1">
          <Label htmlFor="ev-competenze">Competenze (separate da virgole)</Label>
          <Input id="ev-competenze" value={competenze} onChange={(event) => setCompetenze(event.target.value)} />
        </div>
        <div className="space-y-1">
          <Label htmlFor="ev-settori">Settori (separati da virgole)</Label>
          <Input id="ev-settori" value={settori} onChange={(event) => setSettori(event.target.value)} />
        </div>
        <div className="space-y-1">
          <Label htmlFor="ev-lingue">Lingue (separate da virgole)</Label>
          <Input id="ev-lingue" value={lingue} onChange={(event) => setLingue(event.target.value)} />
        </div>
        <div className="space-y-1">
          <Label htmlFor="ev-luogo">Luogo</Label>
          <Input id="ev-luogo" value={luogo} onChange={(event) => setLuogo(event.target.value)} />
        </div>
        <div className="space-y-1">
          <Label htmlFor="ev-sintesi">Sintesi</Label>
          <Textarea id="ev-sintesi" rows={3} value={sintesi} onChange={(event) => setSintesi(event.target.value)} required />
        </div>
        <div className="flex flex-wrap gap-3">
          <Button type="submit" disabled={save.isPending || invalid}>
            {save.isPending ? 'Salvo…' : 'Salva la correzione'}
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={accept.isPending || profile.expected === null || profile.status === 'accettata'}
            onClick={() => accept.mutate()}
          >
            {accept.isPending ? 'Accetto…' : 'Accetta la scheda'}
          </Button>
        </div>
        {(save.isError || accept.isError) && (
          <p role="alert" className="text-sm text-destructive">
            {problem(save.error ?? accept.error, 'Non riesco a salvare.')}
          </p>
        )}
      </form>
      {generated && (
        <section aria-label="Ultima scheda scritta" className="space-y-2">
          <h2 className="text-sm font-medium">Ultima scheda scritta dalla prova</h2>
          <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-[10rem_1fr]">
            <Field label="Ruolo">{generated.ruolo}</Field>
            <Field label="Esperienza">
              {SENIORITY_LABELS[generated.seniority] ?? generated.seniority}, {formatExperience(generated.anni)}
            </Field>
            <Field label="Competenze">{generated.competenze.join(', ') || '—'}</Field>
            <Field label="Sintesi">{generated.sintesi}</Field>
          </dl>
        </section>
      )}
    </div>
  )
}
