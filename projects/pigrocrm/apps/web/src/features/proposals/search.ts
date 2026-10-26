import type { ProposalStato } from './fields'

export type ProposalsSearch = { stato: ProposalStato }

/**
 * `?stato=in_attesa|accettata|rifiutata`, defaulting to the queue itself. Anything else
 * (a mistyped bookmark, an old link) lands on the queue rather than on an error page: the
 * tab is a view, not a record, so there is nothing for a bad value to be wrong about.
 */
export function validateProposalsSearch(search: Record<string, unknown>): ProposalsSearch {
  const stato = search.stato
  return { stato: stato === 'accettata' || stato === 'rifiutata' ? stato : 'in_attesa' }
}
