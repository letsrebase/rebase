import { Link } from '@tanstack/react-router'
import { CalendarPlus, FileCheck, Send } from 'lucide-react'
import { useState } from 'react'
import { Button } from '@rebase/ui/button'
import { useCan } from '@/lib/auth'
import { ApprovalRequestDialog } from './ApprovalRequestDialog'
import { DayRows } from './DayRows'
import { useWorkUnits } from './queries'

/**
 * A contract's days, for the contract's own page: the days recorded against it, newest
 * first, and the three things a person does with them from here: record another with this
 * contract already chosen, record a client's approval for some of them (REB-747), and, on a
 * contract that requires it, ask the client for approval of named days before they are
 * worked (REB-749). The same rows as the register (`DaysPage`), so a day reads the same
 * wherever it is listed.
 */
export function ContractDays({
  contractId,
  customerId,
  requiresPriorApproval,
}: {
  contractId: string
  customerId: string
  requiresPriorApproval: boolean
}) {
  const days = useWorkUnits({ contractId })
  // The two writes this section offers, each the service's own action string.
  const canRecord = useCan('create_work_unit')
  const canApprove = useCan('create_approval')
  const canAsk = useCan('request_approval')
  const [asking, setAsking] = useState(false)
  return (
    <section aria-labelledby="contract-days" className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="contract-days" className="text-lg font-semibold">
          Giornate
        </h2>
        {canRecord || canApprove || canAsk ? (
          <div className="flex flex-wrap gap-2">
            {requiresPriorApproval && canAsk ? (
              <Button variant="outline" size="sm" onClick={() => setAsking(true)}>
                <Send className="mr-2 size-4" aria-hidden />
                Chiedi approvazione
              </Button>
            ) : null}
            {canApprove ? (
              <Button asChild variant="outline" size="sm">
                <Link to="/app/approvals/new" search={{ contract: contractId }}>
                  <FileCheck className="mr-2 size-4" aria-hidden />
                  Registra approvazione
                </Link>
              </Button>
            ) : null}
            {canRecord ? (
              <Button asChild variant="outline" size="sm">
                <Link to="/app/days/new" search={{ contract: contractId }}>
                  <CalendarPlus className="mr-2 size-4" aria-hidden />
                  Registra giornata
                </Link>
              </Button>
            ) : null}
          </div>
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
      {canAsk && requiresPriorApproval ? (
        <ApprovalRequestDialog
          open={asking}
          onOpenChange={setAsking}
          contractId={contractId}
          customerId={customerId}
        />
      ) : null}
    </section>
  )
}
