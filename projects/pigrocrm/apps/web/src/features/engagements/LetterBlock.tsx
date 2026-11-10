import { useState } from 'react'
import { DownloadButton } from '@/components/DownloadButton'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Loader } from '@rebase/ui/loader'
import { toast } from '@rebase/ui/sonner'
import { toProblem } from '@/lib/api'
import { Textarea } from '@rebase/ui/textarea'
import { HIDDEN } from './words'
import {
  EMPTY,
  datesWords,
  feeWords,
  formatQuantity,
  paymentRuleWords,
  waitingSentence,
  type Engagement,
} from './labels'
import { useChangeEngagementState, useCreateLetter, usePreviewLetter, useSendEngagement } from './queries'

/** The free-text fields of the letter's text, by the name the template declares them under. */
const FIELDS: ReadonlyArray<{ name: string; label: string; multiline: boolean }> = [
  { name: 'attivita', label: 'Attività', multiline: true },
  { name: 'risultati', label: 'Risultati', multiline: true },
  { name: 'luogo', label: 'Luogo', multiline: false },
]

/** The terms in one line: the fee over its unit and days a week, the dates, the expected days. */
function termsLine(engagement: Engagement): string {
  const parts: string[] = []
  if (engagement.redacted) parts.push(HIDDEN)
  else {
    const fee = feeWords(engagement.fee, engagement.fee_unit, engagement.days_per_week)
    if (fee !== null) parts.push(fee)
  }
  parts.push(datesWords(engagement.starts_on, engagement.ends_on))
  if (engagement.expected_days !== null) {
    const unit = engagement.fee_unit === 'hour' ? 'ore previste' : 'giorni previsti'
    parts.push(`${formatQuantity(engagement.expected_days)} ${unit}`)
  }
  return parts.join(' · ')
}

function BlankList({ blank }: { blank: readonly string[] }) {
  if (blank.length === 0) return null
  return (
    <p role="note" className="text-sm text-muted-foreground">
      Resta in bianco nella lettera: {blank.join(', ')}.
    </p>
  )
}

/**
 * The block «Lettera e firma» of an engagement's page: the letter's number, its terms and its payment
 * rule, the server's warnings and the missing fields in bands, and the one action the state has. A
 * draft is previewed and created here (the free-text fields typed in a small form, the blanks the server
 * lists printed), then sent for signature; out for signature it waits, and the signature is recorded by
 * hand for now (the Documenso door is its own work). Closed and cancelled engagements are read-only.
 */
