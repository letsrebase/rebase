import { Link } from '@tanstack/react-router'
import { CalendarPlus } from 'lucide-react'
import { Button } from '@rebase/ui/button'
import { useCanWrite } from '@/lib/auth'
import { DayRows } from './DayRows'
import { useWorkUnits } from './queries'

/**
 * A contract's days, for the contract's own page: the days recorded against it, newest
 * first, and the way to record another with this contract already chosen. The same rows
 * as the register (`DaysPage`), so a day reads the same wherever it is listed.
 */
export function ContractDays({ contractId }: { contractId: string }) {
  const days = useWorkUnits({ contractId })
  const canWrite = useCanWrite()
  return (
    <section aria-labelledby="contract-days" className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <h2 id="contract-days" className="text-lg font-semibold">
          Giornate
        </h2>
        {canWrite ? (
          <Button asChild variant="outline" size="sm">
            <Link to="/app/days/new" search={{ contract: contractId }}>
              <CalendarPlus className="mr-2 size-4" aria-hidden />
              Registra giornata
            </Link>
          </Button>
        ) : null}
      </div>
      <DayRows
        days={days}
        showContract={false}
        empty={{
          title: 'Nessuna giornata su questo contratto',
          body: 'Le giornate che registri compaiono qui, con il loro stato.',
        }}
      />
    </section>
  )
}
