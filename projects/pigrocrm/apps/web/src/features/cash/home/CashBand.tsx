import { cn } from '@rebase/ui/cn'
import { ALERT_DOT, cashAlertSentence, type CashAlert } from './alertSentence'

const DOT = { danger: 'bg-watermelon', gold: 'bg-royal-gold' } as const

/**
 * The one-line band of a cash alert: a dot (`danger` for a balance below zero, `gold` for a
 * statement that is late) and the sentence, which carries the meaning without the colour.
 * Shared by the phone Home and the schedule.
 */
export function CashBand({ alert, today, className }: { alert: CashAlert; today: string; className?: string }) {
  return (
    <div
      role="status"
      className={cn('flex items-start gap-2 border border-border bg-card px-3 py-2 text-sm', className)}
    >
      <span aria-hidden className={cn('mt-1.5 size-2 shrink-0 rounded-full', DOT[ALERT_DOT[alert.kind]])} />
      <span className="min-w-0">{cashAlertSentence(alert, today)}</span>
    </div>
  )
}
