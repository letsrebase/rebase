import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useLegalEntity } from '@/lib/legalEntity'
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
export type AttivitaUpdateBody = components['schemas']['AttivitaUpdate']

export function useCalendarMonth(mese: string) {
  // The sidebar's azienda (REB-625): the hours through their deal and the invoices
  // falling due by their own column; the commitments have no azienda and stay.
  const { selected } = useLegalEntity()
  return useQuery({
    queryKey: queryKeys.calendarMonth(mese, selected),
    queryFn: () =>
      unwrap(
        api.GET('/api/calendar', {
          params: { query: { mese, azienda_id: selected ?? undefined } },
        }),
      ),
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
  // The palette caches a term for ten seconds and now finds attività: a task archived or
  // renamed a moment ago must not be offered under its old state.
  void queryClient.invalidateQueries({ queryKey: ['search'] })
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
 * Archiving is `DELETE`, a soft one: the row stays, restorable, and leaves every list.
 * It is for the typo; the change of plan is `cancel`.
 */
export function useArchiveAttivita() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) =>
      unwrap(api.DELETE('/api/activities/{attivita_id}', { params: { path: { attivita_id: id } } })),
    onSuccess: () => invalidateAfterWrite(queryClient),
  })
}

export function useRestoreAttivita() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) =>
      unwrap(
        api.POST('/api/activities/{attivita_id}/restore', {
          params: { path: { attivita_id: id } },
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
