import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/lib/api'
import { queryKeys } from '@/lib/query'

/** The occurrences of one contract's recurring fees, as the server draws the window (the last
 *  90 days and the next 180 by default). Always asked again on mount: the drafts are issued and
 *  deleted on other pages, and a cached answer would offer the button for a fee that is already
 *  on an invoice. */
export function useFeeOccurrences(contractId: string) {
  return useQuery({
    queryKey: queryKeys.contractFeeOccurrences(contractId),
    staleTime: 0,
    queryFn: () =>
      unwrap(
        api.GET('/api/contracts/{contract_id}/fee-occurrences', {
          params: { path: { contract_id: contractId } },
        }),
      ),
  })
}

/** The draft that bills one occurrence. What it moves is wider than the section that asked: the
 *  occurrence is no longer to bill, the invoices list has a new row and the contract's own
 *  timeline says so. */
export function useBillFee(contractId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (occurrence: { rate_card_id: string; period_start: string }) =>
      unwrap(
        api.POST('/api/contracts/{contract_id}/invoice-fee', {
          params: { path: { contract_id: contractId } },
          body: occurrence,
        }),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.contract(contractId) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.invoices() })
    },
  })
}
