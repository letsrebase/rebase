import { MoneyCell } from '@/components/cells'
import { StatusPill } from '@/components/StatusPill'
import { Badge } from '@rebase/ui/badge'
import { cn } from '@rebase/ui/cn'
import { formatMoneyValue } from '@/features/time/columns'
import { documentLabel, foreignAmount, specialChips, withholdingLabel } from './labels'
import type { Purchase } from './queries'
import { SupplierLabel } from './SupplierLabel'

/** The chips under the supplier: outlined, so they say what is special without a tone of
 *  their own (a state is a pill, a property of the document is a chip). */
export function SupplierDocumentCell({ purchase }: { purchase: Purchase }) {
  const cancelled = purchase.status === 'cancelled'
  const chips = specialChips(purchase)
  return (
    <div className={cn('min-w-0 space-y-1', cancelled && 'text-muted-foreground')}>
      <SupplierLabel purchase={purchase} className="block max-w-72 truncate font-medium" />
      <div className="flex flex-wrap items-center gap-1.5 text-sm text-muted-foreground">
        <span className="max-w-72 truncate" title={documentLabel(purchase)}>
          {documentLabel(purchase)}
        </span>
        {cancelled && <StatusPill tone="muted">Annullato</StatusPill>}
        {chips.map((chip) => (
          <Badge key={chip} variant="outline">
            {chip}
          </Badge>
        ))}
      </div>
    </div>
  )
}

/** The amount of a row with what the API stores under it: the original of a foreign amount, the
 *  withholding. Nothing here is computed. */
export function AmountCell({ purchase }: { purchase: Purchase }) {
  const under = [foreignAmount(purchase), withholdingLabel(purchase)].filter(
    (line): line is string => line !== null,
  )
  return (
    <div className={cn('text-right tabular-nums', purchase.status === 'cancelled' && 'text-muted-foreground')}>
      <MoneyCell>{formatMoneyValue(purchase.amount)}</MoneyCell>
      {under.map((line) => (
        <div key={line} className="text-sm text-muted-foreground">
          {line}
        </div>
      ))}
    </div>
  )
}

