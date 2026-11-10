import { useQuery } from '@tanstack/react-query'
import { api, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { DASHBOARD_STALE_MS } from '@/features/dashboard/queries'
import { useLegalEntity } from '@/lib/legalEntity'
import { queryKeys } from '@/lib/query'

export type CashSummary = components['schemas']['CashSummary']

/** The phone Home's money figures. `Tutte` sends nothing; one entity sends its id. */
export function useCashSummary(enabled: boolean) {
  const { selected } = useLegalEntity()
  const params: { legal_entity_id?: string } = selected === null ? {} : { legal_entity_id: selected }
  return useQuery({
    queryKey: queryKeys.dashboard('cash', { ...params }),
    queryFn: () => unwrap(api.GET('/api/cash/summary', { params: { query: params } })),
    staleTime: DASHBOARD_STALE_MS,
    enabled,
  })
}
