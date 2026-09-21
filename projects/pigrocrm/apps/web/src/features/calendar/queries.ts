import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { queryKeys } from '@/lib/query'

/**
 * Wire shapes from the generated OpenAPI schema, never hand-declared: the source of
 * truth is `calendario/schemas.py` and `attivita/schemas.py`, and `pnpm generate:api`
 * tracks them. Every `Decimal` -- `ore`, `totale` -- arrives as a `string`, which is
 * what lets `lib/decimal.ts` read it digit by digit instead of through a float.
 */
export type CalendarMonth = components['schemas']['CalendarMonth']
export type CalendarDay = components['schemas']['CalendarDay']
export type Attivita = components['schemas']['AttivitaRead']
export type DueInvoice = components['schemas']['DueInvoice']
type AttivitaCreateBody = components['schemas']['AttivitaCreate']
type AttivitaUpdateBody = components['schemas']['AttivitaUpdate']

export function useCalendarMonth(mese: string) {
  return useQuery({
    queryKey: queryKeys.calendarMonth(mese),
    queryFn: () => unwrap(api.GET('/api/calendar', { params: { query: { mese } } })),
  })
}

/**
 * Everything a write to a commitment invalidates: the month it is in, every other month
 * (moving a date takes it out of one and puts it into another), and the flat list.
 *
 * The wildcard `['calendario']` prefix rather than the one month the caller happens to
 * be looking at: a commitment whose date moved is stale in two months at once, and a
 * grid still drawing it in the old cell is the shape of bug that makes people stop
 * trusting the screen.
 */
function invalidateAfterWrite(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: ['calendario'] })
  void queryClient.invalidateQueries({ queryKey: ['attivita'] })
}

export function useCreateAttivita() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: AttivitaCreateBody) => unwrap(api.POST('/api/activities', { body })),
    onSuccess: () => invalidateAfterWrite(queryClient),
  })
}

export function useUpdateAttivita() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, changes }: { id: string; changes: AttivitaUpdateBody }) =>
      unwrap(
        api.PATCH('/api/activities/{attivita_id}', {
          params: { path: { attivita_id: id } },
          body: changes,
        }),
      ),
    onSuccess: () => invalidateAfterWrite(queryClient),
  })
}

/**
 * The three closures, as three mutations over three routes.
 *
 * Not one `useSetState(stato)`: «done» writes the day it was done, and «no longer
 * needed» writes neither a date nor a deletion. The state machine is the service's, and
 * a single hook taking a target state would invite the browser to think it owns it.
 */
function useClosure(path: 'complete' | 'cancel' | 'reopen') {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) =>
      unwrap(
        api.POST(`/api/activities/{attivita_id}/${path}` as '/api/activities/{attivita_id}/complete', {
          params: { path: { attivita_id: id } },
        }),
      ),
    onSuccess: () => invalidateAfterWrite(queryClient),
  })
}

export function useCompleteAttivita() {
  return useClosure('complete')
}

export function useCancelAttivita() {
  return useClosure('cancel')
}

export function useReopenAttivita() {
  return useClosure('reopen')
}
