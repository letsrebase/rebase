import type { UseQueryResult } from '@tanstack/react-query'
import { Badge } from '@rebase/ui/badge'
import type { TalentAvailability, TalentCommitments } from '@/lib/api'
import { failureMessage } from '@/lib/adminList'
import { availabilityOf } from '@/lib/availability'
import { formatDay } from '@/lib/format'

/** «libera dal 12 gen 2027», «impegnata dal 7 dic 2026»: when this week's status changes, or
 *  that it does not before the end of what the CRM was asked about. */
function nextChange(availability: TalentAvailability): string | null {
  if (availability.status === 'free') {
    return availability.committed_from ? `impegnata dal ${formatDay(availability.committed_from)}` : null
  }
  return availability.free_from
    ? `libera dal ${formatDay(availability.free_from)}`
    : `nessuna settimana libera fino al ${formatDay(availability.until)}`
}

/** One card's availability: this week, then when it changes. A committed week reads its days, an
 *  unknown one says so in words, so «sconosciuta» never reads as free. */
export function AvailabilityText({ availability }: { availability: TalentAvailability }) {
  const change = nextChange(availability)
  // «3», «2,5» from the CRM's `"3.0"`, `"2.5"`.
  const days = Number(availability.committed_days).toLocaleString('it-IT', { maximumFractionDigits: 1 })
  return (
    <div className="space-y-1">
      {availability.status === 'committed' ? (
        <Badge variant="pill" dot="ink">{`Impegnata ${days} gg/sett.`}</Badge>
      ) : availability.status === 'unknown' ? (
        <Badge
          variant="pill"
          dot="muted"
          title="Un incarico di questa settimana non ha i giorni a settimana nel CRM"
        >
          Sconosciuta
        </Badge>
      ) : (
        <p className="text-muted-foreground">Libera</p>
      )}
      {change && <p className="text-xs text-muted-foreground">{change}</p>}
    </div>
  )
}

/** The cell of one card on a list: what the CRM said, «…» while it answers, «—» when it could
 *  not (the list's own note says why). */
export function AvailabilityCell({
  query,
  freelancerId,
}: {
  query: UseQueryResult<TalentCommitments>
  freelancerId: string
}) {
  if (query.isPending) return <span className="text-muted-foreground">…</span>
  if (query.isError) return <span className="text-muted-foreground">—</span>
  return <AvailabilityText availability={availabilityOf(query.data, freelancerId)} />
}

/** Above a list whose availability the CRM could not give: the door's own sentence, which says
 *  what failed and that the availability is what is missing. */
export function AvailabilityFailure({ query }: { query: UseQueryResult<TalentCommitments> }) {
  const message = failureMessage(query.error, 'Non riesco a leggere dal CRM chi è già impegnato.')
  if (!message) return null
  return (
    <p role="status" className="text-sm text-muted-foreground">
      {message}
    </p>
  )
}
