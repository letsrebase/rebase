import { useState } from 'react'
import { Button } from '@rebase/ui/button'
import { Card, CardContent } from '@rebase/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import { LoadingState } from '@/components/LoadingState'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { StatusPill } from '@/components/StatusPill'
import {
  OWN_MONTH_LABELS,
  OWN_MONTH_TONES,
  datesWords,
  feeWords,
  formatDate,
  paymentRuleWords,
  type OwnEngagementMonth,
} from '@/features/engagements/labels'
import { useOwnEngagement } from '@/features/engagements/queries'
import { toProblem } from '@/lib/api'
import { useIsAdmin } from '@/lib/auth'

const VISIBLE_MONTHS = 6
const EMPTY = '—'
const READ_ERROR = {
  code: 'own_engagement_unavailable',
  detail: "Non riesco a leggere lo stato dell'incarico. I tuoi giorni e la tariffa si vedono lo stesso.",
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5 border-b py-2 text-sm last:border-b-0">
      <dt className="text-muted-foreground">{label}</dt>
      <dd>{value}</dd>
    </div>
  )
}

function heldSentence(held: string[]): string {
  return held.length === 1
    ? 'Approvato, 1 giorno non riconosciuto'
    : `Approvato, ${held.length} giorni non riconosciuti`
}

function MonthState({ month }: { month: OwnEngagementMonth }) {
  const held = month.state === 'approved' && month.held_dates.length > 0
  return (
    <div className="flex flex-col items-start gap-1 text-sm">
      <StatusPill tone={OWN_MONTH_TONES[month.state]}>
        {held ? heldSentence(month.held_dates) : OWN_MONTH_LABELS[month.state]}
      </StatusPill>
      {month.state === 'received' && month.answer_by !== null && (
        <span className="text-muted-foreground">risposta entro il {formatDate(month.answer_by)}</span>
      )}
      {held && (
        <span className="text-muted-foreground tabular-nums">
          {month.held_dates.map((date) => formatDate(date)).join(', ')}
        </span>
      )}
    </div>
  )
}

/**
 * «Il tuo incarico con rebase» (REB-1035, decision 4B): the freelancer's read-only view of the
 * engagement their contract came from. A 404 means it did not come from one: nothing is drawn.
 */
export function OwnEngagementCard({ contractId }: { contractId: string }) {
  const admin = useIsAdmin()
  const query = useOwnEngagement(contractId)
  const [showAll, setShowAll] = useState(false)

  if (!admin) return null
  if (query.isLoading) return <LoadingState label="Leggo il tuo incarico…" />
  if (query.isError) {
    if (toProblem(query.error).status === 404) return null
    return <QueryErrorBanner error={READ_ERROR} onRetry={() => void query.refetch()} />
  }
  const engagement = query.data
  if (!engagement) return null

  const months = engagement.months
  const hidden = months.length > VISIBLE_MONTHS && !showAll
  const shown = hidden ? months.slice(-VISIBLE_MONTHS) : months
  const fee = feeWords(engagement.fee, engagement.fee_unit, engagement.days_per_week)

  return (
    <Card>
      <CardContent className="pt-6">
        <h2 className="mb-3 font-semibold">Il tuo incarico con rebase</h2>
        <dl className="mb-4 grid grid-cols-1 gap-x-6 lg:grid-cols-3">
          <Fact label="Lettera" value={engagement.letter_number ?? EMPTY} />
          <Fact
            label="Tariffa"
            value={`${fee ?? EMPTY} · ${datesWords(engagement.starts_on, engagement.ends_on)}`}
          />
          <Fact label="Pagamento" value={paymentRuleWords(engagement)} />
        </dl>
        {months.length > 0 && (
          <Table scrollLabel="I mesi del tuo incarico">
            <TableHeader>
              <TableRow>
                <TableHead>Mese</TableHead>
                <TableHead>Rendiconto</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {shown.map((month) => (
                <TableRow key={month.month} className="align-top">
                  <TableCell>{month.label}</TableCell>
                  <TableCell>
                    <MonthState month={month} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        {hidden && (
          <Button type="button" variant="outline" size="sm" className="mt-3" onClick={() => setShowAll(true)}>
            Mostra i mesi precedenti
          </Button>
        )}
      </CardContent>
    </Card>
  )
}
