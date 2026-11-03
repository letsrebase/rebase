import { legacyParam } from '@/lib/legacySearch'
import type { ProposalStatus } from './fields'

export type ProposalsSearch = { status: ProposalStatus }

const LEGACY_STATUSES: Record<string, ProposalStatus> = {
  'in_attesa': 'pending',
  'accettata': 'accepted',
  'rifiutata': 'rejected',
}

/**
 * `?status=pending|accepted|rejected`, defaulting to the queue itself. Anything else
 * (a mistyped bookmark, an old link) lands on the queue rather than on an error page: the
 * tab is a view, not a record, so there is nothing for a bad value to be wrong about.
 */
export function validateProposalsSearch(search: Record<string, unknown>): ProposalsSearch {
  // Links written before the English rename carry the Italian parameter and tab words.
  const raw = legacyParam(search, 'status', 'stato')
  const status = LEGACY_STATUSES[String(raw)] ?? raw
  return { status: status === 'accepted' || status === 'rejected' ? status : 'pending' }
}
