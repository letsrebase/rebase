import { StatusPill } from '@/components/StatusPill'
import { cn } from '@rebase/ui/cn'
import {
  EMPTY,
  PAYMENT_LABELS,
  PAYMENT_TONES,
  REPORT_LABELS,
  REPORT_TONES,
  formatDate,
  formatMoney,
  formatQuantity,
  type EngagementPayment,
} from './labels'
import { lateWords, paymentView, type MonthRow } from './monthRows'
import { HIDDEN, waitingWords } from './words'

/** The pill of a month's «Rendiconto» over its sentence: the tacit date, or who decided and when. */
export function ReportCell({ row }: { row: MonthRow }) {
  const monthly = row.monthly
  let line: string | null = null
  if (monthly !== null) {
    if (monthly.effective_state === 'approved') {
      const who = monthly.decided_by_name
      const when = monthly.decided_on === null ? null : formatDate(monthly.decided_on)
      line = [who === null || who === undefined ? null : `da ${who}`, when === null ? null : `il ${when}`]
        .filter((part): part is string => part !== null)
        .join(' ')
      if (line === '') line = null
    } else {
      line = `Nessuna obiezione entro il ${formatDate(monthly.tacit_on)}`
    }
  }
  return (
    <div className="space-y-1">
      <StatusPill tone={REPORT_TONES[row.state]}>{REPORT_LABELS[row.state]}</StatusPill>
      {line === null ? null : <p className="text-xs text-muted-foreground">{line}</p>}
    </div>
  )
}

/**
 * The days (or hours) of a month, as the report counts them, with the held share the server states.
 * «-» when the report is not available, or when the letter is a flat fee (the report prices none).
 */
export function QuantityCell({ row, flat, available }: { row: MonthRow; flat: boolean; available: boolean }) {
  if (!available || flat || row.report === null) return <span className="text-muted-foreground">-</span>
  const held = row.monthly !== null && row.monthly.held_dates.length > 0
  return (
    <div className="text-right tabular-nums">
      <span>{formatQuantity(row.report.quantity)}</span>
      {held ? (
        <p className="text-xs text-muted-foreground">di cui {formatQuantity(row.report.held_quantity)} trattenuti</p>
      ) : null}
    </div>
  )
}

/** A forecast amount is lighter and dotted-underlined, so it never reads as an expected one. */
export function AmountText({ payment }: { payment: EngagementPayment }) {
  const forecast = payment.state === 'forecast'
  return (
    <span
      className={cn(
        'tabular-nums',
        forecast && 'text-muted-foreground underline decoration-dotted underline-offset-4',
      )}
      title={forecast ? 'Previsto: non è ancora una fattura' : undefined}
    >
      {formatMoney(payment.amount)}
    </span>
  )
}

/**
 * The amount of a month: each payment item's amount (lighter when a forecast), else the report's own
 * `payable_amount`. «Nascosto» when the client is not in the reader's view, whatever the server sent.
 */
export function AmountCell({ row, redacted }: { row: MonthRow; redacted: boolean }) {
  if (redacted) return <span className="block text-right text-muted-foreground">{HIDDEN}</span>
  if (row.payments.length > 0) {
    return (
      <div className="space-y-1 text-right">
        {row.payments.map((payment) => (
          <div key={payment.due_item_id}>
            <AmountText payment={payment} />
          </div>
        ))}
      </div>
    )
  }
  const amount = row.report?.payable_amount ?? null
  if (amount === null) return <span className="block text-right text-muted-foreground">-</span>
  return <span className="block text-right tabular-nums">{formatMoney(amount)}</span>
}

/** One payment item as a pill over its date and its reason: «circa 30/12/2026 · in attesa dell'incasso di …». */
export function PaymentItem({ payment, today }: { payment: EngagementPayment; today: string }) {
  const view = paymentView(payment, today)
  const date =
    payment.due_date === null ? null : `${payment.due_date_estimated ? 'circa ' : ''}${formatDate(payment.due_date)}`
  const waiting = waitingWords(payment)
  const sentence = [date, payment.description].filter((part): part is string => part !== null && part !== '')
  return (
    <div className="space-y-1">
      <StatusPill tone={PAYMENT_TONES[view]}>
        {view === 'late' ? lateWords(payment, today) : PAYMENT_LABELS[view]}
      </StatusPill>
      {sentence.length === 0 ? null : (
        <p className="text-xs text-muted-foreground tabular-nums">{sentence.join(' · ')}</p>
      )}
      {waiting === null ? null : <p className="text-xs text-muted-foreground">{waiting}</p>}
    </div>
  )
}

/**
 * «Pagamento al freelance» of a month: its payment items, or, with none, «Pagabile: -» and
 * «dopo l'approvazione» while the month has no approved «Rendiconto» (the brief's Payment row).
 */
export function PaymentCell({ row, today }: { row: MonthRow; today: string }) {
  if (row.payments.length > 0) {
    return (
      <div className="space-y-2">
        {row.payments.map((payment) => (
          <PaymentItem key={payment.due_item_id} payment={payment} today={today} />
        ))}
      </div>
    )
  }
  if (row.state === 'approved' || row.state === 'tacit') {
    return <span className="text-muted-foreground">{EMPTY}</span>
  }
  return (
    <div className="space-y-1">
      <p className="text-sm">Pagabile: -</p>
      <p className="text-xs text-muted-foreground">dopo l'approvazione</p>
    </div>
  )
}
