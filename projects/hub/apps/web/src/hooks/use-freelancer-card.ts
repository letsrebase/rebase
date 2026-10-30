import { useQuery } from '@tanstack/react-query'
import { admin } from '@/lib/api'

/**
 * The admin's read of a talent's card, with the CV's persona and the differences from the
 * profile (REB-855). One query key for the page: «Scheda anonima» and the
 * profile's CV rows all read the same cached answer, so they cannot disagree.
 * `enabled` is off for a deleted card, whose card route answers a 404.
 */
export function useFreelancerCard(freelancerId: string, enabled = true) {
  return useQuery({
    queryKey: ['freelancer-card', freelancerId] as const,
    queryFn: () => admin.freelancerCard(freelancerId),
    enabled,
  })
}
