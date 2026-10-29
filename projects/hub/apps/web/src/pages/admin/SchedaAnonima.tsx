import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useId, useState, type FormEvent, type ReactNode } from 'react'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import {
  admin,
  ApiError,
  type CardConflict,
  type CardProvenance,
  type FreelancerCard,
  type PersonaDifferenceField,
  type Scheda,
} from '@/lib/api'
import { useFreelancerCard } from '@/hooks/use-freelancer-card'
import { bandLabel } from '@/lib/bands'
import { REMOTO_LABELS, SENIORITY_LABELS, formatDateTime, formatExperience } from '@/lib/format'

export function Field({ label, children }: { label: string; children: ReactNode }) {
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
  // The two fields are `flex-col gap-1`, not `space-y-1`: Radix renders a hidden native
  // `<select>` after the trigger inside a form, so the trigger is never the last child and
  // `space-y-1` would give it a bottom margin the number input does not have (REB-857).
  // Every control is the admin's standard 36px (`h-9`: Input, SelectTrigger, a default
  // Button), bottom-aligned, and each label stays on one line.
  return (
    <form onSubmit={submit} className="space-y-2 rounded-md border p-3 text-sm">
      <p className="font-medium">Correggi il livello</p>
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${id}-seniority`} className="whitespace-nowrap">
            Seniority dell’admin
          </Label>
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
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${id}-anni`} className="whitespace-nowrap">
            Anni dell’admin
          </Label>
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
        <Button type="submit" disabled={save.isPending || invalid}>
          {save.isPending ? 'Salvo…' : 'Salva il livello'}
        </Button>
        {corrected && (
          <Button
            type="button"
            variant="outline"
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

const STALE_SENTENCES: Record<string, string> = {
  cv: 'Il CV è cambiato: la scheda si rigenera da sola',
  source: 'Da aggiornare: le fonti o i dati della hub sono cambiati dopo che la scheda è stata scritta',
  prompt: 'Da aggiornare: esiste una versione più recente del prompt',
}
// The card's own order, which a stored `fonti` (a JSONB object, keys sorted by length) does not keep.
const FIELD_LABELS: Record<string, string> = {
  ruolo: 'Ruolo',
  anni: 'Esperienza',
  seniority: 'Seniority',
  competenze: 'Competenze',
  settori: 'Settori',
  lingue: 'Lingue',
  luogo: 'Luogo',
  sintesi: 'Sintesi',
}
const PROVIDER_LABELS: Record<string, string> = { anthropic: 'Anthropic', openai: 'OpenAI', google: 'Google' }

/** `cv` -> «CV», `link:2` -> «Link 2»: the keys the model cites, in the page's words. */
function sourceLabel(key: string): string {
  if (key === 'cv') return 'CV'
  if (key === 'github') return 'GitHub'
  if (key === 'hub') return 'Dati della hub'
  const link = /^link:(\d+)$/.exec(key)
  return link ? `Link ${link[1]}` : key
}

function conflictSentence(conflict: CardConflict): string {
  if (conflict.kind === 'anni_diversi') {
    return `Gli incarichi completati in hub fanno ${conflict.anni_hub} anni, la scheda ne dichiara ${conflict.anni_scheda} (date del CV)`
  }
  return `Anni scartati: ${conflict.anni_scheda}, venivano solo da fonti senza date professionali`
}

function Provenance({ provenance }: { provenance: CardProvenance }) {
  const written = Object.keys(provenance.fonti).length > 0
  const fields = Object.keys(FIELD_LABELS).filter((field) => field in provenance.fonti)
  return (
    <div className="space-y-2 rounded-md border p-3 text-sm">
      <p className="font-medium">Da dove viene la scheda</p>
      {provenance.stale && <p className="text-muted-foreground">{STALE_SENTENCES[provenance.stale]}</p>}
      <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-[10rem_1fr]">
        <Field label="Scritta da">
          {provenance.provider ? (PROVIDER_LABELS[provenance.provider] ?? provenance.provider) : 'modello non registrato'}{' '}
          · Prompt v{provenance.prompt_version}
        </Field>
        <Field label="Fonti, secondo il modello">
          {written ? (
            <ul>
              {fields.map((field) => {
                const keys = provenance.fonti[field] ?? []
                return (
                  <li key={field}>
                    {FIELD_LABELS[field]}: {keys.length ? keys.map(sourceLabel).join(', ') : 'nessuna'}
                  </li>
                )
              })}
            </ul>
          ) : (
            'Scheda scritta prima della registrazione delle fonti'
          )}
        </Field>
        <Field label="Fonti salvate">
          {provenance.sources.length ? (
            <ul>
              {provenance.sources.map((source) => (
                <li key={source.key}>
                  {sourceLabel(source.key)}: {source.ref}, letta il{' '}
                  {formatDateTime(source.fetched_at)}
                  {source.status !== 'ok' && ` (${source.status})`}
                </li>
              ))}
            </ul>
          ) : (
            'Nessuna'
          )}
        </Field>
        {provenance.conflicts.length > 0 && (
          <Field label="Discordanze">
            <ul>
              {provenance.conflicts.map((conflict, index) => (
                <li key={`${index}-${conflict.kind}`}>{conflictSentence(conflict)}</li>
              ))}
            </ul>
          </Field>
        )}
      </dl>
    </div>
  )
}

/**
 * The rows the anonymous card and the named one both show (role, seniority, years, skills):
 * one component, so the two sections read the same values and cannot disagree.
 */
function CardRows({ read }: { read: FreelancerCard }) {
  const { card } = read
  if (!card) return null
  return (
    <>
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
    </>
  )
}

function Written({ read }: { read: FreelancerCard }) {
  const { card } = read
  if (!card) return null
  return (
    <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-[10rem_1fr]">
      <CardRows read={read} />
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

const DIFFERENCE_LABELS: Record<PersonaDifferenceField, string> = {
  nome: 'Nome',
  cognome: 'Cognome',
  email: 'Email',
  telefono: 'Telefono',
  linkedin_url: 'LinkedIn',
}

/** A link only for an `http(s)` address, plain text for anything else: the API keeps
 *  only those, and the page does not trust it to. */
function WebLink({ href }: { href: string }) {
  if (!/^https?:\/\//i.test(href)) return <>{href}</>
  return (
    <a className="underline underline-offset-2" href={href} target="_blank" rel="noreferrer">
      {href}
    </a>
  )
}

/**
 * «Scheda con i dati» (REB-855): who the CV says the person is, which the same call that
 * wrote the anonymous card read out of it, for admins only, and the card's own role,
 * seniority, years and skills beside it (`CardRows`). Where the CV and the profile both
 * say something and differ, a short list says so: the profile is never rewritten from the
 * CV, the admin decides which is right. A card written before prompt version 3 has no
 * data yet, and says «Rigenera scheda» reads it.
 */
function SchedaConDati({ read }: { read: FreelancerCard }) {
  const headingId = useId()
  const { persona, differenze = [] } = read
  let body: ReactNode
  if (!persona) {
    body = (
      <p className="text-sm text-muted-foreground">
        Nessun dato letto dal CV: «Rigenera scheda» lo legge con la versione attuale del prompt.
      </p>
    )
  } else {
    const name = [persona.nome, persona.cognome].filter(Boolean).join(' ')
    body = (
      <div className="space-y-3">
        {differenze.length > 0 && (
          <div role="alert" className="space-y-1 rounded-md border border-destructive/40 p-3 text-sm">
            <p className="font-medium text-destructive">Il CV e il profilo non coincidono</p>
            <ul>
              {differenze.map((difference) => (
                <li key={difference.campo}>
                  {DIFFERENCE_LABELS[difference.campo]}: nel CV «{difference.cv}», nel profilo «{difference.profilo}»
                </li>
              ))}
            </ul>
          </div>
        )}
        <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-[10rem_1fr]">
          <Field label="Nome">{name || '—'}</Field>
          <Field label="Email">
            {persona.email ? (
              <a className="underline underline-offset-2" href={`mailto:${persona.email}`}>
                {persona.email}
              </a>
            ) : (
              '—'
            )}
          </Field>
          <Field label="Telefono">{persona.telefono ?? '—'}</Field>
          <Field label="Città">{persona.citta ?? '—'}</Field>
          <Field label="LinkedIn">{persona.linkedin_url ? <WebLink href={persona.linkedin_url} /> : '—'}</Field>
          <Field label="Altri link">
            {persona.altri_link.length ? (
              <ul>
                {persona.altri_link.map((link) => (
                  <li key={link}>
                    <WebLink href={link} />
                  </li>
                ))}
              </ul>
            ) : (
              '—'
            )}
          </Field>
          <Field label="Aziende e clienti">{words(persona.aziende)}</Field>
          <CardRows read={read} />
          {read.card && <Field label="Sintesi">{read.card.sintesi}</Field>}
        </dl>
      </div>
    )
  }
  return (
    <section aria-labelledby={headingId} className="space-y-3 px-6 pb-6">
      <h2 id={headingId} className="text-sm font-medium">
        Scheda con i dati
      </h2>
      {body}
    </section>
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
  const read = useFreelancerCard(freelancerId)
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
        {read.data.provenance && <Provenance provenance={read.data.provenance} />}
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
    <>
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
      {!read.isPending && !read.isError && (read.data.persona != null || read.data.card != null) && (
        <SchedaConDati read={read.data} />
      )}
    </>
  )
}
