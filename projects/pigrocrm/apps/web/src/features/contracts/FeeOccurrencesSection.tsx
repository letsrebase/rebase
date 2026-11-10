import { Link, useNavigate } from '@tanstack/react-router'
import { FilePlus } from 'lucide-react'
import { toast } from '@rebase/ui/sonner'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { StatusPill } from '@/components/StatusPill'
import { Button } from '@rebase/ui/button'
import { Card, CardContent } from '@rebase/ui/card'
import { formatDate, formatInvoiceHeading } from '@/features/invoices/format'
import { useCan } from '@/lib/auth'
import {
  FEE_STATE_LABELS,
  FEE_STATE_TONE,
  feePeriodLabel,
  type FeeOccurrence,
} from './feeLabels'
import { useBillFee, useFeeOccurrences } from './feeQueries'
import { formatAmount } from './labels'

/**
 * The «Canoni» section of a contract's page (REB-1032): the occurrences of its recurring fee
 * rate cards, each one with where it stands (to bill, on a draft, on an issued invoice) and,
 * for one nobody bills yet, the button «Fattura il canone» that makes the draft invoice with
 * the card and the period already on its line. The window is the server's (the last 90 days
 * and the next 180), and every figure and date is a field it sent.
 *
 * A contract with no recurring fee in that window has no section at all: nothing here is an
 * empty card asking for something the contract does not have. A contract the server will not
 * bill (still a draft, or not in euros) shows the rows without the button and says why.
 * The button is an administrator's, like the days' draft.
 */
export function FeeOccurrencesSection({
  contractId,
  currency,
}: {
  contractId: string
  currency: string
}) {
  const canBill = useCan('bill_contract_fee')
  const navigate = useNavigate()
  const fees = useFeeOccurrences(contractId)
  const bill = useBillFee(contractId)

  // Nothing while it loads and nothing for a contract with no recurring fee: a card that appears
  // and goes away would shift the page of every contract that has none.
  if (fees.isLoading || (fees.isSuccess && fees.data.occurrences.length === 0)) return null

  const billing = bill.isPending ? bill.variables : undefined

  function create(occurrence: FeeOccurrence) {
    bill.mutate(
      { rate_card_id: occurrence.rate_card_id, period_start: occurrence.period_start },
      {
        onSuccess: (result) => {
          toast.success(
            result.created
              ? 'Bozza di fattura creata per il canone'
              : 'Il canone è già su questa bozza',
          )
          void navigate({ to: '/app/invoices/$invoiceId', params: { invoiceId: result.invoice.id } })
        },
      },
    )
  }

  return (
    <Card className="lg:col-span-2">
      <CardContent className="pt-6">
        <h2 className="mb-1 font-semibold">Canoni</h2>
        {fees.data !== undefined && (
          <p className="mb-3 text-sm text-muted-foreground">
            Le scadenze dei canoni ricorrenti dal {formatDate(fees.data.date_from)} al{' '}
            {formatDate(fees.data.date_to)}.
          </p>
        )}

        {fees.isError ? (
          <QueryErrorBanner error={fees.error} />
        ) : fees.data === undefined ? null : (
          <div className="space-y-4">
            <ul className="divide-y">
              {fees.data.occurrences.map((occurrence) => {
                const key = `${occurrence.rate_card_id}-${occurrence.period_start}`
                const pending =
                  billing?.rate_card_id === occurrence.rate_card_id &&
                  billing.period_start === occurrence.period_start
                return (
                  <li key={key} className="flex flex-wrap items-center gap-x-4 gap-y-2 py-3">
                    {/* A text column no narrower than 11rem: below it the amount and the action
                        drop to a line of their own instead of squeezing the words over each other. */}
                    <div className="min-w-44 flex-1">
                      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                        <span className="font-medium">{formatDate(occurrence.period_start)}</span>
                        <StatusPill tone={FEE_STATE_TONE[occurrence.state]}>
                          {FEE_STATE_LABELS[occurrence.state]}
                        </StatusPill>
                      </div>
                      <p className="text-sm text-muted-foreground">{feePeriodLabel(occurrence)}</p>
                    </div>
                    <span className="shrink-0 tabular-nums">
                      {formatAmount(occurrence.amount, currency)}
                    </span>
                    {/* One width for the link and the button, so the amounts above them line
                        up whichever a row shows. */}
                    <div className="flex shrink-0 justify-end sm:w-44">
                      {occurrence.invoice !== null ? (
                        <Link
                          className="text-sm underline underline-offset-4"
                          to="/app/invoices/$invoiceId"
                          params={{ invoiceId: occurrence.invoice.id }}
                        >
                          {formatInvoiceHeading(occurrence.invoice)}
                        </Link>
                      ) : (
                        canBill && (
                          <Button
                            size="sm"
                            variant="outline"
                            aria-label={`Fattura il canone del ${formatDate(occurrence.period_start)}`}
                            disabled={!fees.data.billable || bill.isPending}
                            onClick={() => create(occurrence)}
                          >
                            <FilePlus className="mr-2 size-4" aria-hidden="true" />
                            {pending ? 'Creazione…' : 'Fattura il canone'}
                          </Button>
                        )
                      )}
                    </div>
                  </li>
                )
              })}
            </ul>

            {canBill && fees.data.block_reason !== null && fees.data.block_reason !== undefined && (
              <p role="alert" className="text-sm text-destructive">
                {fees.data.block_reason[0]?.toUpperCase()}
                {fees.data.block_reason.slice(1)}.
              </p>
            )}

            {bill.isError && <QueryErrorBanner error={bill.error} />}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
