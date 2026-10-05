import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useLegalEntityScope } from '@/lib/legalEntity'
import { api, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { queryKeys } from '@/lib/query'

/** Same rule as `features/time/queries.ts`: the wire shapes come from the generated
 *  OpenAPI schema, and every `Decimal` (`importo` above all) arrives as a `string`. */
export type Cost = components['schemas']['CostRead']
export type CostCategory = components['schemas']['CostCategoryRead']

type CostCreateBody = components['schemas']['CostCreate']
type CostUpdateBody = components['schemas']['CostUpdate']

export interface CostsListParams {
  deal_id?: string
  azienda_id?: string
  solo_generali?: boolean
  category_id?: string
  da?: string
  a?: string
}

export function useCosts(params: CostsListParams = {}) {
  // The sidebar's azienda (REB-625) on the general costs, not on a deal's: under
  // azienda A the page shows A's own expenses, and the shared ones (no azienda) only
  // under «Tutte le aziende», which is what the server means by the filter.
  const scoped = useLegalEntityScope(params)
  return useQuery({
    queryKey: queryKeys.costs(scoped),
    queryFn: () => unwrap(api.GET('/api/costs', { params: { query: { ...scoped, limit: 200 } } })),
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

/** A cost changes the deal's P&L, so its summary is stale the moment one is written.
 *  A general expense (`deal_id: null`, §7.4) belongs to no deal and leaves every deal
 *  summary alone. */
function invalidateCosts(queryClient: ReturnType<typeof useQueryClient>, dealId?: string | null) {
  void queryClient.invalidateQueries({ queryKey: queryKeys.costs() })
  if (dealId) {
    void queryClient.invalidateQueries({ queryKey: queryKeys.dealTimeSummary(dealId) })
  }
}

export function useCreateCost() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      unwrap(api.POST('/api/costs', { body: body as unknown as CostCreateBody })),
    onSuccess: (cost) => invalidateCosts(queryClient, cost.deal_id),
  })
}

export function useUpdateCost(costId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      unwrap(
        api.PATCH('/api/costs/{cost_id}', {
          params: { path: { cost_id: costId } },
          body: body as unknown as CostUpdateBody,
        }),
      ),
    onSuccess: (cost) => invalidateCosts(queryClient, cost.deal_id),
  })
}

export function useDeleteCost() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (costId: string) =>
      unwrap(api.DELETE('/api/costs/{cost_id}', { params: { path: { cost_id: costId } } })),
    onSuccess: () => invalidateCosts(queryClient),
  })
}
