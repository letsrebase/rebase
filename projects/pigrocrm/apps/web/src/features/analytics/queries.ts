import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { queryKeys } from '@/lib/query'

/**
 * Wire shapes taken from the generated OpenAPI schema, never hand-declared: the single
 * source of truth is `analytics/schemas.py` and `pnpm generate:api` tracks it.
 *
 * Every economic field on these types is a `string`, deliberately -- a `Numeric` column
 * serialises to JSON as digits, not as a float. Nothing in this feature adds, divides or
 * rounds one of them: §6 forbids an economic total being born in the browser, and
 * `lib/no-float-money.test.ts` fails the build if a module tries.
 */
export type DealPnl = components['schemas']['DealPnl']
export type PeriodPnl = components['schemas']['PeriodPnl']
export type PnlTotals = components['schemas']['PnlTotals']

/**
 * Which date a period P&L attributes revenue by (ORB-61). `issue` is the invoice's
 * own date, the reading slice 4 §7.1 chose («ricavo = fatturato») and the default;
 * `accrual` is `coalesce(competenza_da, data_emissione)` on the server, so August
 * work invoiced in September lands in August, and an invoice with no period falls back
 * to its date rather than out of the year. Two readings side by side rather than one
 * silently changed, which is the spec's own pattern for cash against accrual (§13).
 */
export type PnlBase = 'issue' | 'accrual'

export const PNL_BASES: readonly PnlBase[] = ['issue', 'accrual']

/** The chip that selects each reading. A total `Record`, like `INVOICE_STATE_LABELS`, so
 *  a third base fails to compile here rather than rendering an unlabelled chip. */
export const PNL_BASE_LABELS: Record<PnlBase, string> = {
  issue: 'Per emissione',
  accrual: 'Per competenza',
}

/** The window every period-wide report is asked for. Not exported: the one hook that
 *  takes it is called with an object literal, and a type nobody outside this module
 *  names is one more thing to keep true for no reader. */
interface PeriodParams {
  from: string
  to: string
  customer_id?: string
  base?: PnlBase
}

export function useDealPnl(dealId: string) {
  return useQuery({
    queryKey: queryKeys.dealPnl(dealId),
    queryFn: () =>
      unwrap(api.GET('/api/deals/{deal_id}/pnl', { params: { path: { deal_id: dealId } } })),
  })
}

export function usePeriodPnl(params: PeriodParams) {
  return useQuery({
    queryKey: queryKeys.periodPnl(params),
    queryFn: () => unwrap(api.GET('/api/analytics/pnl', { params: { query: params } })),
  })
}

/**
 * Invalidates the P&L, the hours and the deal's timeline: binding hours to a draft
 * changes what is left to invoice, which all three screens report. A stale «Ore da
 * fatturare» next to a freshly created draft is the shape of bug that makes somebody
 * generate the same draft twice.
 */
export function useToInvoiceDraft(dealId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: { entry_ids: string[]; group_by_month: boolean }) =>
      unwrap(
        api.POST('/api/deals/{deal_id}/time-entries/to-invoice-draft', {
          params: { path: { deal_id: dealId } },
          body,
        }),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.dealPnl(dealId) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.dealTimeSummary(dealId) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.timeEntries() })
      void queryClient.invalidateQueries({ queryKey: queryKeys.timeline('deal', dealId) })
      // The draft itself is a new invoice: the deal's Fatture tab and the invoice list
      // are stale the moment this resolves.
      void queryClient.invalidateQueries({ queryKey: queryKeys.invoices() })
    },
  })
}
