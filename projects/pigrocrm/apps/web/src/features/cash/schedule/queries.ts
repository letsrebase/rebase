import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'
import { toast } from '@rebase/ui/sonner'
import { DASHBOARD_STALE_MS } from '@/features/dashboard/queries'
import { useInvoiceInvalidation } from '@/features/invoices/queries'
import { api, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { useLegalEntity } from '@/lib/legalEntity'
import { queryKeys } from '@/lib/query'

export type CashSchedule = components['schemas']['CashSchedule']
export type CashWeek = components['schemas']['CashWeek']
export type CashAccountCard = components['schemas']['CashAccountCard']
export type CashAlert = components['schemas']['CashAlert']
export type LateFigure = components['schemas']['LateFigure']
export type SoloTaxRow = components['schemas']['SoloTaxRow']
export type DueItem = components['schemas']['DueItemRead']

/** How many items a week's panel asks for at a time; «Mostra altri» asks for the next page. */
export const ITEMS_PAGE = 50

/** The sidebar's legal entity as the parameter these reads take: nothing under «Tutte le
 *  aziende», where the server adds every legal entity up (the same rule as every dashboard). */
function useLegalEntityParam(): { legal_entity_id?: string } {
  const { selected } = useLegalEntity()
  return selected === null ? {} : { legal_entity_id: selected }
}

/**
 * Every key below starts with the literal `'dashboard'`, like `queryKeys.dashboard`, so a write
 * that cannot know which of these is on screen (a settlement) refreshes them all by the prefix
 * `queryKeys.dashboardAll`. The kinds are not added to `queryKeys.dashboard`'s union: this file
 * owns them.
 */
const SCHEDULE = 'cash-schedule'
const PAYABLES = 'cash-payables'
const DUE_ITEMS = 'cash-due-items'

/**
 * The schedule: weeks, balances, late figures, alerts. `weeks` is the horizon the person chose,
 * `null` for the server's own default (the answer says which it is: `horizon_weeks`), so the
 * default is never a number in a component. The previous answer stays on screen while another
 * horizon is read, but never across a change of legal entity: the figures of one azienda are not
 * shown under the name of another.
 */
export function useCashSchedule(weeks: number | null) {
  const scope = useLegalEntityParam()
  const query = { ...scope, ...(weeks === null ? {} : { weeks }) }
  return useQuery({
    queryKey: ['dashboard', SCHEDULE, { ...scope, weeks: weeks === null ? 'default' : String(weeks) }],
    queryFn: () => unwrap(api.GET('/api/cash/schedule', { params: { query } })),
    staleTime: DASHBOARD_STALE_MS,
    placeholderData: (previous, previousQuery) => {
      const before = previousQuery?.queryKey[2] as { legal_entity_id?: string } | undefined
      return before?.legal_entity_id === scope.legal_entity_id ? previous : undefined
    },
  })
}

/** What is owed to suppliers, the mirror of `useReceivablesDashboard`. Core, no module guard. */
export function usePayablesDashboard(enabled = true) {
  const scope = useLegalEntityParam()
  return useQuery({
    queryKey: ['dashboard', PAYABLES, scope],
    queryFn: () => unwrap(api.GET('/api/cash/payables', { params: { query: scope } })),
    staleTime: DASHBOARD_STALE_MS,
    enabled,
  })
}

/** One week's open items, 50 at a time. `from`/`to` are the week's own dates (the current week
 *  starts today: the server has already counted the days before it as late). `direction` is
 *  asked for only when the schedule has no outgoing side. */
export function useWeekItems(range: { from: string; to: string; direction?: 'in' | 'out' }) {
  const scope = useLegalEntityParam()
  const base = {
    ...scope,
    due_from: range.from,
    due_to: range.to,
    ...(range.direction === undefined ? {} : { direction: range.direction }),
  }
  return useInfiniteQuery({
    queryKey: ['dashboard', DUE_ITEMS, base],
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET('/api/due-items', {
          params: { query: { ...base, limit: ITEMS_PAGE, ...(pageParam ? { cursor: pageParam } : {}) } },
        }),
      ),
    initialPageParam: '' as string,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    staleTime: DASHBOARD_STALE_MS,
  })
}

/**
 * «Segna come incassato» / «Segna come pagato»: one settlement for what remains, on the day the
 * server calls today. The amount sent is the item's own `remaining` string, never computed here.
 * Everything that shows the item (the schedule, the late lists, the invoice and purchase lists
 * that read the same fact) is read again afterwards.
 */
export function useSettleDueItem() {
  const client = useQueryClient()
  const invalidateInvoice = useInvoiceInvalidation()
  return useMutation({
    mutationFn: ({ item, today }: { item: DueItem; today: string }) =>
      unwrap(
        api.POST('/api/due-items/{item_id}/settlements', {
          params: { path: { item_id: item.id } },
          body: { amount: item.remaining, settled_on: today },
        }),
      ),
    onSuccess: (_data, { item }) => {
      // Every dashboard read: the schedule, the payables, the receivables, the Home's summary.
      void client.invalidateQueries({ queryKey: queryKeys.dashboardAll })
      if (item.source_kind === 'sales_invoice') {
        // The invoice's own set: the lists, the year's register, the ceilings, the invoice itself.
        invalidateInvoice(item.source_id ?? undefined)
      } else if (item.source_kind === 'purchase') {
        void client.invalidateQueries({ queryKey: queryKeys.purchases() })
        if (item.source_id) void client.invalidateQueries({ queryKey: queryKeys.purchase(item.source_id) })
      }
      toast.success(item.direction === 'in' ? 'Incasso registrato' : 'Pagamento registrato')
    },
  })
}
