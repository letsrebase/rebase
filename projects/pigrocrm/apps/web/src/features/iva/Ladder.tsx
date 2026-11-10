import { StatusPill } from '@/components/StatusPill'
import { NOT_COMPUTABLE } from '@/features/analytics/format'
import { formatMoneyValue } from '@/features/time/columns'
import { formatIsoDateItalian } from '@/lib/dates'
import { OUTCOME_LABELS, PACK_SOURCE, periodPill } from './labels'
import type { IvaLadder, IvaPeriod } from './queries'

/** `"0.00"` is what `Numeric(12, 2)` sends for zero, so a line that is zero is told by the string. */
const ZERO = '0.00'

function Line({
  label,
  amount,
  strong = false,
}: {
  label: string
  amount: string
  strong?: boolean
}) {
  return (
    <div
      className={
        strong
          ? 'flex items-baseline justify-between gap-4 border-t-2 border-foreground px-4 py-3 font-semibold'
          : 'flex items-baseline justify-between gap-4 border-t px-4 py-2 text-sm first:border-t-0'
      }
    >
      <dt>{label}</dt>
      <dd className="whitespace-nowrap tabular-nums">{formatMoneyValue(amount)}</dd>
    </div>
  )
}

/** Why a result is carried instead of paid: the pack's own limit, shown as it sent it. */
function carriedReason(limit: string | null): string {
  return limit === null
    ? 'Il debito è sotto il limite del pacchetto fiscale: si versa con il periodo successivo.'
    : `Il debito non supera ${formatMoneyValue(limit)} (limite del pacchetto fiscale): si versa con il periodo successivo.`
}

function Figure({ period, ladder }: { period: IvaPeriod; ladder: IvaLadder | null }) {
  if (ladder === null) {
    return (
      <>
        <p className="text-sm text-muted-foreground">{period.label}</p>
        <p className="text-2xl font-semibold text-muted-foreground">{NOT_COMPUTABLE}</p>
        <p className="text-sm text-muted-foreground">
          {period.state === 'future'
            ? 'Il periodo non è ancora iniziato.'
            : 'Dipende dal periodo precedente, che non è ancora stato liquidato.'}
        </p>
      </>
    )
  }
  return (
    <>
      <p className="text-sm text-muted-foreground">
        {OUTCOME_LABELS[ladder.outcome]} · {period.label}
      </p>
      <p className="text-2xl font-semibold tabular-nums">{formatMoneyValue(ladder.result)}</p>
      {period.state === 'open' && (
        <p className="text-sm text-muted-foreground">
          In corso: il risultato cambia finché il periodo non è finito.
        </p>
      )}
      {ladder.outcome === 'carried' && (
        <p className="text-sm text-muted-foreground">{carriedReason(period.carry_forward_limit)}</p>
      )}
      {ladder.outcome === 'due' && period.payment_due_on !== null && (
        <p className="text-sm text-muted-foreground">
          Scadenza del versamento: {formatIsoDateItalian(period.payment_due_on)} · {PACK_SOURCE}
        </p>
      )}
    </>
  )
}

/**
 * The settlement ladder of a period (brief «IVA»): IVA on the sales, less the deductible IVA on the
 * purchases, less the credit carried in, then the result in bold. Every figure is the server's
 * string; a line the settlement does not give (a reverse charge, a debit carried in, the quarterly
 * surcharge) is not drawn when it is zero. A period with no ladder says «non calcolabile» and why.
 */
export function Ladder({ period }: { period: IvaPeriod }) {
  const ladder = period.ladder
  const pill = periodPill(period)
  return (
    <section aria-label="Liquidazione" className="border bg-card">
      <div className="space-y-1 p-4">
        <Figure period={period} ladder={ladder} />
        <div className="pt-2">
          <StatusPill tone={pill.tone}>{pill.label}</StatusPill>
        </div>
      </div>
      {ladder !== null && (
        <dl className="border-t">
          <Line label="IVA sulle vendite" amount={ladder.sales_vat} />
          {ladder.reverse_charge_vat !== ZERO && (
            <Line label="IVA dovuta per inversione contabile" amount={ladder.reverse_charge_vat} />
          )}
          <Line label="Meno IVA detraibile sugli acquisti" amount={ladder.purchase_vat} />
          <Line label="Meno credito del periodo precedente" amount={ladder.credit_in} />
          {ladder.debit_in !== ZERO && (
            <Line label="Più debito del periodo precedente" amount={ladder.debit_in} />
          )}
          {ladder.surcharge !== ZERO && (
            <Line label="Più maggiorazione trimestrale" amount={ladder.surcharge} />
          )}
          <Line label={OUTCOME_LABELS[ladder.outcome]} amount={ladder.result} strong />
        </dl>
      )}
    </section>
  )
}
