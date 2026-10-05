import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useLegalEntityScope } from '@/lib/legalEntity'
import { api, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { queryKeys } from '@/lib/query'
import { downloadDocument } from '@/features/documents/queries'

/**
 * Wire shapes taken from the generated OpenAPI schema, never hand-declared: the single
 * source of truth is `timetracking/schemas.py` and `pnpm generate:api` tracks it.
 *
 * Note that `ore`, `tariffa_applicata`, `valore_riga` and every other `Decimal` come
 * through as `string`, never `number` -- which is what lets `lib/decimal.ts` read them
 * digit by digit instead of through a binary float.
 */
export type TimeEntry = components['schemas']['TimeEntryRead']
export type DealTimeSummary = components['schemas']['DealTimeSummary']
export type RateDescription = components['schemas']['RateDescription']
export type RunningTimer = components['schemas']['TimerRead']
type TimerStartBody = components['schemas']['TimerStart']
type TimerUpdateBody = components['schemas']['TimerUpdate']
type TimerStopBody = components['schemas']['TimerStop']

type TimeEntryCreateBody = components['schemas']['TimeEntryCreate']
type TimeEntryUpdateBody = components['schemas']['TimeEntryUpdate']

export interface TimeEntriesListParams {
  deal_id?: string
  azienda_id?: string
  user_id?: string
  da?: string
  a?: string
  fatturabile?: boolean
  fatturato?: boolean
}

/**
 * `enabled` is offered because one caller -- the week grid -- has a filter it cannot
 * safely leave out: `user_id`. An admin's session answers an unfiltered list with the
 * whole team's hours, so a first render made before `useAuth` has resolved would fill a
 * personal grid with somebody else's rows and only then correct itself. Defaulting to
 * `true` keeps every existing caller unchanged.
 */
export function useTimeEntries(
  params: TimeEntriesListParams = {},
  options: { enabled?: boolean } = {},
) {
  // The sidebar's azienda (REB-625), through the deal each entry hangs on; a deal's own
  // tab passes `deal_id` and is left alone.
  const scoped = useLegalEntityScope(params)
  return useQuery({
    queryKey: queryKeys.timeEntries(scoped),
    enabled: options.enabled ?? true,
    queryFn: () =>
      unwrap(api.GET('/api/time-entries', { params: { query: { ...scoped, limit: 200 } } })),
  })
}

export function useDealTimeSummary(dealId: string) {
  return useQuery({
    queryKey: queryKeys.dealTimeSummary(dealId),
    queryFn: () =>
      unwrap(
        api.GET('/api/deals/{deal_id}/time-summary', {
          params: { path: { deal_id: dealId } },
        }),
      ),
  })
}

/**
 * `enabled` on a real `userId`, not a `?? ''` fallback: an empty path segment does not
 * match the route, Starlette's trailing-slash redirect lands the request on the *list*
 * endpoint with an absolute URL that escapes the Vite dev proxy, and it still resolves
 * 200 with nothing to show -- the exact live defect `features/people/$personId.tsx`
 * found first.
 */
export function useDealRates(dealId: string, userId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.dealRates(dealId, userId ?? ''),
    enabled: Boolean(userId),
    queryFn: () =>
      unwrap(
        api.GET('/api/deals/{deal_id}/rates', {
          params: { path: { deal_id: dealId }, query: { user_id: userId as string } },
        }),
      ),
  })
}

/** Invalidates the summary and the deal's own timeline alongside the list: an hour
 *  changes all three, and a stale summary next to a fresh list is the shape of bug
 *  that makes people stop trusting the screen. */
function invalidateAfterWrite(queryClient: ReturnType<typeof useQueryClient>, dealId?: string) {
  void queryClient.invalidateQueries({ queryKey: queryKeys.timeEntries() })
  if (dealId) {
    void queryClient.invalidateQueries({ queryKey: queryKeys.dealTimeSummary(dealId) })
    void queryClient.invalidateQueries({ queryKey: queryKeys.timeline('deal', dealId) })
  }
}

export function useLogTime() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      unwrap(api.POST('/api/time-entries', { body: body as unknown as TimeEntryCreateBody })),
    onSuccess: (entry) => invalidateAfterWrite(queryClient, entry.deal_id),
  })
}

export function useUpdateTimeEntry(entryId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      unwrap(
        api.PATCH('/api/time-entries/{entry_id}', {
          params: { path: { entry_id: entryId } },
          body: body as unknown as TimeEntryUpdateBody,
        }),
      ),
    onSuccess: (entry) => invalidateAfterWrite(queryClient, entry.deal_id),
  })
}

/**
 * The same PATCH as `useUpdateTimeEntry`, with the entry id carried in the *variables*
 * rather than taken at construction.
 *
 * That difference is the whole reason it exists: the week grid has one editable control
 * per deal per day, and `useUpdateTimeEntry(id)` inside that loop would be a hook call
 * per cell -- a variable number of them, in a variable order, which is precisely what
 * the rules of hooks forbid. One instance per row serves every cell in it. The
 * construction-time form stays for the single-entry dialog, where there is exactly one
 * id and binding it once reads better.
 */
