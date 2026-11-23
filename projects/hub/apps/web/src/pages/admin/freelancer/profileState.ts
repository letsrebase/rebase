import { useQuery } from '@tanstack/react-query'
import { admin, ApiError, type WorkProfileAdmin } from '@/lib/api'
import { keys } from '@/lib/query-keys'

/** How the profile stands, in the admin's words: «Da rivedere» is a draft not yet past its 12 months. */
export function profileStandingWord(profile: Pick<WorkProfileAdmin, 'state' | 'expired'>): string {
  if (profile.expired) return 'Scaduto'
  return { draft: 'Da rivedere', approved: 'Approvato', rejected: 'Rifiutato' }[profile.state]
}

/** A draft that an admin can still decide on. */
export const isDecidable = (profile: Pick<WorkProfileAdmin, 'state' | 'expired'>) =>
  profile.state === 'draft' && !profile.expired

/** What each anonymity rule looks for, in the admin's words. */
export const GUARD_RULES: Record<string, string> = {
  link_or_email: 'un indirizzo web o una email',
  email: 'l’email della persona',
  phone: 'un telefono della persona',
  tax_id: 'un codice fiscale o una partita IVA',
  name_account_site_or_client: 'il nome, un account, un sito o un cliente della persona',
}

/** The words the guard's field names go by in the checks list. */
export function guardField(field: string): string {
  if (field === 'summary') return 'il riassunto'
  if (field.includes('.label')) return 'un ambito di lavoro'
  if (field.endsWith('.domain')) return 'il dominio di un progetto'
  return field
}

/** The profile of a talent as the page reads it: `null` when the person sent none (the API's
 *  404), the read otherwise, and whether the read failed for any other reason. */
export function useWorkProfile(freelancerId: string) {
  const read = useQuery({
    queryKey: keys.workProfile(freelancerId),
    queryFn: () => admin.workProfile(freelancerId),
    retry: false,
  })
  const none = read.isError && read.error instanceof ApiError && read.error.status === 404
  return {
    pending: read.isPending,
    failed: read.isError && !none,
    profile: read.data ?? null,
  }
}
