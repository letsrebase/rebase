import { useState } from 'react'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { StatusPill } from '@/components/StatusPill'
import { Button } from '@rebase/ui/button'
import { Loader } from '@rebase/ui/loader'
import { toast } from '@rebase/ui/sonner'
import { ContestDialog } from './ContestDialog'
import { AmountText, PaymentItem } from './MonthParts'
import { HIDDEN } from './words'
import { RecordArrivalDialog } from './RecordArrivalDialog'
import {
  REPORT_LABELS,
  REPORT_TONES,
  formatDate,
  formatQuantity,
  monthLabel,
  type Engagement,
  type ReportDay,
} from './labels'
import { awaitsArrival, awaitsDecision, type MonthRow } from './monthRows'
import { useApproveMonthlyReport } from './queries'

/** The panel lists this many days and then offers «Mostra altri». */
const DAYS_PAGE = 50

function DayRow({ day }: { day: ReportDay }) {
  return (
    <li className="grid grid-cols-[5.5rem_3rem_minmax(0,1fr)] items-baseline gap-x-2 px-3 py-2 text-sm">
      <span className="tabular-nums">{formatDate(day.date)}</span>
      <span className="text-right tabular-nums">{formatQuantity(day.quantity)}</span>
      <span className="min-w-0">
        <span
          className="block truncate text-muted-foreground max-sm:whitespace-normal"
          title={day.description}
        >
          {day.description}
        </span>
        {day.held ? <span className="mt-1 inline-block border px-1.5 text-xs">Trattenuto</span> : null}
        {!day.counts || !day.in_term ? (
          <span className="mt-1 inline-block text-xs text-muted-foreground">
            {day.in_term ? 'Non conteggiato' : 'Fuori dal periodo'}
          </span>
        ) : null}
      </span>
    </li>
  )
}

/**
 * One month of the engagement, in the 330 px panel (decision 2 A): the «Rendiconto» with its dates, the
 * two verbs of the decision (3 B), the days with the held ones named, the objection, and the month's
 * payments. «Approva il mese» is one click with an empty body; «Contesta dei giorni…» opens the dialog.
 * Both exist only for a person: this screen is the only place a month is decided. After the tacit date
 * no new hold is possible, so «Contesta» is gone with the decision itself. Nothing is computed here: the
 * dates, the quantities and the amounts are the server's.
 */