export function useUpdateHours() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ entryId, ore }: { entryId: string; ore: string }) =>
      unwrap(
        api.PATCH('/api/time-entries/{entry_id}', {
          params: { path: { entry_id: entryId } },
          body: { ore } as unknown as TimeEntryUpdateBody,
        }),
      ),
    onSuccess: (entry) => invalidateAfterWrite(queryClient, entry.deal_id),
  })
}

export function useDeleteTimeEntry() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ entryId }: { entryId: string; dealId?: string }) =>
      unwrap(
        api.DELETE('/api/time-entries/{entry_id}', { params: { path: { entry_id: entryId } } }),
      ),
    // The 204 carries no body, so the deal to refresh has to come from the caller's own
    // variables rather than from a response that says nothing.
    onSuccess: (_data, variables) => invalidateAfterWrite(queryClient, variables.dealId),
  })
}

/**
 * The XLSX only. A plain URL, not a fetch: the browser downloads the file itself, so
 * the bytes never pass through JavaScript. Same-origin, so the session cookie travels
 * with it and no token has to be put in a query string.
 *
 * The PDF deliberately has no URL of its own here -- see `useRenderTimeReportPdf`.
 */
export function timeReportXlsxUrl(dealId: string, mese: string): string {
  const query = new URLSearchParams({ mese, formato: 'xlsx' })
  return `/api/deals/${dealId}/time-report?${query.toString()}`
}

/**
 * The PDF half, which is not a download link and cannot be one.
 *
 * The two formats answer differently on purpose. The XLSX is a working copy: the
 * endpoint streams the bytes with a `Content-Disposition`, so an anchor is exactly
 * right. The PDF is an *artefact* -- `render_pdf` archives it as a `document`, and the
 * endpoint answers `201` with that document's JSON, because slice 2's rule is that
 * archived bytes are fetched from the document download endpoint and nowhere else.
 *
 * Pointing an anchor at it therefore navigated the browser to a page of JSON. Two
 * steps, not one: render (and archive), then download the archived document through
 * the one path that carries authorisation on either storage backend.
 */
export function useRenderTimeReportPdf() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (variables: { dealId: string; mese: string }) => {
      const document = await unwrap(
        api.GET('/api/deals/{deal_id}/time-report', {
          params: {
            path: { deal_id: variables.dealId },
            query: { mese: variables.mese, formato: 'pdf' },
          },
        }),
      )
      await downloadDocument((document as { id: string }).id)
      return document
    },
    // The render archived a new document version, so the deal's Documenti tab is stale
    // the moment this resolves.
    onSuccess: (_data, variables) => {
      // The owner-scoped key first, then the empty-object wildcard that partially
      // matches every cached owner -- the same belt-and-braces `useDeleteDocument`
      // uses, and for the same reason: this render archived a new version, so the
      // deal's Documenti tab is stale the moment it resolves.
      void queryClient.invalidateQueries({
        queryKey: queryKeys.documents({ dealId: variables.dealId }),
      })
      void queryClient.invalidateQueries({ queryKey: queryKeys.documents() })
    },
  })
}

/**
 * The running timer, the one thing on this screen that is *live*: `null` when nothing
 * runs, which the API answers with a 200 rather than a 404 because that is the ordinary
 * state of the page. Refetched every minute and on focus, so a clock started on the
 * phone shows up on the laptop without a reload; the second-by-second tick is the
 * component's own, computed from `started_at`, never a request.
 */
export function useRunningTimer(options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: queryKeys.timer,
    enabled: options.enabled ?? true,
    queryFn: async (): Promise<RunningTimer | null> =>
      (await unwrap(api.GET('/api/time-entries/timer'))) ?? null,
    refetchInterval: 60_000,
  })
}

export function useStartTimer() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: TimerStartBody) => unwrap(api.POST('/api/time-entries/timer/start', { body })),
    onSuccess: (timer) => queryClient.setQueryData(queryKeys.timer, timer),
  })
}

export function useUpdateTimer() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: TimerUpdateBody) => unwrap(api.PATCH('/api/time-entries/timer', { body })),
    onSuccess: (timer) => queryClient.setQueryData(queryKeys.timer, timer),
  })
}

/** Stopping answers with the entry the timer became, so the register and the deal's
 *  summary are refreshed exactly as after any other write, and the timer key is set to
 *  `null` rather than refetched -- the server has already said there is none. */
export function useStopTimer() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: TimerStopBody) => unwrap(api.POST('/api/time-entries/timer/stop', { body })),
    onSuccess: (entry) => {
      queryClient.setQueryData(queryKeys.timer, null)
      invalidateAfterWrite(queryClient, entry.deal_id)
    },
  })
}

export function useDiscardTimer() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => unwrap(api.DELETE('/api/time-entries/timer')),
    onSuccess: () => queryClient.setQueryData(queryKeys.timer, null),
  })
}
