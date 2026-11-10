import { useOrganization } from '@/features/organizations/queries'
import type { Purchase } from './queries'

const EMPTY = '—'

/**
 * Who a purchase was bought from: the supplier's registered name, or the merchant of a till
 * receipt. Truncated to its line with the full name as the title, so a 60-character name never
 * widens a table or a card.
 */
export function SupplierLabel({
  purchase,
  className,
}: {
  purchase: Pick<Purchase, 'supplier_id' | 'merchant_name'>
  className?: string
}) {
  const supplier = useOrganization(purchase.supplier_id ?? '', {
    enabled: purchase.supplier_id !== null,
  })
  const name = supplier.data?.legal_name ?? purchase.merchant_name ?? EMPTY
  return (
    <span className={className ?? 'block max-w-72 truncate'} title={name}>
      {name}
    </span>
  )
}
