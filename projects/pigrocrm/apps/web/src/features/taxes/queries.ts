import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from '@rebase/ui/sonner'
import { useLegalEntity } from '@/lib/legalEntity'
import { api, toProblem, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { useCan } from '@/lib/auth'
import { queryKeys, retryUnlessMissing } from '@/lib/query'
import { toIsoDate } from '@/lib/dates'
import { nextDays, type DueDay } from './dates'

/** How many dates the Home carries (brief: at most two rows). */
export const HOME_DATES = 2

/**
 * `GET /api/analytics/fiscal/calendar`: the dates of calendar `year` for one legal entity, from
 * its pack. One legal entity's, like the estimate: the one `legalEntityId` names, or the
 * sidebar's when the caller names none; asked without a legal entity only once the list of legal
 * entities has answered, since on a space with two the server refuses a read that names none.
 * `enabled` is the caller's role and module gate: a role that may not read it makes no request.
 */
export function useTaxCalendar(year: number, legalEntityId?: string | null, enabled = true) {
  const { selected, loaded } = useLegalEntity()
  const legal_entity = legalEntityId === undefined ? selected : legalEntityId
  const query = legal_entity === null ? { year } : { year, legal_entity_id: legal_entity }
  return useQuery({
    queryKey: queryKeys.taxCalendar(year, legal_entity),
    queryFn: () => unwrap(api.GET('/api/analytics/fiscal/calendar', { params: { query } })),
    enabled: enabled && (legal_entity !== null || loaded),
    retry: retryUnlessMissing,
  })
}

/**
 * The next dates of one legal entity for the Home: the first `HOME_DATES` days on or after today,
 * read from this calendar year and, when fewer are left, from the next (the December advance is
 * followed by January's payments). `hasCalendar` says whether the pack carries dates at all, so
 * a pack with none is told from a quiet stretch; the Home draws nothing for the first.
 */
export function useNextTaxDates(legalEntityId?: string | null, enabled = true) {
  const allowed = useCan('get_tax_calendar') && enabled
  const now = new Date()
  const today = toIsoDate(now)
  const year = now.getFullYear()
  const current = useTaxCalendar(year, legalEntityId, allowed)
  const ahead = current.data ? nextDays(current.data.dates, today, HOME_DATES) : []
  const needMore = current.isSuccess && ahead.length < HOME_DATES
  const following = useTaxCalendar(year + 1, legalEntityId, allowed && needMore)
  const days: DueDay[] = needMore
    ? [...ahead, ...nextDays(following.data?.dates ?? [], today, HOME_DATES - ahead.length)]
    : ahead
  const waiting = current.isPending || (needMore && following.isPending)
  return {
    today,
    days,
    waiting,
    error: current.error ?? (needMore ? following.error : null),
    hasCalendar: (current.data?.dates.length ?? 0) + (following.data?.dates.length ?? 0) > 0,
    refetch: () => {
      void current.refetch()
      if (needMore) void following.refetch()
    },
  }
}

export type TaxDueItem = components['schemas']['DueItemRead']
export type TaxPlans = components['schemas']['TaxPlansRead']
export type TaxInstalmentPlan = components['schemas']['TaxInstalmentPlanRead']
export type TaxNoticePlan = components['schemas']['TaxNoticePlanRead']
export type NoticePlanSet = components['schemas']['TaxNoticePlanSet']
export type NoticePlanUpdate = components['schemas']['TaxNoticePlanUpdate']
export type AdvanceMethod = components['schemas']['TaxAdvanceMethodSet']['method']

/** The source kinds of the tax due items (`due_items/models.py`): what «Tasse» lists. */
export const TAX_ITEM_KINDS = [
  'tax_balance',
  'tax_advance',
  'tax_instalment',
  'notice_plan',
  'chamber_fee',
] as const

/** The kinds that are an instalment of a plan: the «Rate» tab reads these for any year. */
const PLAN_ITEM_KINDS = ['tax_instalment', 'notice_plan'] as const

/** One request is 200 items at most, the page's biggest; a legal entity's year holds a few dozen. */
const ITEMS_LIMIT = 200

/** What `useTaxDueItems` reads: the open items (`forecast` and `expected` come together when
 *  `state` is left out) and the settled ones, tax items only. */
async function readTaxItems(
  legalEntityId: string,
  extra: { source_kind?: string; due_from?: string; due_to?: string },
): Promise<TaxDueItem[]> {
  const base = { legal_entity_id: legalEntityId, direction: 'out' as const, limit: ITEMS_LIMIT, ...extra }
  const [open, settled] = await Promise.all([
    unwrap(api.GET('/api/due-items', { params: { query: base } })),
    unwrap(api.GET('/api/due-items', { params: { query: { ...base, state: 'settled' as const } } })),
  ])
  return [...open.items, ...settled.items].filter((item) => item.tax !== null)
}

/**
 * The tax due items of one legal entity whose date falls in calendar `year`: the balances,
 * advances, instalments, notice instalments and the chamber fee, open and paid (REB-1079). Only
 * an administrator reads them, so `enabled` carries the role gate, as the estimate's does.
 */
export function useTaxDueItems(year: number, legalEntityId: string | null, enabled = true) {
  return useQuery({
    queryKey: queryKeys.taxDueItems(String(year), legalEntityId),
    queryFn: () =>
      readTaxItems(legalEntityId ?? '', { due_from: `${year}-01-01`, due_to: `${year}-12-31` }),
    enabled: enabled && legalEntityId !== null,
    retry: retryUnlessMissing,
  })
}

/** The instalments of the plans, any year: the «Rate» tab's next instalment and its state. */
export function useTaxPlanItems(legalEntityId: string | null, enabled = true) {
  return useQuery({
    queryKey: queryKeys.taxDueItems('plans', legalEntityId),
    queryFn: async () => {
      const lists = await Promise.all(
        PLAN_ITEM_KINDS.map((kind) => readTaxItems(legalEntityId ?? '', { source_kind: kind })),
      )
      return lists.flat()
    },
    enabled: enabled && legalEntityId !== null,
    retry: retryUnlessMissing,
  })
}

/** `GET /api/analytics/fiscal/plans`: the instalment plans and the notice plans of one legal
 *  entity. */
export function useTaxPlans(legalEntityId: string | null, enabled = true) {
  return useQuery({
    queryKey: queryKeys.taxPlans(legalEntityId),
    queryFn: () =>
      unwrap(
        api.GET('/api/analytics/fiscal/plans', {
          params: { query: { legal_entity_id: legalEntityId ?? '' } },
        }),
      ),
    enabled: enabled && legalEntityId !== null,
    retry: retryUnlessMissing,
  })
}

/** Every read the plans, the advances and the items move: the estimate and its readings, the
 *  items, the plans, the calendar and the cash schedule that lists the same items. */
function useRefreshTaxes() {
  const client = useQueryClient()
  return () => {
    void client.invalidateQueries({ queryKey: ['fiscal-estimate'] })
    void client.invalidateQueries({ queryKey: queryKeys.taxDueItemsAll })
    void client.invalidateQueries({ queryKey: ['tax-plans'] })
    void client.invalidateQueries({ queryKey: queryKeys.dashboardAll })
  }
}

/** The sentence a refusal of the server carries, for the dialogs that stay open on one. */
export function failureText(error: unknown): string {
  return toProblem(error).detail
}

/** `PUT /fiscal/advance-method`: how the advances of a tax year are computed. */
export function useSetAdvanceMethod(legalEntityId: string) {
  const refresh = useRefreshTaxes()
  return useMutation({
    mutationFn: (body: { year: number; method: AdvanceMethod }) =>
      unwrap(
        api.PUT('/api/analytics/fiscal/advance-method', {
          params: { query: { legal_entity_id: legalEntityId } },
          body,
        }),
      ),
    onSuccess: () => {
      refresh()
      toast.success('Metodo degli acconti salvato')
    },
  })
}

/** `PUT /fiscal/instalment-plan`: in how many instalments the June payments of a year are paid;
 *  one instalment removes the plan. */
export function useSetInstalmentPlan(legalEntityId: string) {
  const refresh = useRefreshTaxes()
  return useMutation({
    mutationFn: (body: components['schemas']['TaxInstalmentPlanSet']) =>
      unwrap(
        api.PUT('/api/analytics/fiscal/instalment-plan', {
          params: { query: { legal_entity_id: legalEntityId } },
          body,
        }),
      ),
    onSuccess: (_data, body) => {
      refresh()
      toast.success(body.instalments === 1 ? 'Rateazione tolta' : 'Rateazione salvata')
    },
  })
}

export function useCreateNoticePlan(legalEntityId: string) {
  const refresh = useRefreshTaxes()
  return useMutation({
    mutationFn: (body: NoticePlanSet) =>
      unwrap(
        api.POST('/api/analytics/fiscal/notice-plans', {
          params: { query: { legal_entity_id: legalEntityId } },
          body,
        }),
      ),
    onSuccess: () => {
      refresh()
      toast.success('Piano di rate registrato')
    },
  })
}

export function useUpdateNoticePlan(planId: string) {
  const refresh = useRefreshTaxes()
  return useMutation({
    mutationFn: (body: NoticePlanUpdate) =>
      unwrap(
        api.PUT('/api/analytics/fiscal/notice-plans/{plan_id}', {
          params: { path: { plan_id: planId } },
          body,
        }),
      ),
    onSuccess: () => {
      refresh()
      toast.success('Piano di rate aggiornato')
    },
  })
}

export function useDeleteNoticePlan() {
  const refresh = useRefreshTaxes()
  return useMutation({
    mutationFn: (planId: string) =>
      unwrap(
        api.DELETE('/api/analytics/fiscal/notice-plans/{plan_id}', {
          params: { path: { plan_id: planId } },
        }),
      ),
    onSuccess: () => {
      refresh()
      toast.success('Piano di rate eliminato')
    },
  })
}

/** «Conferma la differenza»: an administrator confirms the difference an advance already paid
 *  left on a tax item. */
export function useConfirmTaxCarry() {
  const refresh = useRefreshTaxes()
  return useMutation({
    mutationFn: (itemId: string) =>
      unwrap(
        api.POST('/api/due-items/{item_id}/carry-confirmation', {
          params: { path: { item_id: itemId } },
        }),
      ),
    onSuccess: () => {
      refresh()
      toast.success('Differenza confermata')
    },
  })
}
