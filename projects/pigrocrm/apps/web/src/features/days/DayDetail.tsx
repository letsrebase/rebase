import { Link } from '@tanstack/react-router'
import { CalendarCheck, FileText } from 'lucide-react'
import { useState } from 'react'
import { PageHeader } from '@/components/PageHeader'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@rebase/ui/dialog'
import { Label } from '@rebase/ui/label'
import { Loader } from '@rebase/ui/loader'
import { toast } from '@rebase/ui/sonner'
import { Textarea } from '@rebase/ui/textarea'
import { formatOccurredAt } from '@/components/activityLabels'
import { toProblem } from '@/lib/api'
import { useCanWrite } from '@/lib/auth'
import { formatIsoDateItalian } from '@/lib/dates'
import { DayStatePill } from './DayStatePill'
import {
  ACTIONS,
  actorLabel,
  quantitaLabel,
  statoLabel,
  type WorkUnitDetail,
  type WorkUnitStato,
} from './labels'
import { useMoveDay, useWorkUnit } from './queries'

/**
 * `/app/days/$workUnitId`: one day, with its state, the approval it rests on, the log of
 * every move it made, and the moves its state allows.
 *
 * The buttons are the API's own list (`azioni`): the state graph lives in the database and
 * the service, and the page never carries a copy of it that could drift. A move asks for a
 * reason where the move is a judgement (contesting, withdrawing, writing off) and sends a
 * plain one where the act is the reason; the reason lands in the log beside who made the
 * move and when, which is what a dispute is later argued from.
 */
export function DayDetail({ workUnitId }: { workUnitId: string }) {
  const day = useWorkUnit(workUnitId)
  if (day.isError) {
    if (toProblem(day.error).status === 404) return <p className="p-8">Giornata non trovata.</p>
    return (
      <div className="p-8">
        <QueryErrorBanner error={day.error} />
      </div>
    )
  }
  if (!day.data) {
    return (
      <div role="status" className="flex items-center gap-2 p-8 text-muted-foreground">
        <Loader className="size-4" />
        Caricamento…
      </div>
    )
  }
  return <Loaded day={day.data} />
}

function Loaded({ day }: { day: WorkUnitDetail }) {
  const canWrite = useCanWrite()
  const move = useMoveDay(day.id)
  const [pending, setPending] = useState<WorkUnitStato | null>(null)
  const [reason, setReason] = useState('')
  const spec = pending === null ? undefined : ACTIONS[pending]
  const needsReason = spec !== undefined && spec.defaultReason === null
  // Only the moves the page knows how to word: a state the API offers that has no entry in
  // `ACTIONS` is not drawn as a button with a raw state name on it.
  const offered = day.azioni.filter((stato) => ACTIONS[stato] !== undefined)

  function close() {
    if (move.isPending) return
    move.reset()
    setPending(null)
    setReason('')
  }

  function confirm() {
    if (pending === null || spec === undefined) return
    const motivo = reason.trim() !== '' ? reason.trim() : spec.defaultReason
    if (motivo === null) return
    move.mutate(
      { stato: pending, motivo },
      {
        onSuccess: (next) => {
          toast.success(`Giornata: ${statoLabel(next.stato).toLowerCase()}`)
          setPending(null)
          setReason('')
        },
      },
    )
  }

  return (
    <>
      <PageHeader
        icon={CalendarCheck}
        title={`Giornata del ${formatIsoDateItalian(day.data)}`}
        description={`${day.contract_titolo}, ${day.customer_nome}`}
        actions={
          canWrite && offered.length > 0 ? (
            <>
              {offered.map((stato) => {
                const action = ACTIONS[stato]
                if (action === undefined) return null
                return (
                  <Button
                    key={stato}
                    variant={action.tone}
                    onClick={() => {
                      move.reset()
                      setPending(stato)
                    }}
                  >
                    {action.label}
                  </Button>
                )
              })}
            </>
          ) : undefined
        }
      />

      <div className="max-w-3xl space-y-8 px-4 pb-10 sm:px-8">
        <p className="flex flex-wrap gap-x-4 text-sm text-muted-foreground">
          <Link to="/app/days" search={{ contract: day.contract_id }} className="underline underline-offset-4">
            Tutte le giornate del contratto
          </Link>
          <Link
            to="/app/customers/$customerId"
            params={{ customerId: day.customer_id }}
            className="underline underline-offset-4"
          >
            {day.customer_nome}
          </Link>
        </p>

        <section aria-label="La giornata">
          <dl className="border-t text-sm">
            <Row label="Stato">
              <DayStatePill stato={day.stato} />
            </Row>
            <Row label="Quanto">{quantitaLabel(day.quantita)}</Row>
            <Row label="Cosa è stato fatto">{day.descrizione}</Row>
            {day.note ? <Row label="Note">{day.note}</Row> : null}
          </dl>
        </section>

        {day.stato === 'lavorato_senza_approvazione' ? (
          <Notice>
            Il contratto richiede l’approvazione preventiva del cliente e a questa giornata non è
            collegata nessuna. Resta «{statoLabel('lavorato_senza_approvazione')}» finché non ne arriva una.
          </Notice>
        ) : null}
        {day.fuori_termine ? (
          <Notice>
            {day.fuori_termine === 'prima_dell_inizio'
              ? `La data precede l’inizio del contratto, il ${formatIsoDateItalian(day.contratto_inizio)}.`
              : `La data è dopo la fine del contratto, il ${day.contratto_fine ? formatIsoDateItalian(day.contratto_fine) : ''}.`}
          </Notice>
        ) : null}

        <Approval day={day} />
        <History day={day} />
      </div>

      <Dialog open={pending !== null} onOpenChange={(open) => (open ? undefined : close())}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{spec?.title}</DialogTitle>
            <DialogDescription>{spec?.description}</DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="day-reason">{needsReason ? 'Motivo' : 'Motivo (facoltativo)'}</Label>
            <Textarea
              id="day-reason"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder={needsReason ? 'Cosa è successo' : (spec?.defaultReason ?? '')}
              rows={3}
            />
          </div>
          {move.error ? <QueryErrorBanner error={move.error} /> : null}
          <DialogFooter>
            <Button variant="outline" disabled={move.isPending} onClick={close}>
              Annulla
            </Button>
            <Button
              variant={spec?.tone === 'destructive' ? 'destructive' : 'default'}
              disabled={move.isPending || (needsReason && reason.trim() === '')}
              onClick={confirm}
            >
              {move.isPending ? <Loader className="mr-2 size-4" /> : null}
              {spec?.label}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1 border-b py-2 sm:flex-row sm:justify-between sm:gap-6">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="font-medium sm:text-right">{children}</dd>
    </div>
  )
}

