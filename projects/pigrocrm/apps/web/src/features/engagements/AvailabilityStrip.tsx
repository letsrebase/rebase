import { cn } from '@rebase/ui/cn'
import { LoadingState } from '@/components/LoadingState'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { useFreelancerAvailability } from './queries'
import {
  AVAILABILITY_LABELS,
  formatDate,
  formatQuantity,
  type AvailabilityWeek,
} from './labels'

/** «02/11» from `2026-11-02`: the week's Monday under its cell, as short as the room allows. */
function shortMonday(iso: string): string {
  const [, month, day] = iso.split('-')
  return month === undefined || day === undefined ? iso : `${day}/${month}`
}

/**
 * What a week's cell says for a screen reader: its Monday in full, the word for its status and the
 * days committed. The word is what makes a week readable without its colour or its hatching.
 */
function weekDescription(week: AvailabilityWeek): string {
  const label = AVAILABILITY_LABELS[week.status]
  const days =
    week.status === 'unknown'
      ? ''
      : `, ${formatQuantity(week.committed_days)} ${week.committed_days === '1' || week.committed_days === '1.0' ? 'giorno' : 'giorni'}`
  return `Settimana del ${formatDate(week.starts_on)}: ${label}${days}`
}

/**
 * A freelancer's availability as a strip (the approved brief, question 6 A): one cell a week, the
 * committed days as the figure, «?» hatched for a week that cannot be said, the week's Monday under
 * each cell. A week is a word and a figure, never colour alone: a committed week reads its days, a
 * free one «0», and an unknown one «?» with the hatching, so «sconosciuta» never looks like «libera».
 * The strip draws what the server answered and adds nothing up.
 */
export function AvailabilityStrip({
  weeks,
  className,
}: {
  weeks: readonly AvailabilityWeek[]
  className?: string
}) {
  return (
    <div className={cn('space-y-2', className)}>
      <ol
        aria-label="Disponibilità, una cella a settimana"
        className="grid grid-cols-6 gap-1 sm:grid-cols-12"
      >
        {weeks.map((week) => (
          <li key={week.week} data-status={week.status} title={weekDescription(week)} className="min-w-0">
            <div
              role="img"
              aria-label={weekDescription(week)}
              className={cn(
                'flex h-10 items-center justify-center border text-sm font-medium tabular-nums',
                week.status === 'committed' && 'bg-foreground text-background',
                week.status === 'free' && 'bg-background text-muted-foreground',
                week.status === 'unknown' &&
                  'bg-[repeating-linear-gradient(135deg,var(--border)_0,var(--border)_1px,transparent_1px,transparent_0.375rem)] text-foreground',
              )}
            >
              {week.status === 'unknown' ? '?' : formatQuantity(week.committed_days)}
            </div>
            <div aria-hidden="true" className="mt-1 text-center text-xs tabular-nums text-muted-foreground">
              {shortMonday(week.starts_on)}
            </div>
          </li>
        ))}
      </ol>
      <p className="text-xs text-muted-foreground">
        Giorni impegnati a settimana, con il lunedì sotto. 0 è libera; ? è sconosciuta: un incarico
        della settimana non ha i giorni a settimana.
      </p>
    </div>
  )
}

/**
 * The strip of one freelancer, read by the server from the live engagements (the draft, out for
 * signature and active ones, the same three the overlap warning counts). `excludingEngagementId`
 * leaves one out, as an engagement's own page does for its draft; `dateFrom` starts the strip on
 * the week of that day. The strip has its own loading and error states, so the page around it stays.
 */
export function FreelancerAvailability({
  freelancerId,
  dateFrom,
  excludingEngagementId,
  className,
}: {
  freelancerId: string | null
  dateFrom?: string
  excludingEngagementId?: string
  className?: string
}) {
  const query = useFreelancerAvailability({ freelancerId, dateFrom, excludingEngagementId })
  if (freelancerId === null) {
    return (
      <p className={cn('text-sm text-muted-foreground', className)}>
        Scegli il freelance per vedere quando è libero.
      </p>
    )
  }
  if (query.isPending) return <LoadingState label="Leggo la disponibilità…" className={className} />
  if (query.isError) return <QueryErrorBanner error={query.error} onRetry={() => void query.refetch()} />
  return <AvailabilityStrip weeks={query.data.weeks} className={className} />
}
