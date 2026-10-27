import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useId, useState, type FormEvent, type ReactNode } from 'react'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import { admin, ApiError, type FreelancerCard, type Scheda } from '@/lib/api'
import { bandLabel } from '@/lib/bands'
import { REMOTO_LABELS, SENIORITY_LABELS, formatDateTime, formatExperience } from '@/lib/format'

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </>
  )
}

/** A list the card keeps as words, or an empty mark when it has none. */
function words(items: string[]): string {
  return items.length ? items.join(', ') : '—'
}

const NO_CORRECTION = 'nessuna'

/**
 * The level an admin sets over Claude's reading (REB-799): a seniority and a number of
 * years, each optional, saved through the talent's override, which keeps it across a
 * «Rigenera scheda» and records it in the audit trail. «Togli la correzione» puts
 * Claude's own reading back.
 */
function LevelEditor({ freelancerId, read }: { freelancerId: string; read: FreelancerCard }) {
  const client = useQueryClient()
  const id = useId()
  const [seniority, setSeniority] = useState<string>(read.seniority_admin ?? NO_CORRECTION)
  const [anni, setAnni] = useState<string>(read.anni_admin == null ? '' : String(read.anni_admin))
  const save = useMutation({
    mutationFn: (data: { seniority_admin: Scheda['seniority'] | null; anni_admin: number | null }) =>
      admin.overrideFreelancer(freelancerId, data),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['freelancer-card', freelancerId] })
      void client.invalidateQueries({ queryKey: ['freelancer', freelancerId] })
      void client.invalidateQueries({ queryKey: ['audit', 'freelancers', freelancerId] })
      void client.invalidateQueries({ queryKey: ['freelancers'] })
    },
  })
  const years = anni.trim() === '' ? null : Number(anni)
  const invalid = years !== null && (!Number.isInteger(years) || years < 0 || years > 60)
  const corrected = read.seniority_admin != null || read.anni_admin != null
  function submit(event: FormEvent) {
    event.preventDefault()
    if (invalid) return
    save.mutate({
      seniority_admin: seniority === NO_CORRECTION ? null : (seniority as Scheda['seniority']),
      anni_admin: years,
    })
  }
  return (
    <form onSubmit={submit} className="space-y-2 rounded-md border p-3 text-sm">
      <p className="font-medium">Correggi il livello</p>
      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <Label htmlFor={`${id}-seniority`}>Seniority dell’admin</Label>
          <Select value={seniority} onValueChange={setSeniority}>
            <SelectTrigger id={`${id}-seniority`} className="w-44">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={NO_CORRECTION}>Come la scheda</SelectItem>
              {Object.entries(SENIORITY_LABELS).map(([value, label]) => (
                <SelectItem key={value} value={value}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <Label htmlFor={`${id}-anni`}>Anni dell’admin</Label>
          <Input
            id={`${id}-anni`}
            type="number"
            min={0}
            max={60}
            className="w-28"
            value={anni}
            onChange={(event) => setAnni(event.target.value)}
            aria-invalid={invalid}
          />
        </div>
        <Button type="submit" size="sm" disabled={save.isPending || invalid}>
          {save.isPending ? 'Salvo…' : 'Salva il livello'}
        </Button>
        {corrected && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={save.isPending}
            onClick={() => {
              setSeniority(NO_CORRECTION)
              setAnni('')
              save.mutate({ seniority_admin: null, anni_admin: null })
            }}
          >
            Togli la correzione
          </Button>
        )}
      </div>
      {invalid && <p className="text-destructive">Gli anni sono un numero intero da 0 a 60.</p>}
      {save.isError && (
        <p role="alert" className="text-destructive">
          {save.error instanceof ApiError ? save.error.message : 'Non riesco a salvare il livello.'}
        </p>
      )}
    </form>
  )
}

function Written({ read }: { read: FreelancerCard }) {
  const { card } = read
  if (!card) return null
  return (
    <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-[10rem_1fr]">
      <Field label="Ruolo">{card.ruolo}</Field>
      <Field label="Seniority">
        <span className="flex flex-wrap items-center gap-1.5">
          {SENIORITY_LABELS[card.seniority] ?? card.seniority}
          {card.seniority_incerta && <Badge variant="outline">Da verificare</Badge>}
          {read.seniority_admin != null && <Badge variant="outline">Corretta dall’admin</Badge>}
        </span>
      </Field>
      <Field label="Esperienza">
        <span className="flex flex-wrap items-center gap-1.5">
          {formatExperience(card.anni)}
          {read.anni_admin != null && <Badge variant="outline">Corretta dall’admin</Badge>}
        </span>
      </Field>
      <Field label="Competenze">
        {card.competenze.length ? (
          <span className="flex flex-wrap gap-1.5">
            {card.competenze.map((skill, index) => (
              <Badge key={`${index}-${skill}`} variant="outline">
                {skill}
              </Badge>
            ))}
          </span>
        ) : (
          '—'
        )}
      </Field>
      <Field label="Settori">{words(card.settori)}</Field>
      <Field label="Lingue">{words(card.lingue)}</Field>
      <Field label="Luogo">{card.luogo ?? '—'}</Field>
      <Field label="Sintesi">{card.sintesi}</Field>
      <Field label="Modalità">{read.modalita ? (REMOTO_LABELS[read.modalita] ?? read.modalita) : '—'}</Field>
      <Field label="Fascia cliente">{read.fascia ? bandLabel(read.fascia) : 'Tariffa da definire'}</Field>
      <Field label="Scritta">
        {read.generated_at ? `${formatDateTime(read.generated_at)} · ${read.model ?? '—'}` : '—'}
      </Field>
      {read.error && <Field label="Ultimo errore">{read.error}</Field>}
    </dl>
  )
}

/**
 * «Scheda anonima» on the admin's talent page (REB-514, spec § 2.1, § 5.1): the card
 * Claude wrote from the CV, which is all the team builder reads of this person, with
 * the work mode from the profile and the band a company would read, both of which core
 * reads from the profile when the card is shown, so an edited rate or mode shows at
 * once and the page computes no band of its own. Without a card, the
 * sentence the writer left, or that there is none. «Rigenera scheda» writes it again
 * from the current CV even when that CV failed before; a failure comes back as the
 * card's own `error`, and the API's sentence is shown when it refuses outright (the
 * builder off, a 503). The admin reads everything here, `luogo` included. The level
 * (seniority and years) is Claude's reading unless an admin corrected it, which the
 * section says and lets them do (REB-799).
 */
export function SchedaAnonima({ freelancerId, hasCv }: { freelancerId: string; hasCv: boolean }) {
  const headingId = useId()
  const client = useQueryClient()
  // `AdminFreelancerDetail` invalidates this key after «Rimuovi CV», «Modifica scheda»,
  // «Elimina» and «Ripristina», since each changes what the section reads (the card,
  // the mode, the band from the rate).
  const key = ['freelancer-card', freelancerId] as const
  const read = useQuery({ queryKey: key, queryFn: () => admin.freelancerCard(freelancerId) })
  const regenerate = useMutation({
    mutationFn: () => admin.regenerateFreelancerCard(freelancerId),
    onSuccess: (updated) => client.setQueryData<FreelancerCard>(key, updated),
  })
  let body: ReactNode
  if (read.isError) body = <p className="text-sm text-muted-foreground">Non riesco a leggere la scheda anonima.</p>
  else if (read.isPending) body = <p className="text-sm text-muted-foreground">Caricamento…</p>
  else if (read.data.card) {
    body = (
      <div className="space-y-4">
        <Written read={read.data} />
        <LevelEditor key={`${read.data.seniority_admin}-${read.data.anni_admin}`} freelancerId={freelancerId} read={read.data} />
      </div>
    )
  }
  else if (read.data.error) body = <p className="text-sm">{read.data.error}</p>
  else {
    body = (
      <p className="text-sm text-muted-foreground">{hasCv ? 'Nessuna scheda ancora.' : 'Nessuna scheda: manca il CV.'}</p>
    )
  }
  return (
    <section aria-labelledby={headingId} className="space-y-3 px-6 pb-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id={headingId} className="text-sm font-medium">
          Scheda anonima
        </h2>
        {hasCv && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => regenerate.mutate()}
            disabled={regenerate.isPending}
          >
            {regenerate.isPending ? 'Rigenero…' : 'Rigenera scheda'}
          </Button>
        )}
      </div>
      {regenerate.isError && (
        <p role="alert" className="text-sm text-destructive">
          {regenerate.error instanceof ApiError ? regenerate.error.message : 'Non riesco a rigenerare la scheda.'}
        </p>
      )}
      {body}
    </section>
  )
}
