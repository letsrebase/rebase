import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { queryKeys } from '@/lib/query'

/**
 * The admin-only writes behind the three time-tracking settings tabs.
 *
 * A module of its own rather than more of `queries.ts`: everything here belongs to
 * slice 4's `CostCategoryService`, `PeriodLockService` and the two rate setters on
 * `TimeEntryService`, all of which call `actor.require_admin` and are all deliberately
 * absent from the MCP surface. The read side people share with the rest of the app
 * (`useCostCategories`) already lives in `features/costs/queries.ts` and is imported
 * from there, exactly as `PipelinePanel` imports `useStages` from the deals feature.
 */
export type PeriodLock = components['schemas']['PeriodLockRead']

type CostCategoryCreateBody = components['schemas']['CostCategoryCreate']
type CostCategoryUpdateBody = components['schemas']['CostCategoryUpdate']
type UserRatesBody = components['schemas']['UserRatesUpdate']
type DealRateBody = components['schemas']['DealRateUpdate']

/** Invalidates every list that shows a category, archived or not, because the panel
 *  toggles between the two views and a stale cache is what makes an archive look like
 *  it did nothing. The prefix `['cost-categories']` matches both entries
 *  `queryKeys.costCategories` can produce -- the flag is the second element. */
function invalidateCategories(queryClient: QueryClient) {
  void queryClient.invalidateQueries({ queryKey: ['cost-categories'] })
}

export function useCreateCostCategory() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: CostCategoryCreateBody) =>
      unwrap(api.POST('/api/cost-categories', { body })),
    onSuccess: () => invalidateCategories(queryClient),
  })
}

export function useUpdateCostCategory(categoryId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: CostCategoryUpdateBody) =>
      unwrap(
        api.PATCH('/api/cost-categories/{category_id}', {
          params: { path: { category_id: categoryId } },
          body,
        }),
      ),
    onSuccess: () => invalidateCategories(queryClient),
  })
}

/** Archive, never delete: `CostCategoryService` has no DELETE verb at all, because a
 *  deleted category with costs still attached leaves rows whose `category_id` resolves
 *  to nothing. `useUnarchiveCostCategory` is its mandatory other half -- an archive
 *  with no way back is a one-way door on a boolean column. */
export function useArchiveCostCategory() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (categoryId: string) =>
      unwrap(
        api.POST('/api/cost-categories/{category_id}/archive', {
          params: { path: { category_id: categoryId } },
        }),
      ),
    onSuccess: () => invalidateCategories(queryClient),
  })
}

export function useUnarchiveCostCategory() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (categoryId: string) =>
      unwrap(
        api.POST('/api/cost-categories/{category_id}/unarchive', {
          params: { path: { category_id: categoryId } },
        }),
      ),
    onSuccess: () => invalidateCategories(queryClient),
  })
}

/** Idempotent on the server: a second call answers `[]` rather than duplicating the
 *  taxonomy, so the button behind it cannot do damage by being pressed twice. */
export function useSeedCostCategories() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => unwrap(api.POST('/api/cost-categories/seed')),
    onSuccess: () => invalidateCategories(queryClient),
  })
}

export function usePeriodLocks(year?: number) {
  return useQuery({
    queryKey: queryKeys.periodLocks(year),
    queryFn: () =>
      unwrap(
        api.GET('/api/period-locks', {
          params: { query: year === undefined ? {} : { year } },
        }),
      ),
  })
}

/** Closing a month changes what can still be written into the past, so every hours and
 *  costs list is now answering a different question -- an entry that was editable a
 *  moment ago is not, and the row on screen would still offer to edit it. */
function invalidateAfterLockChange(queryClient: QueryClient) {
  void queryClient.invalidateQueries({ queryKey: ['period-locks'] })
  void queryClient.invalidateQueries({ queryKey: queryKeys.timeEntries() })
  void queryClient.invalidateQueries({ queryKey: queryKeys.costs() })
}

export function useClosePeriod() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: { year: number; month: number }) =>
      unwrap(api.POST('/api/period-locks', { body })),
    onSuccess: () => invalidateAfterLockChange(queryClient),
  })
}

export function useReopenPeriod() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ year, month }: { year: number; month: number }) =>
      unwrap(
        api.DELETE('/api/period-locks/{year}/{month}', { params: { path: { year, month } } }),
      ),
    onSuccess: () => invalidateAfterLockChange(queryClient),
  })
}

export function useSetUserRates(userId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: UserRatesBody) =>
      unwrap(
        api.PUT('/api/users/{user_id}/rates', {
          params: { path: { user_id: userId } },
          body,
        }),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.users })
      // Deliberately does NOT invalidate any time-entry cache: a rate change cannot
      // move an already-written row (§5), so refetching them would suggest it might.
      // `['deal-rates']` is the exception and is not a contradiction -- it is the
      // "what would a *new* entry freeze" preview, which this genuinely changes.
      void queryClient.invalidateQueries({ queryKey: ['deal-rates'] })
    },
  })
}

export function useSetDealRate(dealId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: DealRateBody) =>
      unwrap(
        api.PUT('/api/deals/{deal_id}/rate', {
          params: { path: { deal_id: dealId } },
          body,
        }),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.deal(dealId) })
      // The bare prefix, not `queryKeys.deals()`: that helper's second element is the
      // filter object, and an exact `['deals', {}]` would miss every list fetched with
      // a filter -- including the one `RatesPanel` itself is showing.
      void queryClient.invalidateQueries({ queryKey: ['deals'] })
      void queryClient.invalidateQueries({ queryKey: ['deal-rates'] })
    },
  })
}
