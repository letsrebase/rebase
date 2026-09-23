import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { queryKeys } from '@/lib/query'

export type SuggestedCustomer = components['schemas']['SuggestedCustomer']
export type CustomersFromSuggestions = components['schemas']['CustomersFromSuggestions']

/** Under `gmail`, like every key of this feature, so a disconnect that clears the
 *  feature's cache clears the proposals with it. */
export const suggestionKeys = { all: ['gmail', 'customer-suggestions'] as const }

/**
 * The customers the connected mailbox proposes (REB-223). Each read asks Gmail about a
 * year of sent mail, a few seconds and the owner's quota, so it is read once per visit:
 * not again on focus, and not retried on a refusal, which is a sentence to show
 * (a lapsed consent, no mailbox) rather than a glitch to hide.
 */
export function useCustomerSuggestions() {
  return useQuery({
    queryKey: suggestionKeys.all,
    queryFn: () => unwrap(api.GET('/api/gmail/customer-suggestions')),
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
    retry: false,
  })
}

/** Creates what was ticked, customers and people together. Every list they appear in is
 *  stale afterwards, the Home's first steps included (they read the same keys). The
 *  proposals are not read again, which would ask Gmail about the whole year a second
 *  time: the imported domains are dropped from the answer already in hand, so the list
 *  never offers what was just created. */
export function useImportSuggestions() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: CustomersFromSuggestions) =>
      unwrap(api.POST('/api/customers/from-suggestions', { body })),
    onSuccess: (_created, body) => {
      const imported = new Set(body.clienti.map((cliente) => cliente.dominio))
      queryClient.setQueryData<SuggestedCustomer[]>(suggestionKeys.all, (proposals) =>
        proposals?.filter((proposal) => !imported.has(proposal.dominio)),
      )
      void queryClient.invalidateQueries({ queryKey: queryKeys.customers() })
      void queryClient.invalidateQueries({ queryKey: queryKeys.people() })
    },
  })
}
