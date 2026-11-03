import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { queryKeys } from '@/lib/query'
import {
  defaultContractFormValues,
  toContractBody,
  type ContractFormValues,
} from '@/features/contracts/contractFormValues'

/** What converting a won deal into a contract would start from (`GET
 *  /api/deals/{id}/contract-proposal`): the title, start and end the deal suggests and the
 *  rate cards its lines become. The server computes it; nothing here adds up a line. */
export type ContractProposal = components['schemas']['ContractFromDealProposal']
export type ConvertDealBody = components['schemas']['ContractFromDeal']

/**
 * The proposal, fetched when the dialog opens and never kept: `gcTime: 0` so the next open
 * reads the deal as it is then (a line edited, or a stage moved, in between) rather than
 * the answer of the last time. A refusal (a deal that is not won, with no lines, or
 * already converted) is the query's error, which the dialog prints.
 */
export function useContractProposal(dealId: string, enabled: boolean) {
  return useQuery({
    enabled,
    gcTime: 0,
    queryKey: queryKeys.dealContractProposal(dealId),
    queryFn: () =>
      unwrap(
        api.GET('/api/deals/{deal_id}/contract-proposal', {
          params: { path: { deal_id: dealId } },
        }),
      ),
  })
}

export function useConvertDeal(dealId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: ConvertDealBody) =>
      unwrap(
        api.POST('/api/deals/{deal_id}/contract', {
          params: { path: { deal_id: dealId } },
          body,
        }),
      ),
    onSuccess: () => {
      // The deal now carries `contract_id` and a `contract` timeline row; the contracts
      // lists have one more.
      void queryClient.invalidateQueries({ queryKey: queryKeys.deal(dealId) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.timeline('deal', dealId) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.contracts() })
    },
  })
}

/** The form's first state: the contract form's own defaults (no renewal, 30 days' notice,
 *  monthly billing, expenses not reimbursed) under what the deal proposes. */
export function proposalToFormValues(proposal: ContractProposal): ContractFormValues {
  return {
    ...defaultContractFormValues(),
    title: proposal.title,
    start: proposal.start,
    end: proposal.end ?? '',
  }
}

const BODY_KEYS = [
  'title',
  'start',
  'end',
  'renewal_type',
  'renewal_notice_days',
  'termination_notice_days',
  'billing_cadence',
  'expense_policy',
] as const

/** The request body: the eight fields a conversion takes, built by the contract form's own
 *  `toContractBody` so the two dialogs read a notice or an expense policy the same way. */
export function toConvertBody(values: ContractFormValues): ConvertDealBody {
  const full = toContractBody(values, undefined, () => false) as Record<string, unknown>
  return Object.fromEntries(BODY_KEYS.map((key) => [key, full[key]])) as unknown as ConvertDealBody
}
