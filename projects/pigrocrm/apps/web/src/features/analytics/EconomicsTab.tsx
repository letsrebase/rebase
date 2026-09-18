import { useState } from 'react'
import { FilterChip } from '@/components/FilterRow'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { Card, CardContent } from '@rebase/ui/card'
import { Skeleton } from '@rebase/ui/skeleton'
import { useIsAdmin } from '@/lib/auth'
import { toIsoDate } from '@/lib/dates'
import { PeriodTotals, PnlRows } from './PnlRows'
import { ToInvoiceDialog } from './ToInvoiceDialog'
import { PNL_BASE_LABELS, PNL_BASES, useDealPnl, usePeriodPnl, type PnlBase } from './queries'

/**
 * A discriminated argument, never two optional props: there is then no "empty string"
 * spelling to get wrong, and the same shape `useDocuments` already uses for
 * `{customerId} | {dealId}`. An empty path segment does not match the API route,
 * Starlette's trailing-slash redirect lands the request on the *list* endpoint, and it
 * still resolves 200 with nothing to show -- a live defect this product has already
 * paid for once.
 */
type EconomicsTabProps = { dealId: string } | { customerId: string }

function DealEconomics({ dealId }: { dealId: string }) {
  const pnl = useDealPnl(dealId)
  const isAdmin = useIsAdmin()
  const [invoicing, setInvoicing] = useState(false)

  // The banner and nothing else: a conto economico rendered under a failed read would
  // show a screenful of dashes that look like real zeroes.
  if (pnl.isError) return <QueryErrorBanner error={pnl.error} />
  if (pnl.isLoading || !pnl.data) return <Skeleton className="h-64 w-full" />

  // The string the API sent, compared as a string. `Number(...) > 0` on a decimal field
  // is exactly the accidental arithmetic `lib/no-float-money.test.ts` exists to catch,
  // and `"0.00"` is the only spelling `Numeric(12,2)` serialises for zero.
  const somethingToInvoice = pnl.data.ore_fatturabili_non_fatturate !== '0.00'

  return (
    <div className="space-y-6">
      <Card>
        <CardContent className="pt-6">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="font-semibold">Conto economico</h2>
            <Badge variant={pnl.data.stato === 'chiuso' ? 'default' : 'secondary'}>
              {pnl.data.stato}
            </Badge>
          </div>
          <PnlRows pnl={pnl.data} />
        </CardContent>
      </Card>

      {/* `admin` in the UI because `bind_time_to_invoice` is `admin` in the service: a
          button everyone can press and only one role can use is a 403 waiting to
          happen. The service check remains the one that decides. */}
      {isAdmin && somethingToInvoice && (
        <div>
          <Button onClick={() => setInvoicing(true)}>Genera bozza di fattura</Button>
          <p className="mt-2 text-xs text-muted-foreground">
            Crea una bozza raggruppando le ore per tariffa e mese. Non emette niente:
            l&apos;emissione resta un passaggio a parte.
          </p>
        </div>
      )}

      {invoicing && (
        <ToInvoiceDialog dealId={dealId} open onOpenChange={() => setInvoicing(false)} />
      )}
    </div>
  )
}

/** One sentence per reading, under the chips: the row's hint names the base, this says
 *  what the base does to the figure, and only one of the two is on screen at a time. */
const BASE_NOTE: Record<PnlBase, string> = {
  emissione: 'Ricavi attribuiti alla data di emissione della fattura.',
  competenza:
    'Ricavi attribuiti al periodo di competenza della fattura, o alla data di emissione quando manca.',
}

function CustomerEconomics({ customerId }: { customerId: string }) {
  // A customer's economics is the sum of their deals, served by the same period endpoint
  // with a `customer_id` filter -- never a second aggregation written here, which is how
  // two totals begin to disagree. The window defaults to the current calendar year
  // because the endpoint requires one; `toIsoDate` builds it from local date parts, so
  // the report cannot open on next year late on 31 December.
  const year = new Date().getFullYear()
  // The reading is state and not a search param: it is a way of looking at one card,
  // and `emissione` is what the spec recorded, so the tab opens on it every time.
  const [base, setBase] = useState<PnlBase>('emissione')
  const pnl = usePeriodPnl({
    from: toIsoDate(new Date(year, 0, 1)),
    to: toIsoDate(new Date(year, 11, 31)),
    customer_id: customerId,
    // Always explicit, the default included, so the request says what the card shows.
    base,
  })

  return (
    <Card>
      <CardContent className="pt-6">
        {/* The chips stay up whatever the request did: a failed «per competenza» read
            with no way back to «per emissione» would strand the reader on a banner. */}
        <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="mb-1 font-semibold">Conto economico {year}</h2>
            <p className="text-xs text-muted-foreground">
              Somma dei deal di questo cliente nell&apos;anno in corso. {BASE_NOTE[base]}
            </p>
          </div>
          {/* `FilterChip`, the riga-filtri toggle of the design spec (§4), and not a
              `Select`: two readings are a pair of chips, one always pressed -- unlike
              `FilterChips` there is no «all», because a P&L is always read by *some*
              date. `aria-pressed` is the state, as everywhere else. */}
          <div role="group" aria-label="Ricavi per" className="flex items-center gap-2">
            {PNL_BASES.map((option) => (
              <FilterChip key={option} pressed={base === option} onPress={() => setBase(option)}>
                {PNL_BASE_LABELS[option]}
              </FilterChip>
            ))}
          </div>
        </div>
        {pnl.isError ? (
          // The banner and nothing else: a conto economico rendered under a failed read
          // would show a screenful of dashes that look like real zeroes.
          <QueryErrorBanner error={pnl.error} />
        ) : pnl.isLoading || !pnl.data ? (
          <Skeleton className="h-48 w-full" />
        ) : (
          <PeriodTotals pnl={pnl.data} base={base} />
        )}
      </CardContent>
    </Card>
  )
}

/**
 * The «Economia» tab, on a deal and on a customer.
 *
 * Two very different reports behind one name, because the question is the same one --
 * "is this making money?" -- and the answer has a different shape depending on what is
 * being asked about. A deal has one conto economico; a customer has the sum of theirs,
 * split into what is finished and what is not.
 */
export function EconomicsTab(props: EconomicsTabProps) {
  return 'dealId' in props ? (
    <DealEconomics dealId={props.dealId} />
  ) : (
    <CustomerEconomics customerId={props.customerId} />
  )
}
