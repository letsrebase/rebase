import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/lib/api'
import { useLegalEntity } from '@/lib/legalEntity'
import { queryKeys } from '@/lib/query'
import type { Contract } from '@/features/contracts/queries'
import type { WorkUnitCreateBody, WorkUnitRow, WorkUnitStato } from './labels'

export interface DaysListParams {
  contractId?: string
  da?: string
  a?: string
  stato?: WorkUnitStato
}

/**
 * The days of a contract, or of a window of dates: the API refuses a list with neither,
 * so the hook does not run until the caller has one. Newest first, as the API answers.
 * The sidebar's azienda narrows the window (REB-625), and is part of the key.
 */
export function useWorkUnits(params: DaysListParams, options: { enabled?: boolean } = {}) {
  const { selected } = useLegalEntity()
  const bounded = params.contractId !== undefined || params.da !== undefined || params.a !== undefined
  return useQuery({
    enabled: (options.enabled ?? true) && bounded,
    queryKey: queryKeys.workUnits({ ...params, azienda: selected }),
    queryFn: () =>
      unwrap(
        api.GET('/api/work-units', {
          params: {
            query: {
              contract_id: params.contractId,
              da: params.da,
              a: params.a,
              stato: params.stato,
              azienda_id: selected ?? undefined,
            },
          },
        }),
      ),
  })
}

export function useWorkUnit(workUnitId: string) {
  return useQuery({
    enabled: workUnitId !== '',
    queryKey: queryKeys.workUnit(workUnitId),
    queryFn: () =>
      unwrap(
        api.GET('/api/work-units/{work_unit_id}', {
          params: { path: { work_unit_id: workUnitId } },
        }),
      ),
  })
}

/**
 * The contracts a day can be recorded against: the ones that have not ended, in the
 * sidebar's azienda, every page of them (a long history of ended contracts must not push a
 * live one off the first page and out of the picker). A contract in `bozza` is offered too,
 * because that is the state every contract is born in and the ledger does not wait for it to
 * be activated.
 *
 * Under the contracts feature's own `['contracts']` prefix, so a contract written anywhere
 * (its terms, its state, its approval flag) refreshes what the day form believes about it.
 */
export function useDayContracts() {
  const { selected } = useLegalEntity()
  return useQuery({
    queryKey: queryKeys.contracts({ for: 'days', azienda: selected }),
    queryFn: async () => {
      const items: Contract[] = []
      let cursor: string | undefined
      do {
        const page = await unwrap(
          api.GET('/api/contracts', {
            params: {
              query: { azienda_id: selected ?? undefined, limit: 200, sort: 'titolo', cursor },
            },
          }),
        )
        items.push(...page.items)
        cursor = page.next_cursor ?? undefined
      } while (cursor !== undefined)
      return items.filter((contract) => contract.stato !== 'terminato' && contract.stato !== 'scaduto')
    },
  })
}

/**
 * `useDayContracts`, plus the contract the page was opened for when it is not among them:
 * a link from a day's page to an ended contract must open with that contract chosen and not
 * a blank select, and the list rightly leaves ended contracts out of what a new day can be
 * recorded on. Reads the one contract only when the list is in and does not hold it, under the
 * contracts feature's own key for it.
 */
export function useDayContractOptions(selectedId: string | undefined) {
  const list = useDayContracts()
  const missing =
    selectedId !== undefined && list.isSuccess && !list.data.some((contract) => contract.id === selectedId)
  const extra = useQuery({
    enabled: missing,
    queryKey: queryKeys.contract(selectedId ?? ''),
    queryFn: () =>
      unwrap(
        api.GET('/api/contracts/{contract_id}', {
          params: { path: { contract_id: selectedId ?? '' } },
        }),
      ),
  })
  return {
    ...list,
    data: list.data === undefined ? undefined : extra.data ? [...list.data, extra.data] : list.data,
  }
}

/** One contract's rate cards, under the contracts feature's key so a rate written on its page
 *  is what the form shows next. */
export function useDayRateCards(contractId: string) {
  return useQuery({
    enabled: contractId !== '',
    queryKey: queryKeys.contractRateCards(contractId),
    queryFn: () =>
      unwrap(
        api.GET('/api/contracts/{contract_id}/rate-cards', {
          params: { path: { contract_id: contractId } },
        }),
      ),
  })
}

/**
 * The days of a contract that can still use an approval, for the approval form: the proposed
 * ones and the ones worked without approval, each asked for by state so a long ledger of
 * approved and billed days cannot push them out of the newest two hundred, plus the days the
 * form was opened for (read by id when they are not in those lists: a day older than the
 * lists reach must stay reachable from its own page).
 */
export function useApprovalCandidates(contractId: string, openedFor: readonly string[]) {
  const proposed = useWorkUnits({ contractId, stato: 'proposto' }, { enabled: contractId !== '' })
  const flagged = useWorkUnits(
    { contractId, stato: 'lavorato_senza_approvazione' },
    { enabled: contractId !== '' },
  )
  const listed = new Set([...(proposed.data?.items ?? []), ...(flagged.data?.items ?? [])].map((day) => day.id))
  const missing = contractId === '' || !proposed.isSuccess || !flagged.isSuccess ? [] : openedFor.filter((id) => !listed.has(id))
  const extra = useQueries({
    queries: missing.map((id) => ({
      queryKey: queryKeys.workUnit(id),
      queryFn: () =>
        unwrap(api.GET('/api/work-units/{work_unit_id}', { params: { path: { work_unit_id: id } } })),
    })),
  })
  const byId = new Map<string, WorkUnitRow>()
  for (const day of [
    ...(proposed.data?.items ?? []),
    ...(flagged.data?.items ?? []),
    ...extra.flatMap((query) => (query.data && query.data.contract_id === contractId ? [query.data] : [])),
  ]) {
    byId.set(day.id, day)
  }
  const days = [...byId.values()].sort((a, b) => b.data.localeCompare(a.data))
  return {
    days,
    isPending: proposed.isPending || flagged.isPending,
    isError: proposed.isError || flagged.isError,
    error: proposed.error ?? flagged.error,
  }
}

/**
 * What a write to a day leaves stale: every list of days, the day itself, and the
 * calendar (a day is on its date there, with its state), by prefix, because a move is
 * stale in whichever month the page is not showing.
 */
function useInvalidateDays() {
  const queryClient = useQueryClient()
  return () => {
    void queryClient.invalidateQueries({ queryKey: ['work-units'] })
    void queryClient.invalidateQueries({ queryKey: ['work-unit'] })
    void queryClient.invalidateQueries({ queryKey: ['calendario'] })
  }
}

export function useRecordDay() {
  const invalidate = useInvalidateDays()
  return useMutation({
    mutationFn: (body: WorkUnitCreateBody) => unwrap(api.POST('/api/work-units', { body })),
    onSuccess: invalidate,
  })
}

export function useMoveDay(workUnitId: string) {
  const queryClient = useQueryClient()
  const invalidate = useInvalidateDays()
  return useMutation({
    mutationFn: (body: { stato: WorkUnitStato; motivo: string }) =>
      unwrap(
        api.POST('/api/work-units/{work_unit_id}/transitions', {
          params: { path: { work_unit_id: workUnitId } },
          body,
        }),
      ),
    onSuccess: (detail) => {
      queryClient.setQueryData(queryKeys.workUnit(workUnitId), detail)
      invalidate()
    },
    // A 409 means the day moved under this page (another tab, a trigger): read it again.
    onError: () => void queryClient.invalidateQueries({ queryKey: queryKeys.workUnit(workUnitId) }),
  })
}
