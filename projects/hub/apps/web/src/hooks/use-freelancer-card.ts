import { useQuery } from '@tanstack/react-query'
import { admin } from '@/lib/api'
import { keys } from '@/lib/query-keys'

/**
 * The admin's read of a talent's card, with the CV's persona and the differences from the
 * profile (REB-855). One query key for the page: «Scheda anonima» and the
 * profile's CV rows all read the same cached answer, so they cannot disagree. A deleted
 * card's route answers a 404, which reads as an error here.
 */
export function useFreelancerCard(freelancerId: string) {
  return useQuery({
    queryKey: keys.freelancerCard(freelancerId),
    queryFn: () => admin.freelancerCard(freelancerId),
  })
}