export function LetterBlock({
  engagement,
  onComplete,
}: {
  engagement: Engagement
  /** «Completa»: opens the edit dialog on the fields an import left out. */
  onComplete: () => void
}) {
  const preview = usePreviewLetter(engagement.id)
  const create = useCreateLetter(engagement.id)
  const change = useChangeEngagementState(engagement.id)
  const send = useSendEngagement(engagement.id)
  // Without the signing site the letter is sent by hand: the state is then moved by hand too.
  const unavailable = (send.error as { code?: string } | null)?.code === 'signing_unavailable'
  const [values, setValues] = useState<Record<string, string>>({})
  const draft = engagement.state === 'draft'
  const waitingSignature = engagement.state === 'awaiting_signature'
  const closed = engagement.state === 'concluded' || engagement.state === 'cancelled'
  const hasLetter = engagement.letter_document_id !== null && engagement.letter_document_id !== undefined
  const body = {
    variables: Object.fromEntries(
      Object.entries(values)
        .map(([name, value]) => [name, value.trim()] as const)
        .filter(([, value]) => value !== ''),
    ),
  }
  const missing = (engagement.waiting ?? []).filter(
    (waiting) => waiting.code === 'missing_days_per_week' || waiting.code === 'missing_client',
  )
  const warnings = engagement.warnings ?? []
  const problem = preview.error ?? create.error ?? change.error

  return (
    <section id="lettera" aria-labelledby="engagement-letter" className="space-y-4 border bg-card p-4">
      <h2 id="engagement-letter" className="text-lg font-semibold">
        Lettera e firma
      </h2>

      {!closed
        ? warnings.map((warning) => (
            <p
              key={`${warning.code}-${warning.engagement_ids.join(',')}`}
              role="note"
              className="border bg-paper px-3 py-2 text-sm"
            >
              {warning.message}
            </p>
          ))
        : null}
      {!closed && missing.length > 0 ? (
        <div
          role="note"
          className="flex flex-wrap items-center justify-between gap-3 border bg-paper px-3 py-2 text-sm"
        >
          <span>{missing.map((waiting) => waitingSentence(waiting)).join(' · ')}</span>
          <Button size="sm" variant="outline" onClick={onComplete}>
            Completa
          </Button>
        </div>
      ) : null}

      <dl className="grid gap-x-4 gap-y-2 text-sm sm:grid-cols-[minmax(0,9rem)_minmax(0,1fr)]">
        <dt className="text-muted-foreground">Lettera</dt>
        <dd className="tabular-nums">
          {engagement.letter_number === null ? 'Numero non ancora dato' : `n. ${engagement.letter_number}`}
          {hasLetter ? '' : ' · non ancora creata'}
        </dd>
        <dt className="text-muted-foreground">Termini</dt>
        <dd className="tabular-nums">{termsLine(engagement)}</dd>
        <dt className="text-muted-foreground">Pagamento</dt>
        <dd>
          {paymentRuleWords({
            payment_rule: engagement.payment_rule,
            payment_days: engagement.payment_days,
            payment_end_of_month: engagement.payment_end_of_month,
            collection_days: engagement.collection_days,
          }) || EMPTY}
        </dd>
      </dl>

      {draft && !hasLetter ? (
        <fieldset className="space-y-3">
          <legend className="text-sm font-medium">I campi liberi della lettera</legend>
          {FIELDS.map((field) => (
            <div key={field.name} className="space-y-1">
              <Label htmlFor={`letter-${field.name}`}>{field.label}</Label>
              {field.multiline ? (
                <Textarea
                  id={`letter-${field.name}`}
                  rows={2}
                  value={values[field.name] ?? ''}
                  onChange={(event) => setValues({ ...values, [field.name]: event.target.value })}
                />
              ) : (
                <Input
                  id={`letter-${field.name}`}
                  value={values[field.name] ?? ''}
                  onChange={(event) => setValues({ ...values, [field.name]: event.target.value })}
                />
              )}
            </div>
          ))}
        </fieldset>
      ) : null}

      {problem === null ? null : <QueryErrorBanner error={problem} />}

      {waitingSignature ? (
        <p className="text-sm">
          Aspetta la firma. La firma si registra a mano, per ora: quando la lettera è firmata, premi «Segna come
          firmata».
        </p>
      ) : null}
      {closed && !hasLetter ? <p className="text-sm text-muted-foreground">Nessuna lettera archiviata.</p> : null}

      <div className="flex flex-wrap gap-2">
        {draft ? (
          <Button variant="outline" disabled={preview.isPending} onClick={() => preview.mutate(body)}>
            {preview.isPending ? <Loader className="mr-2 size-4" /> : null}
            Anteprima
          </Button>
        ) : null}
        {draft && !hasLetter ? (
          <Button
            disabled={create.isPending}
            onClick={() =>
              create.mutate(body, { onSuccess: () => toast.success('Lettera creata.') })
            }
          >
            {create.isPending ? <Loader className="mr-2 size-4" /> : null}
            Crea la lettera
          </Button>
        ) : null}
        {hasLetter ? (
          <DownloadButton variant="outline" path={`/api/documents/${engagement.letter_document_id}/download`}>
            Apri il PDF
          </DownloadButton>
        ) : null}
        {draft && hasLetter ? (
          <Button
            disabled={send.isPending}
            onClick={() =>
              send.mutate(undefined, {
                onSuccess: (done) =>
                  toast.success(
                    done.mail_sent === false
                      ? 'Inviata, ma la mail non è partita: si può rimandare.'
                      : 'Inviata per la firma.',
                  ),
              })
            }
          >
            {send.isPending ? <Loader className="mr-2 size-4" /> : null}
            Invia per la firma
          </Button>
        ) : null}
        {draft && hasLetter && unavailable ? (
          <Button
            variant="outline"
            disabled={change.isPending}
            onClick={() =>
              change.mutate(
                { state: 'awaiting_signature' },
                { onSuccess: () => toast.success('Lettera segnata come inviata, a mano.') },
              )
            }
          >
            Segna come inviata (a mano)
          </Button>
        ) : null}
        {draft && hasLetter && send.isError ? (
          <p role="alert" className="w-full text-sm text-destructive">
            {toProblem(send.error).detail}
          </p>
        ) : null}
        {waitingSignature ? (
          <Button
            disabled={change.isPending}
            onClick={() =>
              change.mutate(
                { state: 'active' },
                { onSuccess: () => toast.success('Lettera segnata come firmata.') },
              )
            }
          >
            {change.isPending ? <Loader className="mr-2 size-4" /> : null}
            Segna come firmata
          </Button>
        ) : null}
      </div>

      {create.data === undefined ? null : <BlankList blank={create.data.blank} />}
      {preview.data === undefined ? null : (
        <div className="space-y-2">
          <h3 className="text-sm font-medium">Anteprima della lettera</h3>
          {preview.data.draft ? (
            <p role="note" className="text-sm text-muted-foreground">
              Il testo della lettera è ancora una bozza: non può essere inviato.
            </p>
          ) : null}
          <BlankList blank={preview.data.blank} />
          <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words border bg-paper p-3 text-sm">
            {preview.data.markdown}
          </pre>
        </div>
      )}
    </section>
  )
}