export function MonthPanel({
  engagement,
  row,
  days,
  daysAvailable,
  today,
}: {
  engagement: Engagement
  row: MonthRow
  days: readonly ReportDay[]
  daysAvailable: boolean
  today: string
}) {
  const approve = useApproveMonthlyReport(engagement.id)
  const [dialog, setDialog] = useState<'arrival' | 'contest' | null>(null)
  const [shown, setShown] = useState(DAYS_PAGE)
  const monthly = row.monthly
  const decidable = engagement.state === 'active' || engagement.state === 'concluded'
  const monthDays = days.filter((day) => day.date.startsWith(row.key))
  const heldDays = monthly?.held_dates ?? []

  return (
    <div className="space-y-6 p-4">
      <header>
        <h2 className="text-lg font-semibold first-letter:uppercase">{monthLabel(row.month)}</h2>
      </header>

      <section aria-label="Rendiconto" className="space-y-2 text-sm">
        <h3 className="font-medium">Rendiconto</h3>
        <StatusPill tone={REPORT_TONES[row.state]}>{REPORT_LABELS[row.state]}</StatusPill>
        {monthly === null ? (
          <p className="text-muted-foreground">
            {row.ended ? 'Il rendiconto di questo mese non è stato registrato.' : 'Il mese non è ancora finito.'}
          </p>
        ) : (
          <dl className="grid grid-cols-[minmax(0,7rem)_minmax(0,1fr)] gap-x-3 gap-y-1">
            <dt className="text-muted-foreground">Arrivato il</dt>
            <dd className="tabular-nums">{formatDate(monthly.received_on)}</dd>
            {monthly.effective_state === 'approved' ? (
              <>
                <dt className="text-muted-foreground">Deciso</dt>
                <dd>
                  {[
                    monthly.decided_by_name ? `da ${monthly.decided_by_name}` : null,
                    monthly.decided_on === null ? null : `il ${formatDate(monthly.decided_on)}`,
                  ]
                    .filter((part): part is string => part !== null)
                    .join(' ') || 'Sì'}
                </dd>
              </>
            ) : (
              <>
                <dt className="text-muted-foreground">Obiezioni entro</dt>
                <dd className="tabular-nums">{formatDate(monthly.tacit_on)}</dd>
              </>
            )}
          </dl>
        )}
        {monthly?.effective_state === 'tacit' ? (
          <p className="text-muted-foreground">
            Nessuna obiezione entro il {formatDate(monthly.tacit_on)}: non si può più trattenere un giorno.
          </p>
        ) : null}
      </section>

      {decidable && (awaitsArrival(row) || awaitsDecision(row)) ? (
        <section aria-label="Decisione" className="space-y-2">
          {approve.isError ? <QueryErrorBanner error={approve.error} /> : null}
          {awaitsArrival(row) ? (
            <Button className="w-full" onClick={() => setDialog('arrival')}>
              Registra l'arrivo
            </Button>
          ) : (
            <div className="flex flex-col gap-2">
              <Button
                disabled={approve.isPending}
                onClick={() =>
                  approve.mutate(
                    { month: row.key, body: {} },
                    { onSuccess: () => toast.success('Mese approvato.') },
                  )
                }
              >
                {approve.isPending ? <Loader className="mr-2 size-4" /> : null}
                Approva il mese
              </Button>
              <Button
                variant="outline"
                disabled={approve.isPending || !daysAvailable}
                onClick={() => setDialog('contest')}
              >
                Contesta dei giorni…
              </Button>
            </div>
          )}
        </section>
      ) : null}

      {heldDays.length > 0 ? (
        <section aria-label="Giorni trattenuti" className="space-y-2 text-sm">
          <h3 className="font-medium">Giorni trattenuti</h3>
          <p className="tabular-nums">{heldDays.map((date) => formatDate(date)).join(', ')}</p>
          {monthly?.objection ? (
            <p className="whitespace-pre-wrap break-words">
              <span className="text-muted-foreground">Motivo: </span>
              {monthly.objection}
            </p>
          ) : null}
        </section>
      ) : null}

      <section aria-label="Giorni" className="space-y-2">
        <h3 className="text-sm font-medium">Giorni</h3>
        {!daysAvailable ? (
          <p className="text-sm text-muted-foreground">I giorni non sono disponibili.</p>
        ) : monthDays.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nessun giorno registrato in questo mese.</p>
        ) : (
          <>
            <ul className="divide-y border">
              {monthDays.slice(0, shown).map((day) => (
                <DayRow key={`${day.date}-${day.source}-${day.description}`} day={day} />
              ))}
            </ul>
            {monthDays.length > shown ? (
              <Button variant="ghost" size="sm" onClick={() => setShown(shown + DAYS_PAGE)}>
                Mostra altri
              </Button>
            ) : null}
          </>
        )}
      </section>

      <section aria-label="Pagamenti" className="space-y-3">
        <h3 className="text-sm font-medium">Pagamenti</h3>
        {row.payments.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {row.state === 'approved' || row.state === 'tacit'
              ? 'Nessun pagamento per questo mese, per ora.'
              : "Pagabile: - (dopo l'approvazione)"}
          </p>
        ) : (
          <ul className="space-y-3">
            {row.payments.map((payment) => (
              <li key={payment.due_item_id} className="border p-3 text-sm">
                <div className="flex items-start justify-between gap-3">
                  <PaymentItem payment={payment} today={today} />
                  {engagement.redacted ? (
                    <span className="text-muted-foreground">{HIDDEN}</span>
                  ) : (
                    <AmountText payment={payment} />
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
        <p className="text-xs text-muted-foreground">Ritenuta e netto: da definire</p>
      </section>

      {dialog === 'arrival' ? (
        <RecordArrivalDialog engagementId={engagement.id} month={row.key} onClose={() => setDialog(null)} />
      ) : null}
      {dialog === 'contest' ? (
        <ContestDialog
          engagementId={engagement.id}
          month={row.key}
          days={monthDays}
          onClose={() => setDialog(null)}
        />
      ) : null}
    </div>
  )
}
