import { Link } from '@tanstack/react-router'
import { useDeal } from '@/features/deals/queries'

/**
 * The «Deal» row of a contract that was converted from one (REB-934): the deal's name, linking
 * back to it. Nothing for a contract written by hand, which has no deal.
 */
export function ContractDealLink({ dealId }: { dealId: string | null | undefined }) {
  const deal = useDeal(dealId ?? '', { enabled: dealId != null })
  if (dealId == null) return null
  return (
    <div className="flex justify-between gap-4 border-b py-2 text-sm">
      <span className="text-muted-foreground">Deal</span>
      <Link
        className="text-right underline underline-offset-4"
        to="/app/deal/$dealId"
        params={{ dealId }}
      >
        {deal.data?.name ?? 'Apri il deal'}
      </Link>
    </div>
  )
}
