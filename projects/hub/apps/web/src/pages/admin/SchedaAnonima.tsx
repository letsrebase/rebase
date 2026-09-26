import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useId, type ReactNode } from 'react'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { admin, ApiError, type FreelancerCard } from '@/lib/api'
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

/** A list the card keeps as words, or «—» when it has none. */
function words(items: string[]): string {
  return items.length ? items.join(', ') : '—'
}

function Written({ read }: { read: FreelancerCard }) {
  const { card } = read
  if (!card) return null
  return (
    <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-[10rem_1fr]">
      <Field label="Ruolo">{card.ruolo}</Field>
      <Field label="Seniority">{SENIORITY_LABELS[card.seniority] ?? card.seniority}</Field>
      <Field label="Esperienza">{formatExperience(card.anni)}</Field>
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
 * builder off, a 503). The admin reads everything here, `luogo` included.
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
  else if (read.data.card) body = <Written read={read.data} />
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
