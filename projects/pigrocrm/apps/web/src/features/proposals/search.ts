import type { ProposalStatus } from './fields'

export type ProposalsSearch = { status: ProposalStatus }

/**
 * `?status=pending|accepted|rejected`, defaulting to the queue itself. Anything else
 * (a mistyped bookmark, an old link) lands on the queue rather than on an error page: the
 * tab is a view, not a record, so there is nothing for a bad value to be wrong about.
 */
export function validateProposalsSearch(search: Record<string, unknown>): ProposalsSearch {
  const status = search.status
  return { status: status === 'accepted' || status === 'rejected' ? status : 'pending' }
}
