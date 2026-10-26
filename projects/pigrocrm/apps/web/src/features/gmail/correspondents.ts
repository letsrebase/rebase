import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, toProblem, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { queryKeys } from '@/lib/query'

export type DiscoveryReport = components['schemas']['DiscoveryReport']
export type DiscoveredCorrespondent = components['schemas']['DiscoveredCorrespondent']

/** Under `gmail`, like every key of this feature; `useDisconnectGmail` removes this
 *  prefix, so a disconnect never leaves the old mailbox's addresses on a customer. */
export const correspondentsPrefix = ['gmail', 'correspondents'] as const

export const correspondentKeys = {
  customer: (customerId: string) => [...correspondentsPrefix, customerId] as const,
}

/**
 * Who at a customer's domain the connected mailbox has written to or heard from
 * (REB-766). Each read asks Gmail for every conversation with that domain, a few seconds
 * and the owner's quota, so it only runs once `enabled` says a person asked: opening the
 * Email tab never spends it. Not read again on focus, and not retried on a refusal, which
 * is a sentence to show (no domain on the customer, a lapsed consent) rather than a
 * glitch to hide.
 */
export function useCustomerCorrespondents(customerId: string, enabled: boolean) {
  return useQuery({
    queryKey: correspondentKeys.customer(customerId),
    queryFn: () =>
      unwrap(
        api.GET('/api/gmail/customers/{customer_id}/correspondents', {
          params: { path: { customer_id: customerId } },
        }),
      ),
    enabled,
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
    retry: false,
  })
}

/** Files one found address as a person of the customer. The answer already in hand is
 *  marked rather than read again, which would ask Gmail about the whole domain a second
 *  time; the customer's people are stale afterwards, so the Persone card above shows
 *  the new one. The refusal that names the address (`indirizzo` in the problem
 *  document) means it was filed since the read (a second tab, an import): the row is
 *  marked all the same, so it stops offering what the book already holds. Any other
 *  refusal, a customer whose domain was cleared since, leaves the row as it was. */
export function useAddCorrespondent(customerId: string) {
  const queryClient = useQueryClient()
  function markFiled(indirizzo: string) {
    queryClient.setQueryData<DiscoveryReport>(
      correspondentKeys.customer(customerId),
      (report) =>
        report && {
          ...report,
          corrispondenti: report.corrispondenti.map((found) =>
            found.indirizzo === indirizzo ? { ...found, gia_in_anagrafica: true } : found,
          ),
        },
    )
    void queryClient.invalidateQueries({ queryKey: queryKeys.people() })
  }
  return useMutation({
    mutationFn: (body: { indirizzo: string; nome: string }) =>
      unwrap(
        api.POST('/api/customers/{customer_id}/people-from-mail', {
          params: { path: { customer_id: customerId } },
          body,
        }),
      ),
    onSuccess: (_person, body) => markFiled(body.indirizzo),
    onError: (error, body) => {
      const problem = toProblem(error)
      if (problem.status === 409 && problem.indirizzo === body.indirizzo) markFiled(body.indirizzo)
    },
  })
}
