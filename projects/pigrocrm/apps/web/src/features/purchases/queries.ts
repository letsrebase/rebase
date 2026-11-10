import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
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