function Notice({ children }: { children: React.ReactNode }) {
  return <p className="border border-muted bg-muted/40 px-3 py-2 text-sm">{children}</p>
}

/**
 * The approval the day rests on, and the document that proves it. An approval is immutable
 * evidence (a correction is a new one), so this only reads it: who sent it, when it was
 * received, the verbatim excerpt, and a way into the archived original.
 */
function Approval({ day }: { day: WorkUnitDetail }) {
  const approval = day.approval
  return (
    <section aria-labelledby="day-approval" className="space-y-3">
      <h2 id="day-approval" className="font-semibold">
        Approvazione
      </h2>
      {approval === null ? (
        <p className="text-sm text-muted-foreground">
          {day.requires_prior_approval
            ? 'Nessuna approvazione collegata, e il contratto ne richiede una.'
            : 'Nessuna approvazione collegata. Il contratto non la richiede.'}
        </p>
      ) : (
        <>
          <p className="text-sm text-muted-foreground">
            Ricevuta il {formatOccurredAt(approval.ricevuto_il)} da {approval.mittente}, per{' '}
            {approval.canale.replace('_', ' ')}.
          </p>
          <blockquote className="whitespace-pre-line border-l-2 border-foreground pl-4 text-sm">
            {approval.estratto}
          </blockquote>
          <Button asChild variant="outline" size="sm">
            <Link to="/app/documents/$documentId" params={{ documentId: approval.document_id }}>
              <FileText className="mr-2 size-4" aria-hidden />
              {approval.documento_titolo ?? 'Apri il documento'}
            </Link>
          </Button>
        </>
      )}
    </section>
  )
}

/** Every move the day made, oldest first as the log is written, each with when, by whom and why. */
function History({ day }: { day: WorkUnitDetail }) {
  return (
    <section aria-labelledby="day-history" className="space-y-3">
      <h2 id="day-history" className="font-semibold">
        Cronologia
      </h2>
      <ol className="border-t">
        {day.transizioni.map((step) => (
          <li key={step.id} className="space-y-1 border-b py-3 text-sm">
            <p className="flex flex-wrap items-center gap-x-2 gap-y-1">
              {step.stato_precedente === null ? (
                <span className="font-medium">Registrata come {statoLabel(step.stato_nuovo).toLowerCase()}</span>
              ) : (
                <span className="font-medium">
                  {statoLabel(step.stato_precedente)} <span aria-hidden="true">→</span>
                  <span className="sr-only"> passa a </span> {statoLabel(step.stato_nuovo)}
                </span>
              )}
            </p>
            <p className="text-muted-foreground">
              <time dateTime={step.created_at}>{formatOccurredAt(step.created_at)}</time>, {actorLabel(step.attore).toLowerCase()}
            </p>
            <p>{step.motivo}</p>
          </li>
        ))}
      </ol>
    </section>
  )
}
