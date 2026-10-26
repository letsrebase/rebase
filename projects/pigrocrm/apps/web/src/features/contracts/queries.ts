import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { api, toProblem, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { queryKeys } from '@/lib/query'

/** Wire shapes straight from the generated schema, the same rule as every other
 *  `queries.ts` here: `packages/core/src/pigrocrm/core/contracts/schemas.py` is the one
 *  source, `pnpm generate:api` carries it, and every `Decimal` arrives as a `string`. */
export type Contract = components['schemas']['ContractRead']
export type ContractCreateBody = components['schemas']['ContractCreate']
export type ContractUpdateBody = components['schemas']['ContractUpdate']
export type ContractState = components['schemas']['ContractStateChange']['stato']
export type RateCard = components['schemas']['RateCardRead']
export type RateCardCreateBody = components['schemas']['RateCardCreate']
export type RateCardUpdateBody = components['schemas']['RateCardUpdate']
export type ContractExpense = components['schemas']['ContractExpenseRead']
export type ContractExpenseCreateBody = components['schemas']['ContractExpenseCreate']
export type ContractExpenseUpdateBody = components['schemas']['ContractExpenseUpdate']
export type RenewalAssumption = components['schemas']['RenewalAssumptionRead']
export type RenewalAssumptionBody = components['schemas']['RenewalAssumptionUpsert']
export type ContractProjection = components['schemas']['ContractProjectionRead']
export type ContractConcentration = components['schemas']['ContractConcentrationCap']

export function useCustomerContracts(customerId: string) {
  return useQuery({
    queryKey: queryKeys.contracts({ customer_id: customerId }),
    // Every page, newest first: a customer has a handful of contracts over its life, and
    // a tab that stopped at the first page would hide the older ones with no way to ask.
    queryFn: async () => {
      const items: Contract[] = []
      let cursor: string | undefined
      do {
        const page = await unwrap(
          api.GET('/api/contracts', {
            params: {
              query: { customer_id: customerId, limit: 200, sort: 'created_at', dir: 'desc', cursor },
            },
          }),
        )
        items.push(...page.items)
        cursor = page.next_cursor ?? undefined
      } while (cursor !== undefined)
      return { items }
    },
  })
}

export function useContract(contractId: string) {
  return useQuery({
    queryKey: queryKeys.contract(contractId),
    queryFn: () =>
      unwrap(
        api.GET('/api/contracts/{contract_id}', {
          params: { path: { contract_id: contractId } },
        }),
      ),
  })
}

/** A contract written anywhere refreshes the customer's list, the row itself and its
 *  timeline: the page, the tab and the history must not disagree about the same row. */
function invalidateContract(client: QueryClient, contractId: string) {
  void client.invalidateQueries({ queryKey: queryKeys.contract(contractId) })
  void client.invalidateQueries({ queryKey: queryKeys.contracts() })
  void client.invalidateQueries({ queryKey: queryKeys.timeline('contract', contractId) })
}

export function useCreateContract() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: ContractCreateBody) => unwrap(api.POST('/api/contracts', { body })),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.contracts() })
    },
  })
}

export function useUpdateContract(contractId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: ContractUpdateBody) =>
      unwrap(
        api.PATCH('/api/contracts/{contract_id}', {
          params: { path: { contract_id: contractId } },
          body,
        }),
      ),
    onSuccess: () => {
      // Under the contract's own key, so the programmed revenue (which reads its dates)
      // and the rest of the page's sections refresh with the row.
      invalidateContract(queryClient, contractId)
    },
  })
}

export function useSetContractState(contractId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (stato: ContractState) =>
      unwrap(
        api.POST('/api/contracts/{contract_id}/state', {
          params: { path: { contract_id: contractId } },
          body: { stato },
        }),
      ),
    onSuccess: () => invalidateContract(queryClient, contractId),
  })
}

// -- Tariffe ------------------------------------------------------------------------

export function useRateCards(contractId: string) {
  return useQuery({
    queryKey: queryKeys.contractRateCards(contractId),
    queryFn: () =>
      unwrap(
        api.GET('/api/contracts/{contract_id}/rate-cards', {
          params: { path: { contract_id: contractId } },
        }),
      ),
  })
}

/** A rate card moves what the projection programs, so both go stale together. */
function invalidateRates(client: QueryClient, contractId: string) {
  void client.invalidateQueries({ queryKey: queryKeys.contractRateCards(contractId) })
  void client.invalidateQueries({ queryKey: ['contract', contractId, 'projection'] })
  // The days analysis sets the realised rate against the card in force (REB-756).
  void client.invalidateQueries({ queryKey: ['dashboard'] })
  void client.invalidateQueries({ queryKey: queryKeys.timeline('contract', contractId) })
}

