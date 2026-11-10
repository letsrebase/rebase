import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { queryKeys } from '@/lib/query'

export type SuggestedOrganization = components['schemas']['SuggestedOrganization']
export type OrganizationsFromSuggestions = components['schemas']['OrganizationsFromSuggestions']

/** Under `gmail`, like every key of this feature, so a disconnect that clears the
 *  feature's cache clears the proposals with it. */
export const suggestionKeys = { all: ['gmail', 'organization-suggestions'] as const }

/**
 * The organizations the connected mailbox proposes (REB-223). Each read asks Gmail about a
 * year of sent mail, a few seconds and the owner's quota, so it is read once per visit:
 * not again on focus, and not retried on a refusal, which is a sentence to show
 * (a lapsed consent, no mailbox) rather than a glitch to hide.
 */
export function useOrganizationSuggestions() {
  return useQuery({
    queryKey: suggestionKeys.all,
    queryFn: () => unwrap(api.GET('/api/gmail/organization-suggestions')),
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
    retry: false,
  })
}

/** Creates what was ticked, organizations and people together. Every list they appear in is
 *  stale afterwards, the Home's first steps included (they read the same keys). The
 *  proposals are not read again, which would ask Gmail about the whole year a second
 *  time: the imported domains are dropped from the answer already in hand, so the list
 *  never offers what was just created. */
export function useImportSuggestions() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: OrganizationsFromSuggestions) =>
      unwrap(api.POST('/api/organizations/from-suggestions', { body })),
    onSuccess: (_created, body) => {
      const imported = new Set(body.organizations.map((customer) => customer.domain))
      queryClient.setQueryData<SuggestedOrganization[]>(suggestionKeys.all, (proposals) =>
        proposals?.filter((proposal) => !imported.has(proposal.domain)),
      )
      void queryClient.invalidateQueries({ queryKey: queryKeys.organizations() })
      void queryClient.invalidateQueries({ queryKey: queryKeys.people() })
    },
  })
}
