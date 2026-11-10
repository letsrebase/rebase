import { cn } from '@rebase/ui/cn'
import { formatMoneyValue } from '@/features/time/columns'
import { CASH_LABELS, MONTHS_PLACEHOLDER, formatDay, missingReason, netItems } from './labels'
import type { MemberMonth } from './queries'

/** Every amount says what it is: «Stima» (dotted underline) or «Cedolino» (ink weight). */
export function Figure({ value, from }: { value: string; from: MemberMonth['source'] }) {
  return (
    <span className="inline-flex flex-col items-end">
      <span className={cn('tabular-nums', from === 'payslip' && 'font-semibold')}>{formatMoneyValue(value)}</span>
      <span
        className={cn(
          'text-xs',
          from === 'payslip' ? 'font-medium text-foreground' : 'text-muted-foreground underline decoration-dotted underline-offset-2',
        )}
      >
        {from === 'payslip' ? 'Cedolino' : 'Stima'}
      </span>
    </span>
  )
}

/** Where nothing can be said: a dash and the reason the server gave. */
export function Absent({ reason }: { reason: string | null }) {
  return (
    <span className="inline-flex flex-col items-end text-muted-foreground">
      <span>{MONTHS_PLACEHOLDER}</span>
      {reason === null ? null : <span className="max-w-56 whitespace-normal text-right text-xs">{reason}</span>}
    </span>
  )
}

/** What leaves for a person: each net payment with its date, or the dash and its reason. */
export function NetCell({ month }: { month: MemberMonth | undefined }) {
  const items = netItems(month)
  if (month === undefined || items.length === 0) return <Absent reason={month === undefined ? null : missingReason(month)} />
  return (
    <span className="inline-flex flex-col items-end gap-1">
      {items.map((item) => (
        <span key={`${item.kind}-${item.due_date}`} className="inline-flex flex-col items-end">
          <Figure value={item.amount} from={month.source} />
          <span className="text-xs text-muted-foreground">
            {item.kind === 'extra_pay' ? `${CASH_LABELS.extra_pay}, ` : ''}il {formatDay(item.due_date)}
          </span>
        </span>
      ))}
    </span>
  )
}
