import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from '@rebase/ui/sonner'
import { api, toProblem, unwrap } from '@/lib/api'
import type { StatusTone } from '@/components/StatusPill'
import type { components } from '@/lib/api-types'
import { queryKeys } from '@/lib/query'

/**
 * Wire shape of one deal and one pipeline stage, taken directly from the generated
 * OpenAPI schema rather than hand-declared -- the same reasoning as `Customer`/
 * `Person`'s own aliases (features/customers/queries.ts, features/people/
 * queries.ts): the single source of truth is `DealRead`/`PipelineStageRead` in
 * packages/core/src/pigrocrm/core/{deals,pipeline}/schemas.py, and these track it
 * through `pnpm generate:api` automatically.
 *
 * Note `valore_previsto`/`ore_preventivate`/`valore_preventivato` come through as
 * `string | null`, never `number`: `Decimal` fields serialise to JSON strings
 * (confirmed against the generated type), which is exactly what lets `columns.tsx`
 * parse them digit-by-digit instead of through a binary float -- see that file's
 * `centsFromDecimalString` for why that distinction matters the moment more than
 * one of these is added together.
 */
export type Deal = components['schemas']['DealRead']
export type Stage = components['schemas']['PipelineStageRead']

/**
 * The tone a pipeline stage reads as in a `StatusPill` (design spec §4), keyed on the
 * stage's `tipo` rather than on its name: a tenant renames «Vinto» to whatever it likes
 * and adds as many open stages as it wants, but `tipo` is the closed enum
 * (`open`/`won`/`lost`) the backend guarantees -- so this map stays total and no
 * tenant-defined stage can fall through to a tone nobody chose.
 *
 * Every open stage is `muted`: a deal in the middle of a pipeline is not news, and a
 * board of eight coloured pills would say nothing. Only the two ends are stated.
 */
export const DEAL_STAGE_TONE: Record<Stage['tipo'], StatusTone> = {
  open: 'muted',
  won: 'ink',
  lost: 'danger',
}

type DealCreateBody = components['schemas']['DealCreate']
type DealUpdateBody = components['schemas']['DealUpdate']

/**
 * `fatturato_non_vinto` and `da_fatturare` are the two operational-dashboard
 * drill-throughs (slice 6 §6.2). They are here rather than expressed as a client-side
 * filter over the fetched list because criterion 2 requires the card and the list behind
 * it to be the *same predicate*: `DealRepository.list` and the two counts on the
 * dashboard share one predicate function in `packages/core`, and a second, approximate
 * version of it written in the browser is exactly the divergence that requirement exists
 * to forbid.
 */
export interface DealsListParams {
  search?: string
  customer_id?: string
  stage_id?: string
  fatturato_non_vinto?: boolean
  da_fatturare?: boolean
}

/**
 * The aggregated result `useDeals` resolves to -- deliberately not the wire's own
 * `DealPage` (`{items, next_cursor}`): that shape is one *page*, and handing it
 * straight to the Kanban board is exactly the bug this type exists to rule out.
 * See `fetchAllDeals` below for the full reasoning.
 */
export interface DealsResult {
  items: Deal[]
  /** True only if `MAX_PAGES` was exhausted while the server still reported more
   *  (`next_cursor` still non-null) -- see `fetchAllDeals`'s own docstring. Never
   *  true for any tenant this product is actually sized for; it exists so a
   *  pathological one is told, not silently shown a partial board. */
  truncated: boolean
}

// `DealListQuery.limit` (deals/schemas.py) is bounded `ge=1, le=200`; 200 is
// therefore the fewest possible requests to drain a real result set.
const MAX_PAGE_SIZE = 200
// A hard stop against a runaway loop, not a bound this product's own target user
// (a solo/small-team Italian freelancer) is expected to ever approach: 100 pages
// of 200 is 20,000 deals. If a tenant ever does cross it, `truncated: true` says
// so explicitly instead of either looping indefinitely or silently stopping.
const MAX_PAGES = 100

