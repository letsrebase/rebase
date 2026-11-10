import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from '@tanstack/react-query'
import { useLegalEntityScope } from '@/lib/legalEntity'
import { api, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { asBody } from '@/lib/formValues'
import { queryKeys } from '@/lib/query'

/** Same rule as `features/time/queries.ts`: the wire shapes come from the generated
 *  OpenAPI schema, and every `Decimal` (`amount` above all) arrives as a `string`. */
export type Purchase = components['schemas']['PurchaseRead']
export type PurchaseKind = Purchase['kind']
export type PurchaseStatus = Purchase['status']
export type PurchasePage = components['schemas']['PurchasePage']
export type CostCategory = components['schemas']['CostCategoryRead']

type PurchaseCreateBody = components['schemas']['PurchaseCreate']
type PurchaseUpdateBody = components['schemas']['PurchaseUpdate']

export interface PurchasesListParams {
  kind?: PurchaseKind
  status?: PurchaseStatus
  supplier_id?: string
  deal_id?: string
  legal_entity_id?: string
  only_general?: boolean
  category_id?: string
  from?: string
  to?: string
}

export function usePurchases(params: PurchasesListParams = {}, options: { enabled?: boolean } = {}) {
  // The sidebar's azienda (REB-625) on the general purchases, not on a deal's: under
  // azienda A the page shows A's own expenses, and the shared ones (no azienda) only
  // under «Tutte le aziende», which is what the server means by the filter.
  const scoped = useLegalEntityScope(params)
  return useQuery({
    enabled: options.enabled ?? true,
    queryKey: queryKeys.purchases(scoped),
    queryFn: () =>
      unwrap(api.GET('/api/purchases', { params: { query: { ...scoped, limit: 200 } } })),
  })
}

/** The page size of «Acquisti»: the API accepts up to 200, a screen reads 50. */
export const PURCHASES_PAGE_SIZE = 50

/**
 * The list of «Acquisti», a page at a time with the cursor, and the figures of the whole
 * selection the server answers with (`total_count`, `total_amount`): never summed from the rows
 * on screen, which are only the pages read so far.
 *
 * Its key carries `pages` so it can never share a cache entry with `usePurchases`, whose data
 * is a single page and not a list of them; both sit under the `['purchases']` prefix, which a
 * write invalidates.
 */
export function usePurchaseList(params: PurchasesListParams = {}) {
  const scoped = useLegalEntityScope(params)
  const query = useInfiniteQuery({
    queryKey: queryKeys.purchases({ pages: scoped }),
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET('/api/purchases', {
          params: { query: { ...scoped, limit: PURCHASES_PAGE_SIZE, cursor: pageParam } },
        }),
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  })
  const pages = query.data?.pages
  const last = pages?.at(-1)
  return {
    items: pages?.flatMap((page) => page.items) ?? [],
    /** Undefined until the first page lands, so a screen never draws a figure it has not got. */
    totalCount: last?.total_count,
    totalAmount: last?.total_amount,
    isLoading: query.isLoading,
    isError: query.isError,
    error: query.error,
    hasMore: query.hasNextPage,
    isFetchingMore: query.isFetchingNextPage,
    loadMore: () => void query.fetchNextPage(),
    retry: () => void query.refetch(),
  }
}

/** One purchase, for its own page and for the form that edits it. Empty id: no request. */
export function usePurchase(purchaseId: string) {
  return useQuery({
    enabled: purchaseId !== '',
    queryKey: queryKeys.purchase(purchaseId),
    queryFn: () =>
      unwrap(
        api.GET('/api/purchases/{purchase_id}', {
          params: { path: { purchase_id: purchaseId } },
        }),
      ),
  })
}

export function useCostCategories(includeArchived = false) {
  return useQuery({
    queryKey: queryKeys.costCategories(includeArchived),
    queryFn: () =>
      unwrap(
        api.GET('/api/cost-categories', {
          params: { query: { include_archived: includeArchived } },
        }),
      ),
  })
}

/** A purchase changes the deal's P&L, so its summary is stale the moment one is written.
 *  A general purchase (`deal_id: null`, §7.4) belongs to no deal and leaves every deal
 *  summary alone. */
function invalidatePurchases(queryClient: QueryClient, dealId?: string | null) {
  void queryClient.invalidateQueries({ queryKey: queryKeys.purchases() })
  // The page of a purchase reads it by its own key, which a list's prefix does not reach.
  void queryClient.invalidateQueries({ queryKey: ['purchase'] })
  if (dealId) {
    void queryClient.invalidateQueries({ queryKey: queryKeys.dealTimeSummary(dealId) })
  }
}

export function useCreatePurchase() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      unwrap(api.POST('/api/purchases', { body: asBody<PurchaseCreateBody>(body) })),
    onSuccess: (purchase) => invalidatePurchases(queryClient, purchase.deal_id),
  })
}

export function useUpdatePurchase(purchaseId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      unwrap(
        api.PATCH('/api/purchases/{purchase_id}', {
          params: { path: { purchase_id: purchaseId } },
          body: asBody<PurchaseUpdateBody>(body),
        }),
      ),
    onSuccess: (purchase) => invalidatePurchases(queryClient, purchase.deal_id),
  })
}

/** «Elimina»: the archive (`deleted_at`), reversible with `useRestorePurchase`. */
export function useDeletePurchase() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (purchaseId: string) =>
      unwrap(
        api.DELETE('/api/purchases/{purchase_id}', {
          params: { path: { purchase_id: purchaseId } },
        }),
      ),
    onSuccess: () => invalidatePurchases(queryClient),
  })
}

export function useRestorePurchase() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (purchaseId: string) =>
      unwrap(
        api.POST('/api/purchases/{purchase_id}/restore', {
          params: { path: { purchase_id: purchaseId } },
        }),
      ),
    onSuccess: (purchase) => invalidatePurchases(queryClient, purchase.deal_id),
  })
}

/** «Annulla»: the purchase stays on the register with its status `cancelled`, which is not
 *  the archive: an annulled invoice is still a document that existed. */
export function useCancelPurchase() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (purchaseId: string) =>
      unwrap(
        api.POST('/api/purchases/{purchase_id}/cancel', {
          params: { path: { purchase_id: purchaseId } },
        }),
      ),
    onSuccess: (purchase) => invalidatePurchases(queryClient, purchase.deal_id),
  })
}
