import { useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import { LoadingState } from '@/components/LoadingState'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import { useMediaQuery } from '@rebase/ui/use-media-query'
import { CashBand } from '@/features/cash/home/CashBand'
import { Freshness } from '@/features/dashboard/Freshness'
import { defaultDashboardSearch } from '@/features/dashboard/search'
import { Balances } from './Balances'
import { ExpectedSwatch, ForecastSwatch } from './Flow'
import { LateFigures, LateNote } from './Late'
import { LOADING_SENTENCE, NO_ITEMS_SENTENCE } from './labels'
import { PayablesSection, ReceivablesSection } from './Owed'
import { useCashSchedule, type CashSchedule as Schedule } from './queries'
import { SoloTaxes } from './SoloTaxes'
import { Detail, WeekPanel } from './WeekPanel'
import { WeekCards, WeeksTable } from './Weeks'

/** The weeks are a table from `md`, the week's panel is inline from `lg`: below them a week is a
 *  card and its items a full-screen sheet (the brief: phone first). */
const TABLE = '(min-width: 768px)'
const INLINE_PANEL = '(min-width: 1024px)'

/** The band at the head of the schedule: the worst alert, one line, «e N altre» for the rest,
 *  and no way to close it (the mailbox band of REB-973 has none either). */
function Band({ alerts }: { alerts: Schedule['alerts'] }) {
  const [worst, ...rest] = alerts
  if (worst === undefined) return null
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
      <CashBand alert={worst} className="min-w-0 flex-1" />
      {rest.length > 0 && (
        <span className="text-sm text-muted-foreground">{rest.length === 1 ? 'e 1 altra' : `e ${rest.length} altre`}</span>
      )}
    </div>
  )
}

function Legend() {
  return (
    <ul aria-label="Legenda" className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
      <li className="flex items-center gap-1.5">
        <ExpectedSwatch />
        Attesi: fatture e scadenze certe
      </li>
      <li className="flex items-center gap-1.5 italic">
        <ForecastSwatch />
        previsti: stime, ricorrenze e bozze
      </li>
    </ul>
  )
}

function Horizon({
  options,
  value,
  onChange,
}: {
  options: number[]
  value: number
  onChange: (weeks: number) => void
}) {
  return (
    <Select
      value={String(value)}
      onValueChange={(next) => {
        // The choice is one of the server's own options: found by its text, never parsed.
        const chosen = options.find((option) => String(option) === next)
        if (chosen !== undefined) onChange(chosen)
      }}
    >
      <SelectTrigger size="sm" aria-label="Orizzonte">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {options.map((option) => (
          <SelectItem key={option} value={String(option)}>
            {option} settimane
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

/**
 * The cash schedule (the brief `2026-11-10-cash-and-schedule-brief.md`): the one view mounted by
 * the Home tab «Scadenziario» and by the page «Cassa». From the top: the band, the balances, the
 * horizon, legend and age of the figures, the two late figures, the weeks (a table with the
 * selected week's items in a panel; a card per week on the phone), the note on what the balance
 * leaves out, the solo's taxes, «Da incassare» and «Da pagare», and the line to the economic tab.
 *
 * Every figure, flag and count is the server's. What a viewer may not read arrives as `null` and
 * is absent, never zero.
 */
export function CashSchedule() {
  const [horizon, setHorizon] = useState<number | null>(null)
  const [open, setOpen] = useState<string | null>(null)
  const client = useQueryClient()
  const wide = useMediaQuery(TABLE)
  const inline = useMediaQuery(INLINE_PANEL)
  const query = useCashSchedule(horizon)

  if (query.isError) return <QueryErrorBanner error={query.error} onRetry={() => void query.refetch()} />
  if (query.isPending || !query.data) return <LoadingState label={LOADING_SENTENCE} />

  const schedule = query.data
  const week = schedule.weeks.find((candidate) => candidate.start === open) ?? null

  return (
    <div className="space-y-6">
      <Band alerts={schedule.alerts} />
      <Balances schedule={schedule} />

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <Horizon options={schedule.horizon_options} value={schedule.horizon_weeks} onChange={setHorizon} />
        <Legend />
        <div className="md:ml-auto">
          <Freshness
            calculatedAt={schedule.calculated_at}
            onRefresh={() => void client.invalidateQueries({ queryKey: ['dashboard'] })}
          />
        </div>
      </div>

      {schedule.has_open_items ? (
        <>
          <LateFigures receivable={schedule.late_receivable} payable={schedule.late_payable} />
          <div className="flex items-start gap-6">
            <div className="min-w-0 flex-1 space-y-3">
              {wide ? (
                <WeeksTable schedule={schedule} selected={open} onSelect={setOpen} />
              ) : (
                <WeekCards schedule={schedule} onSelect={setOpen} />
              )}
              {schedule.show_balance && (
                <LateNote receivable={schedule.late_receivable} payable={schedule.late_payable} />
              )}
            </div>
            {week !== null && inline && (
              <Detail inline label="Voci della settimana" onClose={() => setOpen(null)}>
                <WeekPanel key={week.start} schedule={schedule} week={week} />
              </Detail>
            )}
          </div>
          {week !== null && !inline && (
            <Detail inline={false} label="Voci della settimana" onClose={() => setOpen(null)}>
              <WeekPanel key={week.start} schedule={schedule} week={week} />
            </Detail>
          )}
          {schedule.taxes !== null && schedule.taxes.length > 0 && <SoloTaxes taxes={schedule.taxes} />}
        </>
      ) : (
        <p role="status" className="text-sm text-muted-foreground">
          {NO_ITEMS_SENTENCE(schedule.horizon_weeks)}
        </p>
      )}

      {schedule.has_open_items && (
        <div className="grid gap-6 lg:grid-cols-2">
          <ReceivablesSection />
          {schedule.show_outgoing && <PayablesSection />}
        </div>
      )}

      <p className="text-sm text-muted-foreground">
        «Cassa» conta quando i soldi entrano ed escono; l’analisi «Economica» conta quando il lavoro è maturato. Lo stesso
        mese può leggersi in modo diverso nelle due viste, ed è voluto.{' '}
        <Link to="/app" search={{ ...defaultDashboardSearch(), tab: 'economic' }} className="underline underline-offset-4">
          Apri Economica
        </Link>
      </p>
    </div>
  )
}
