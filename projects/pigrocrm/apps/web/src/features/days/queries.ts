import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/lib/api'
import { useLegalEntity } from '@/lib/legalEntity'
import { queryKeys } from '@/lib/query'
import type { WorkUnitCreateBody, WorkUnitStato } from './labels'

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
 * sidebar's azienda. A contract in `bozza` is offered too, because that is the state every
 * contract is born in and the ledger does not wait for it to be activated.
 */
export function useDayContracts() {
  const { selected } = useLegalEntity()
  return useQuery({
    queryKey: [...queryKeys.dayContracts, selected],
    queryFn: async () => {
      const page = await unwrap(
        api.GET('/api/contracts', {
          params: { query: { azienda_id: selected ?? undefined, limit: 200, sort: 'titolo' } },
        }),
      )
      return page.items.filter((contract) => contract.stato !== 'terminato' && contract.stato !== 'scaduto')
    },
  })
}

export function useDayRateCards(contractId: string) {
  return useQuery({
    enabled: contractId !== '',
    queryKey: queryKeys.dayRateCards(contractId),
    queryFn: () =>
      unwrap(
        api.GET('/api/contracts/{contract_id}/rate-cards', {
          params: { path: { contract_id: contractId } },
        }),
      ),
  })
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
