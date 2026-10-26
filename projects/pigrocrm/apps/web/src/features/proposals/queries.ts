import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, toProblem, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { queryKeys } from '@/lib/query'
import type { Proposal, ProposalStato } from './fields'

/** The API's own page size ceiling (`ProposalListQuery.limit`): a review queue is bounded
 *  by what agents leave pending, and this is how far one request can see. */
export const QUEUE_LIMIT = 200

type ProposalAcceptBody = components['schemas']['ProposalAccept']
type ProposalRejectBody = components['schemas']['ProposalReject']

/**
 * One list per tab. The queue is oldest first, the order proposals arrived in; the decided
 * tabs ask for the most recent decision first (`ordine`), so a space with more than
 * `QUEUE_LIMIT` decided proposals still shows the latest ones and not the first ones.
 */
export function useProposals(stato: ProposalStato, options: { enabled?: boolean } = {}) {
  return useQuery({
    enabled: options.enabled ?? true,
    queryKey: queryKeys.proposals(stato),
    queryFn: () =>
      unwrap(
        api.GET('/api/proposals', {
          params: {
            query: {
              stato,
              ordine: stato === 'in_attesa' ? 'arrivo' : 'decisione',
              limit: QUEUE_LIMIT,
            },
          },
        }),
      ),
  })
}

/** How many proposals wait: the Home's notice reads it, and a page that has the queue
 *  already open shares the same cache entry. */
export function usePendingProposals(options: { enabled?: boolean } = {}) {
  return useProposals('in_attesa', options)
}

export function useProposal(proposalId: string) {
  return useQuery({
    enabled: proposalId !== '',
    queryKey: queryKeys.proposal(proposalId),
    queryFn: () =>
      unwrap(
        api.GET('/api/proposals/{proposal_id}', {
          params: { path: { proposal_id: proposalId } },
        }),
      ),
  })
}

/** Filed, then accepted or rejected: where a rejection's reason is kept (the `proposals`
 *  table has no column for it). Read only once the proposal is decided. */
export function useProposalTimeline(proposalId: string, enabled: boolean) {
  return useQuery({
    enabled: enabled && proposalId !== '',
    queryKey: queryKeys.proposalTimeline(proposalId),
    queryFn: () =>
      unwrap(
        api.GET('/api/proposals/{proposal_id}/timeline', {
          params: { path: { proposal_id: proposalId } },
        }),
      ),
  })
}

/** The contract a day proposal is for, read for its title only. */
export function useProposalContract(contractId: string | null) {
  return useQuery({
    enabled: contractId !== null,
    queryKey: queryKeys.proposalContract(contractId ?? ''),
    queryFn: () =>
      unwrap(
        api.GET('/api/contracts/{contract_id}', {
          params: { path: { contract_id: contractId ?? '' } },
        }),
      ),
  })
}

/**
 * What a decision, or a refused one, leaves stale.
 *
 * A decision moves a proposal out of the queue, and an accepted one writes a contract or a
 * day and re-points the source document at the contract, so everything a proposal's
 * decision can touch is refetched by prefix: the queue, the proposal itself, the documents
 * (the list and the document's own page) and the timelines.
 *
 * A **409** is the other half: someone else (another tab, or an assistant over MCP)
 * decided the proposal first, so what this page holds is wrong about its state and its
 * buttons would answer 409 again. The proposal and the queue are read again and the page
 * redraws as a decided one.
 */
function useDecisionCache(proposalId: string) {
  const queryClient = useQueryClient()
  return {
    onSuccess: (decided: Proposal) => {
      queryClient.setQueryData(queryKeys.proposal(decided.id), decided)
      void queryClient.invalidateQueries({ queryKey: ['proposals'] })
      if (decided.stato === 'accettata') {
        void queryClient.invalidateQueries({ queryKey: ['documents'] })
        void queryClient.invalidateQueries({ queryKey: ['document'] })
        void queryClient.invalidateQueries({ queryKey: ['timeline'] })
      }
    },
    onError: (error: unknown) => {
      if (toProblem(error).status !== 409) return
      void queryClient.invalidateQueries({ queryKey: queryKeys.proposal(proposalId) })
      void queryClient.invalidateQueries({ queryKey: ['proposals'] })
      void queryClient.invalidateQueries({ queryKey: queryKeys.proposalTimeline(proposalId) })
    },
  }
}

export function useAcceptProposal(proposalId: string) {
  return useMutation({
    mutationFn: (body: ProposalAcceptBody) =>
      unwrap(
        api.POST('/api/proposals/{proposal_id}/accept', {
          params: { path: { proposal_id: proposalId } },
          body,
        }),
      ),
    ...useDecisionCache(proposalId),
  })
}

export function useRejectProposal(proposalId: string) {
  return useMutation({
    mutationFn: (body: ProposalRejectBody) =>
      unwrap(
        api.POST('/api/proposals/{proposal_id}/reject', {
          params: { path: { proposal_id: proposalId } },
          body,
        }),
      ),
    ...useDecisionCache(proposalId),
  })
}