/**
 * `GET /api/deals` is cursor-paginated and bounded (`limit` defaults to 50, capped
 * at 200 -- deals/schemas.py's `DealListQuery`, deals/repository.py's keyset
 * pagination on `Deal.id` ascending). A `useDeals` that only ever fetched the
 * first page would silently render a subset of a stage's deals the moment a
 * tenant passes 50 open deals: the Kanban's own per-column count and total
 * (`columns.tsx`'s `sumValorePrevisto`) would then be wrong with nothing on
 * screen saying so, which is worse than a visible cap.
 *
 * This walks the cursor at the maximum page size until the server reports no
 * `next_cursor`, aggregating every page into one flat list -- "every deal
 * matching the filter" is the actual contract the Kanban and the list view both
 * need, not "the first 50". `MAX_PAGES` is the one safety valve: see its own
 * comment for why crossing it is surfaced rather than silent.
 */
export async function fetchAllDeals(params: DealsListParams): Promise<DealsResult> {
  const items: Deal[] = []
  let cursor: string | undefined
  for (let page = 0; page < MAX_PAGES; page += 1) {
    const data = await unwrap(
      api.GET('/api/deals', {
        params: { query: { ...params, limit: MAX_PAGE_SIZE, cursor } },
      }),
    )
    items.push(...data.items)
    if (!data.next_cursor) return { items, truncated: false }
    cursor = data.next_cursor
  }
  return { items, truncated: true }
}

export function useDeals(params: DealsListParams = {}) {
  return useQuery({
    queryKey: queryKeys.deals(params),
    queryFn: () => fetchAllDeals(params),
  })
}

export function useStages() {
  return useQuery({
    queryKey: queryKeys.stages,
    queryFn: () => unwrap(api.GET('/api/pipeline-stages')),
  })
}

export function useDeal(dealId: string) {
  return useQuery({
    queryKey: queryKeys.deal(dealId),
    queryFn: () =>
      unwrap(api.GET('/api/deals/{deal_id}', { params: { path: { deal_id: dealId } } })),
  })
}

/**
 * `body` arrives as the loosely-typed `Record<string, unknown>` that
 * `DealForm.submit` builds -- the same idiom `customers/queries.ts`/`people/
 * queries.ts` use for the identical reason: a dynamic mix of native columns and
 * tenant-defined custom fields no fixed interface can describe. `as unknown as
 * DealCreateBody` says so honestly rather than a bare `as never` that hides the
 * target type entirely.
 */
export function useCreateDeal() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      unwrap(api.POST('/api/deals', { body: body as unknown as DealCreateBody })),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.deals() }),
  })
}

export function useUpdateDeal(dealId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      unwrap(
        api.PATCH('/api/deals/{deal_id}', {
          params: { path: { deal_id: dealId } },
          body: body as unknown as DealUpdateBody,
        }),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.deal(dealId) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.deals() })
      void queryClient.invalidateQueries({ queryKey: queryKeys.timeline('deal', dealId) })
    },
  })
}

