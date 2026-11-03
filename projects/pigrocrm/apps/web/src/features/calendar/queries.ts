import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useLegalEntity } from '@/lib/legalEntity'
import { api, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { queryKeys } from '@/lib/query'

/**
 * Wire shapes from the generated OpenAPI schema, never hand-declared: the source of
 * truth is `calendar/schemas.py` and `tasks/schemas.py`, and `pnpm generate:api`
 * tracks them. Every `Decimal` -- `hours`, `total` -- arrives as a `string`, which is
 * what lets `lib/decimal.ts` read it digit by digit instead of through a float.
 */
export type CalendarMonth = components['schemas']['CalendarMonth']
export type CalendarDay = components['schemas']['CalendarDay']
export type Task = components['schemas']['TaskRead']
export type DueInvoice = components['schemas']['DueInvoice']
type TaskCreateBody = components['schemas']['TaskCreate']
export type TaskUpdateBody = components['schemas']['TaskUpdate']

export function useCalendarMonth(month: string) {
  // The sidebar's azienda (REB-625): the hours through their deal and the invoices
  // falling due by their own column; the commitments have no azienda and stay.
  const { selected } = useLegalEntity()
  return useQuery({
    queryKey: queryKeys.calendarMonth(month, selected),
    queryFn: () =>
      unwrap(
        api.GET('/api/calendar', {
          params: { query: { month, legal_entity_id: selected ?? undefined } },
        }),
      ),
  })
}

/**
 * Everything a write to a commitment invalidates: the month it is in, every other month
 * (moving a date takes it out of one and puts it into another), and the flat list.
 *
 * The wildcard `queryKeys.calendarAll` prefix rather than the one month the caller happens to
 * be looking at: a commitment whose date moved is stale in two months at once, and a
 * grid still drawing it in the old cell is the shape of bug that makes people stop
 * trusting the screen.
 */
function invalidateAfterWrite(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: queryKeys.calendarAll })
  void queryClient.invalidateQueries({ queryKey: queryKeys.activitiesAll })
  // The palette caches a term for ten seconds and now finds attività: a task archived or
  // renamed a moment ago must not be offered under its old state.
  void queryClient.invalidateQueries({ queryKey: ['search'] })
}

export function useCreateTask() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: TaskCreateBody) => unwrap(api.POST('/api/activities', { body })),
    onSuccess: () => invalidateAfterWrite(queryClient),
  })
}

export function useUpdateTask() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, changes }: { id: string; changes: TaskUpdateBody }) =>
      unwrap(
        api.PATCH('/api/activities/{task_id}', {
          params: { path: { task_id: id } },
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
export function useArchiveTask() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) =>
      unwrap(api.DELETE('/api/activities/{task_id}', { params: { path: { task_id: id } } })),
    onSuccess: () => invalidateAfterWrite(queryClient),
  })
}

export function useRestoreTask() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) =>
      unwrap(
        api.POST('/api/activities/{task_id}/restore', {
          params: { path: { task_id: id } },
        }),
      ),
    onSuccess: () => invalidateAfterWrite(queryClient),
  })
}

/**
 * The three closures, as three mutations over three routes.
 *
 * Not one `useSetState(status)`: «done» writes the day it was done, and «no longer
 * needed» writes neither a date nor a deletion. The state machine is the service's, and
 * a single hook taking a target state would invite the browser to think it owns it.
 */
function useClosure(path: 'complete' | 'cancel' | 'reopen') {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) =>
      unwrap(
        api.POST(`/api/activities/{task_id}/${path}` as '/api/activities/{task_id}/complete', {
          params: { path: { task_id: id } },
        }),
      ),
    onSuccess: () => invalidateAfterWrite(queryClient),
  })
}

export function useCompleteTask() {
  return useClosure('complete')
}

export function useCancelTask() {
  return useClosure('cancel')
}

export function useReopenTask() {
  return useClosure('reopen')
}
