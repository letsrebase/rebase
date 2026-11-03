import { Link } from '@tanstack/react-router'
import { FileSignature } from 'lucide-react'
import { useState } from 'react'
import { Button } from '@rebase/ui/button'
import { ConvertDealDialog } from '@/features/deals/ConvertDealDialog'
import { useStages } from '@/features/deals/queries'
import type { components } from '@/lib/api-types'

type DealWithContract = Pick<
  components['schemas']['DealDetailRead'],
  'id' | 'pipeline_stage_id' | 'contract_id'
>

/**
 * «Crea contratto», in the deal header: only on a deal in a won stage that has no contract
 * yet, and only for someone who may write (the caller checks that). The dialog does the
 * asking; a won deal with no lines, which the server refuses, says so there.
 */
export function ConvertDealButton({ deal }: { deal: DealWithContract }) {
  const [open, setOpen] = useState(false)
  const stages = useStages()
  const stage = stages.data?.find((item) => item.id === deal.pipeline_stage_id)
  if (stage?.type !== 'won' || deal.contract_id != null) return null
  return (
    <>
      <Button variant="outline" onClick={() => setOpen(true)}>
        <FileSignature className="mr-2 size-4" />
        Crea contratto
      </Button>
      <ConvertDealDialog dealId={deal.id} open={open} onOpenChange={setOpen} />
    </>
  )
}

/**
 * What the deal page says once the deal is a contract: where it went, and that its lines are
 * the contract's from now on (`DealService.update` refuses a write to them), so the figures
 * on this page are the price as it was booked and the contract is the one to read.
 */
export function DealContractSection({ contractId }: { contractId: string | null | undefined }) {
  if (contractId == null) return null
  return (
    <section className="text-sm">
      <h3 className="mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
        Contratto
      </h3>
      <div className="flex justify-between gap-4 border-b py-2 last:border-0">
        <span className="text-muted-foreground">Contratto</span>
        <Link
          to="/app/contracts/$contractId"
          params={{ contractId }}
          className="text-right font-medium underline underline-offset-2"
        >
          Apri il contratto
        </Link>
      </div>
      <p className="py-2 text-xs text-muted-foreground">
        Le righe di valore sono bloccate: da qui il prezzo è quello del contratto, che resta la
        sola fonte.
      </p>
    </section>
  )
}