/**
 * Optimistic: dragging a card and then watching a spinner is exactly the kind of
 * friction that makes a CRM unpleasant, so the move is written into every cached
 * deals list immediately, before the server has agreed to it.
 *
 * On failure the rollback has to actually restore the *previous* cache contents,
 * not merely re-render -- `onError` below puts every snapshotted query back
 * exactly as it was, which is what makes the dropped card visually snap back to
 * its original column once React re-renders from the restored data (`KanbanBoard`
 * re-derives each column by filtering on `pipeline_stage_id`, so restoring that
 * one field is enough). Verified live against the running API by soft-deleting a
 * deal in a second tab and then dragging its still-cached card in the first: the
 * PATCH 404s (`DealRepository.get` excludes a soft-deleted row, so `move_stage`
 * raises `NotFound`), the card returns to its original column, and a toast reads
 * "deal <id> not found".
 *
 * `onError` toasts the server's message itself, on the mutation, rather than
 * leaving it to a per-call `.mutate(vars, {onError})` at the call site (the
 * brief's own pattern, and this hook's first shipped version). `useMoveDeal` is
 * one `useMutation()` instance shared by the whole board, and dragging a second
 * card before the first PATCH settles calls `mutate()` again on that same
 * instance -- and `@tanstack/query-core`'s `MutationObserver` re-points itself at
 * the new call, so a per-call `onError` passed to the *first* call is silently
 * overwritten and never runs once that first mutation actually settles. The
 * rollback above is unaffected (it reads the snapshot straight from the
 * mutation's own context, not the observer), but a toast that lived at the call
 * site would go missing precisely when it matters most: two failed-then-
 * succeeded (or vice versa) drags in quick succession, silently. Defining
 * `onError` here, once, on the mutation's own options, survives being overtaken
 * by a later call -- confirmed with two overlapping `mutate()` calls in
 * `queries.test.tsx`, not just one (a single call cannot tell the two designs
 * apart). Every other mutation on the Kanban screen (`useCreateDeal`, used by
 * "Nuovo deal") is guarded against this by its own dialog's Salva button being
 * disabled while `isPending`, so a second call cannot be fired before the first
 * settles in the first place -- dragging has no equivalent "disable while a move
 * is pending" gate, which is what makes this hook the one exposed to the trap in
 * practice.
 *
 * `getQueriesData`/`setQueryData` are typed as `DealsResult` for this hook's own
 * purposes, but the same `['deals', ...]` key prefix also matches `features/
 * customers/queries.ts`'s `useCustomerDeals` (a plain wire `DealPage`). Both
 * shapes carry `.items: Deal[]`, which is all this hook ever reads or rewrites,
 * so touching that cache entry too is harmless -- and `onSettled`'s invalidation
 * below refreshes it correctly regardless.
 */
export function useMoveDeal() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ dealId, stageId }: { dealId: string; stageId: string }) =>
      unwrap(
        api.PATCH('/api/deals/{deal_id}/stage', {
          params: { path: { deal_id: dealId } },
          body: { stage_id: stageId },
        }),
      ),
    onMutate: async ({ dealId, stageId }) => {
      await queryClient.cancelQueries({ queryKey: queryKeys.deals() })
      const snapshot = queryClient.getQueriesData<DealsResult>({ queryKey: queryKeys.deals() })
      for (const [key, data] of snapshot) {
        if (!data) continue
        queryClient.setQueryData<DealsResult>(key, {
          ...data,
          items: data.items.map((deal) =>
            deal.id === dealId ? { ...deal, pipeline_stage_id: stageId } : deal,
          ),
        })
      }
      return { snapshot }
    },
    onError: (error, _variables, context) => {
      for (const [key, data] of context?.snapshot ?? []) {
        queryClient.setQueryData(key, data)
      }
      toast.error(toProblem(error).detail)
    },
    onSettled: (_data, _error, variables) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.deals() })
      void queryClient.invalidateQueries({ queryKey: queryKeys.deal(variables.dealId) })
      // A stage can be deleted server-side (admin-only, `PipelineService.delete`)
      // between one load of the board and the next. `useStages`'s own cache has
      // no other reason to refresh mid-session, so without this a deleted stage
      // keeps rendering as a phantom column (with whatever deals it still had)
      // until the next full reload or `staleTime` (30s) lapses on its own --
      // observed live. A move is the one event on this screen already touching
      // the pipeline, so it is the natural point to also refresh what the
      // pipeline itself looks like.
      void queryClient.invalidateQueries({ queryKey: queryKeys.stages })
      // A move changes which stage a deal is in, which is the whole of the commercial
      // dashboard's pipeline snapshot, and it may change the closures too. By the
      // ['dashboard'] prefix: this mutation cannot know which period is on screen.
      void queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    },
  })
}

/**
 * `DELETE /api/deals/{id}` is a soft delete: the server sets `deleted_at`. Unlike
 * `CustomerService.soft_delete`, `DealService.soft_delete` raises no conflict --
 * nothing else in this slice holds a foreign key to a deal -- so there is no
 * active-dependents count to surface here.
 */
export function useDeleteDeal() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (dealId: string) =>
      unwrap(api.DELETE('/api/deals/{deal_id}', { params: { path: { deal_id: dealId } } })),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.deals() }),
  })
}
