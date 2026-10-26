import { Link } from '@tanstack/react-router'
import { ChevronRight } from 'lucide-react'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Skeleton } from '@rebase/ui/skeleton'
import { formatIsoDateItalian } from '@/lib/dates'
import { DayStatePill } from './DayStatePill'
import { quantitaLabel, type WorkUnitRow } from './labels'
import type { useWorkUnits } from './queries'

/**
 * The days a query returned, one row each, newest first as the API orders them. A row is
 * a link to the day's page: the register is a way to get there, and the state and the
 * contract are on the row so a list of them reads without opening one.
 *
 * `showContract` is off inside a contract's own panel, where the contract is the page.
 * A list the API cut (`troncato`) says so: a register that silently stops at the newest
 * two hundred is a register that lies about the oldest.
 */
export function DayRows({
  days,
  showContract = true,
  empty,
}: {
  days: ReturnType<typeof useWorkUnits>
  showContract?: boolean
  empty: { title: string; body: string }
}) {
  if (days.isError) return <QueryErrorBanner error={days.error} />
  if (days.isPending && days.fetchStatus === 'idle') return null
  if (days.isPending) {
    return (
      <div className="space-y-3" role="status" aria-label="Caricamento giornate">
        {Array.from({ length: 3 }, (_, index) => (
          <Skeleton key={index} className="h-16 w-full" />
        ))}
      </div>
    )
  }
  const rows = days.data.items
  if (rows.length === 0) {
    return (
      <div className="border border-dashed px-6 py-12 text-center">
        <h2 className="font-semibold">{empty.title}</h2>
        <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">{empty.body}</p>
      </div>
    )
  }
  return (
    <>
      <ul className="border-t">
        {rows.map((day) => (
          <DayRow key={day.id} day={day} showContract={showContract} />
        ))}
      </ul>
      {days.data.troncato ? (
        <p className="mt-3 text-sm text-muted-foreground">
          Ci sono più giornate di quelle mostrate: sono le più recenti. Restringi il periodo per vedere le altre.
        </p>
      ) : null}
    </>
  )
}

function DayRow({ day, showContract }: { day: WorkUnitRow; showContract: boolean }) {
  return (
    <li className="border-b">
      <Link
        to="/app/days/$workUnitId"
        params={{ workUnitId: day.id }}
        className="group flex items-center gap-4 px-2 py-3 outline-none hover:bg-muted/50 focus-visible:ring-[3px] focus-visible:ring-ring/50"
      >
        <div className="w-24 shrink-0 text-sm font-medium tabular-nums">{formatIsoDateItalian(day.data)}</div>
        <div className="min-w-0 flex-1">
          <p className="truncate font-medium">{day.descrizione}</p>
          <p className="truncate text-sm text-muted-foreground">
            {quantitaLabel(day.quantita)}
            {showContract ? ` · ${day.contract_titolo}, ${day.customer_nome}` : ''}
          </p>
        </div>
        <DayStatePill stato={day.stato} />
        <ChevronRight className="size-4 shrink-0 text-muted-foreground group-hover:text-foreground" aria-hidden />
      </Link>
    </li>
  )
}
