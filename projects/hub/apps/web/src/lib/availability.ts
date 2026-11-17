import { useQuery, type UseQueryResult } from '@tanstack/react-query'
import { admin, type TalentAvailability, type TalentCommitments } from './api'
import { keys } from './query-keys'

/** How long a read of the CRM is good for: a list re-rendered, filtered or paged within a
 *  minute does not ask it again. */
const FRESH_MS = 60_000

/**
 * Who is already committed, as the CRM says (REB-1120): one read of the CRM for every card on
 * the page, apart from the list itself, so the list shows its rows while the CRM is slow and
 * stays usable when it is down. Not retried: the door already asked twice.
 */
export function useTalentAvailability(): UseQueryResult<TalentCommitments> {
  return useQuery({
    queryKey: keys.talentAvailability,
    queryFn: () => admin.talentAvailability(),
    staleTime: FRESH_MS,
    retry: false,
  })
}

/** A card's availability in the CRM's answer; a card it does not name is free. */
export function availabilityOf(read: TalentCommitments, freelancerId: string): TalentAvailability {
  return (
    read.freelancers[freelancerId] ?? {
      status: 'free',
      committed_days: '0',
      committed_from: null,
      free_from: null,
      until: read.date_to,
    }
  )
}
