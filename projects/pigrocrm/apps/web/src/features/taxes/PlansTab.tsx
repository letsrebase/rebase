import { useState } from 'react'
import { Detail } from './Detail'
import { PlanPanel } from './PlanPanel'
import { LoadingState } from '@/components/LoadingState'
import { StatusPill } from '@/components/StatusPill'
import { Button } from '@rebase/ui/button'
import { cn } from '@rebase/ui/cn'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import { money } from '@/features/dashboard/format'
import { formatIsoDateItalian } from '@/lib/dates'
import { distanceLabel } from './dates'
import { LOADING_SENTENCE, NOT_ONE_YET, PLANS_EMPTY_SENTENCE } from './labels'
import { NoticePlanDialog } from './NoticePlanDialog'
import { nextInstalment, planRows, planState, type PlanRow } from './plans'
import { useTaxPlanItems, useTaxPlans } from './queries'
import { Failure } from './Failure'

/** «Nessuna rata da pagare» is not a date: the cell says a dash, like a reading not there yet. */
function NextCell({ row, today }: { row: PlanRow; today: string }) {
  const next = nextInstalment(row, today)
  if (next === null || next.due_date === null) return <>{NOT_ONE_YET}</>
  return (
    <>
      <span className="block">{formatIsoDateItalian(next.due_date)}</span>
      <span className="block text-xs text-muted-foreground">{distanceLabel(today, next.due_date)}</span>
    </>
  )
}

function TotalCell({ row }: { row: PlanRow }) {
  return <>{row.kind === 'notice' ? money(row.plan.total) : NOT_ONE_YET}</>
}

function CountCell({ row }: { row: PlanRow }) {
  return <>{row.kind === 'notice' ? row.plan.instalments.length : row.plan.instalments}</>
}

/**
 * «Rate» (the brief): one row per plan, the one chosen for a tax year's June payments or the one
 * of a notice entered by hand, with the amount the notice asks (before interest), how many
 * instalments, the next one and where the plan stands. A row opens the plan's panel. The strong
 * action is «Nuovo piano»: a notice's plan; choosing the instalments of the June payments is
 * done from the tax's own panel in «Imposte».
 */
export function PlansTab({
  legalEntityId,
  wide,
  inline,
  today,
}: {
  legalEntityId: string
  wide: boolean
  inline: boolean
  today: string
}) {
  const [selected, onSelect] = useState<string | null>(null)
  const plans = useTaxPlans(legalEntityId)
  const items = useTaxPlanItems(legalEntityId)
  const [adding, setAdding] = useState(false)
  if (plans.isError) return <Failure error={plans.error} onRetry={() => void plans.refetch()} />
  if (items.isError) return <Failure error={items.error} onRetry={() => void items.refetch()} />
  if (plans.isPending || items.isPending) return <LoadingState label={LOADING_SENTENCE} />
  const rows = planRows(plans.data.instalment_plans, plans.data.notice_plans, items.data)
  const open = rows.find((row) => row.key === selected)
  const panel = open ? (
    <Detail inline={inline} label="Piano aperto" onClose={() => onSelect(null)}>
      <PlanPanel row={open} legalEntityId={legalEntityId} today={today} onClose={() => onSelect(null)} />
    </Detail>
  ) : null
  return (
    <div className="flex items-start gap-6">
    <div className="min-w-0 flex-1 space-y-3">
      <div className="flex justify-end">
        <Button onClick={() => setAdding(true)}>Nuovo piano</Button>
      </div>
      {rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">{PLANS_EMPTY_SENTENCE}</p>
      ) : wide ? (
        <div className="overflow-x-auto overflow-y-hidden border bg-card">
          <Table scrollLabel="Piani di rate">
            <TableHeader>
              <TableRow>
                <TableHead>Piano</TableHead>
                <TableHead className="text-right">Importo senza interessi</TableHead>
                <TableHead className="text-right">Rate</TableHead>
                <TableHead>Prossima rata</TableHead>
                <TableHead>Stato</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => {
                const state = planState(row, today)
                return (
                  <TableRow
                    key={row.key}
                    data-state={row.key === selected ? 'selected' : undefined}
                    aria-selected={row.key === selected}
                    tabIndex={0}
                    className="cursor-pointer align-top focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset"
                    onClick={() => onSelect(row.key)}
                    onKeyDown={(event) => {
                      if (event.key !== 'Enter' && event.key !== ' ') return
                      event.preventDefault()
                      onSelect(row.key)
                    }}
                  >
                    <TableCell className="font-medium">{row.title}</TableCell>
                    <TableCell className={cn('whitespace-nowrap text-right tabular-nums')}>
                      <TotalCell row={row} />
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-right tabular-nums">
                      <CountCell row={row} />
                    </TableCell>
                    <TableCell className="whitespace-nowrap">
                      <NextCell row={row} today={today} />
                    </TableCell>
                    <TableCell>
                      <StatusPill tone={state.tone}>{state.label}</StatusPill>
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </div>
      ) : (
        <ul className="space-y-3" aria-label="Piani di rate">
          {rows.map((row) => {
            const state = planState(row, today)
            return (
              <li key={row.key}>
                <button
                  type="button"
                  onClick={() => onSelect(row.key)}
                  className="block w-full space-y-2 border bg-card p-4 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <div className="flex items-baseline justify-between gap-3">
                    <p className="font-medium">{row.title}</p>
                    <StatusPill tone={state.tone}>{state.label}</StatusPill>
                  </div>
                  <div className="flex justify-between text-sm">
                    <span>Importo senza interessi</span>
                    <span className="tabular-nums">
                      <TotalCell row={row} />
                    </span>
                  </div>
                  <div className="flex justify-between text-sm">
                    <span>Rate</span>
                    <span className="tabular-nums">
                      <CountCell row={row} />
                    </span>
                  </div>
                  <div className="flex justify-between text-sm">
                    <span>Prossima rata</span>
                    <span className="text-right">
                      <NextCell row={row} today={today} />
                    </span>
                  </div>
                </button>
              </li>
            )
          })}
        </ul>
      )}
      {adding ? <NoticePlanDialog legalEntityId={legalEntityId} onClose={() => setAdding(false)} /> : null}
    </div>
    {panel}
    </div>
  )
}