/** `chiudiPrecedente` asks the server to end the card in force on `body.valido_da` the
 *  day before it, in the same transaction; without it an overlap is refused. */
export function useCreateRateCard(contractId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      body,
      chiudiPrecedente,
    }: {
      body: RateCardCreateBody
      chiudiPrecedente: boolean
    }) =>
      unwrap(
        api.POST('/api/contracts/{contract_id}/rate-cards', {
          params: {
            path: { contract_id: contractId },
            query: { chiudi_precedente: chiudiPrecedente },
          },
          body,
        }),
      ),
    onSuccess: () => invalidateRates(queryClient, contractId),
  })
}

export function useUpdateRateCard(contractId: string, rateCardId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: RateCardUpdateBody) =>
      unwrap(
        api.PATCH('/api/contracts/{contract_id}/rate-cards/{rate_card_id}', {
          params: { path: { contract_id: contractId, rate_card_id: rateCardId } },
          body,
        }),
      ),
    onSuccess: () => invalidateRates(queryClient, contractId),
  })
}

// -- Spese ----------------------------------------------------------------------------

export function useContractExpenses(contractId: string) {
  return useQuery({
    queryKey: queryKeys.contractExpenses(contractId),
    queryFn: () =>
      unwrap(
        api.GET('/api/contracts/{contract_id}/expenses', {
          params: { path: { contract_id: contractId } },
        }),
      ),
  })
}

function invalidateExpenses(client: QueryClient, contractId: string) {
  void client.invalidateQueries({ queryKey: queryKeys.contractExpenses(contractId) })
  void client.invalidateQueries({ queryKey: queryKeys.timeline('contract', contractId) })
}

export function useCreateContractExpense(contractId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: ContractExpenseCreateBody) =>
      unwrap(
        api.POST('/api/contracts/{contract_id}/expenses', {
          params: { path: { contract_id: contractId } },
          body,
        }),
      ),
    onSuccess: () => invalidateExpenses(queryClient, contractId),
  })
}

export function useUpdateContractExpense(contractId: string, expenseId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: ContractExpenseUpdateBody) =>
      unwrap(
        api.PATCH('/api/contracts/{contract_id}/expenses/{expense_id}', {
          params: { path: { contract_id: contractId, expense_id: expenseId } },
          body,
        }),
      ),
    onSuccess: () => invalidateExpenses(queryClient, contractId),
  })
}

// -- Rinnovo e ricavi programmati -------------------------------------------------------

/** `null` when nobody has recorded an assumption yet: the API answers 404 for «none
 *  recorded», which is a state of this contract and not a failure of the request. */
export function useRenewalAssumption(contractId: string) {
  return useQuery({
    queryKey: queryKeys.contractAssumption(contractId),
    queryFn: async (): Promise<RenewalAssumption | null> => {
      try {
        return await unwrap(
          api.GET('/api/contracts/{contract_id}/renewal-assumption', {
            params: { path: { contract_id: contractId } },
          }),
        )
      } catch (error) {
        const problem = toProblem(error)
        if (problem.status === 404 && problem.entity === 'renewal_assumption') return null
        throw error
      }
    },
  })
}

export function useSetRenewalAssumption(contractId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: RenewalAssumptionBody) =>
      unwrap(
        api.PUT('/api/contracts/{contract_id}/renewal-assumption', {
          params: { path: { contract_id: contractId } },
          body,
        }),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.contractAssumption(contractId) })
      void queryClient.invalidateQueries({ queryKey: ['contract', contractId, 'projection'] })
      void queryClient.invalidateQueries({ queryKey: queryKeys.timeline('contract', contractId) })
    },
  })
}

export interface ProjectionWindow {
  da: string
  a: string
}

export function useContractProjection(
  contractId: string,
  window: ProjectionWindow,
  enabled: boolean,
) {
  return useQuery({
    enabled,
    queryKey: queryKeys.contractProjection(contractId, window),
    queryFn: () =>
      unwrap(
        api.GET('/api/contracts/{contract_id}/projection', {
          params: { path: { contract_id: contractId }, query: window },
        }),
      ),
  })
}

export function useContractConcentration(contractId: string, enabled: boolean) {
  return useQuery({
    enabled,
    queryKey: queryKeys.contractConcentration(contractId),
    queryFn: () =>
      unwrap(
        api.GET('/api/contracts/{contract_id}/concentration', {
          params: { path: { contract_id: contractId } },
        }),
      ),
  })
}
