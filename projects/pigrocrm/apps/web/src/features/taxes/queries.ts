import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from '@rebase/ui/sonner'
import { useLegalEntity } from '@/lib/legalEntity'
import { api, toProblem, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { useCan } from '@/lib/auth'
import { queryKeys, retryUnlessMissing } from '@/lib/query'
import { toIsoDate } from '@/lib/dates'
import { homeDays, nextDays } from './dates'

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
 * The next tax days of one legal entity for the Home: the pack's dates and, when the role may
 * read the due items (`read_fiscal_due_items`, a gate of its own: a role that reads the calendar
 * and not the items still sees the calendar), the items «Tasse» draws, through the same queries
 * and the same merge (`homeDays`). Dates are read from this calendar year and, when fewer are
 * left, from the next (the December advance is followed by January's payments). `hasCalendar`
 * says whether the pack carries dates at all, so a pack with none is told from a quiet stretch;
 * the Home draws nothing for the first. A failed read of the items is not the block's failure:
 * the dates are still true, and the block draws them alone.
 */
export function useNextTaxDates(legalEntityId?: string | null, enabled = true) {
  const allowed = useCan('get_tax_calendar') && enabled
  const itemsAllowed = useCan('read_fiscal_due_items') && allowed
  const now = new Date()
  const today = toIsoDate(now)
  const year = now.getFullYear()
  const current = useTaxCalendar(year, legalEntityId, allowed)
  const ahead = current.data ? nextDays(current.data.dates, today, HOME_DATES) : []
  const needMore = current.isSuccess && ahead.length < HOME_DATES
  const following = useTaxCalendar(year + 1, legalEntityId, allowed && needMore)
  const entity = current.data?.legal_entity_id ?? null
  const items = useTaxDueItems(year, entity, itemsAllowed && current.isSuccess)
  const nextItems = useTaxDueItems(year + 1, entity, itemsAllowed && needMore)
  const entries = [
    ...(current.data?.dates ?? []),
    ...(needMore ? (following.data?.dates ?? []) : []),
  ]
  const dueItems = [...(items.data ?? []), ...(needMore ? (nextItems.data ?? []) : [])]
  const days = homeDays(entries, dueItems, today, HOME_DATES)
  const waiting =
    current.isPending ||
    (needMore && following.isPending) ||
    (itemsAllowed && current.isSuccess && items.isPending) ||
    (itemsAllowed && needMore && nextItems.isPending)
  return {
    today,
    days,
    waiting,
    error: current.error ?? (needMore ? following.error : null),
    hasCalendar: (current.data?.dates.length ?? 0) + (following.data?.dates.length ?? 0) > 0,
    refetch: () => {
      void current.refetch()
      if (needMore) void following.refetch()
      if (items.isError) void items.refetch()
      if (needMore && nextItems.isError) void nextItems.refetch()
    },
  }
}

export type TaxDueItem = components['schemas']['DueItemRead']
export type F24Group = components['schemas']['F24GroupRead']
export type F24Line = components['schemas']['F24LineRead']
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
  'tax_credit',
] as const

/** The kinds that are an instalment of a plan: the «Rate» tab reads these for any year. */
const PLAN_ITEM_KINDS = ['tax_instalment', 'notice_plan'] as const

/** One request is 200 items at most, the page's biggest; a legal entity's year holds a few dozen. */
const ITEMS_LIMIT = 200

/** What `useTaxDueItems` reads: the open items (`forecast` and `expected` come together when
 *  `state` is left out) and the settled ones of one tax source kind, every page, so a busy year of
 *  purchases and payroll never pushes a tax item off the end. */
async function readKind(
  legalEntityId: string,
  kind: string,
  extra: { due_from?: string; due_to?: string },
): Promise<TaxDueItem[]> {
  const found: TaxDueItem[] = []
  for (const state of [undefined, 'settled' as const]) {
    let cursor: string | undefined
    do {
      const page = await unwrap(
        api.GET('/api/due-items', {
          params: {
            query: {
              legal_entity_id: legalEntityId,
              direction: 'out',
              source_kind: kind,
              limit: ITEMS_LIMIT,
              ...(state === undefined ? {} : { state }),
              ...(cursor === undefined ? {} : { cursor }),
              ...extra,
            },
          },
        }),
      )
      found.push(...page.items)
      cursor = page.next_cursor ?? undefined
    } while (cursor !== undefined)
  }
  return found
}

/** The tax items of `kinds`, one request series per kind (never the IVA settlements or the
 *  purchases that share the table), each item once. */
async function readTaxItems(
  legalEntityId: string,
  kinds: readonly string[],
  extra: { due_from?: string; due_to?: string },
): Promise<TaxDueItem[]> {
  const lists = await Promise.all(kinds.map((kind) => readKind(legalEntityId, kind, extra)))
  const byId = new Map<string, TaxDueItem>()
  for (const item of lists.flat()) {
    if (TAX_ITEM_KINDS.some((kind) => kind === item.source_kind)) byId.set(item.id, item)
  }
  return [...byId.values()]
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
      readTaxItems(legalEntityId ?? '', TAX_ITEM_KINDS, {
        due_from: `${year}-01-01`,
        due_to: `${year}-12-31`,
      }),
    enabled: enabled && legalEntityId !== null,
    retry: retryUnlessMissing,
  })
}

/** The instalments of the plans, any year: the «Rate» tab's next instalment and its state. */
export function useTaxPlanItems(legalEntityId: string | null, enabled = true) {
  return useQuery({
    queryKey: queryKeys.taxDueItems('plans', legalEntityId),
    queryFn: () => readTaxItems(legalEntityId ?? '', PLAN_ITEM_KINDS, {}),
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
export function useRefreshTaxes() {
  const client = useQueryClient()
  return () => {
    void client.invalidateQueries({ queryKey: ['fiscal-estimate'] })
    void client.invalidateQueries({ queryKey: queryKeys.taxDueItemsAll })
    void client.invalidateQueries({ queryKey: ['tax-plans'] })
    void client.invalidateQueries({ queryKey: ['f24-groups'] })
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

/** The F24 groups of one legal entity whose payment date falls in calendar `year`, open and paid
 *  (a dissolved group is gone from the page), every page. */
export function useF24Groups(year: number, legalEntityId: string | null, enabled = true) {
  return useQuery({
    queryKey: queryKeys.taxF24Groups(year, legalEntityId),
    queryFn: async () => {
      const found: F24Group[] = []
      for (const state of ['open', 'settled'] as const) {
        let cursor: string | undefined
        do {
          const page = await unwrap(
            api.GET('/api/f24-groups', {
              params: {
                query: {
                  state,
                  legal_entity_id: legalEntityId ?? '',
                  due_from: `${year}-01-01`,
                  due_to: `${year}-12-31`,
                  limit: ITEMS_LIMIT,
                  ...(cursor === undefined ? {} : { cursor }),
                },
              },
            }),
          )
          found.push(...page.items)
          cursor = page.next_cursor ?? undefined
        } while (cursor !== undefined)
      }
      return [...new Map(found.map((group) => [group.id, group])).values()]
    },
    enabled: enabled && legalEntityId !== null,
    retry: retryUnlessMissing,
  })
}

/** The inbox's tax documents a receipt can be picked from. */
export function useTaxDocuments(enabled: boolean) {
  return useQuery({
    queryKey: ['inbox-tax-documents'],
    queryFn: () =>
      unwrap(
        api.GET('/api/inbox/items', {
          params: { query: { kind: 'tax_document', limit: 50 } },
        }),
      ),
    enabled,
  })
}

/** «Raggruppa in un F24»: the due items of one date paid with one document. */
export function useGroupF24() {
  const refresh = useRefreshTaxes()
  return useMutation({
    mutationFn: (dueItemIds: string[]) =>
      unwrap(api.POST('/api/f24-groups', { body: { due_item_ids: dueItemIds } })),
    onSuccess: () => {
      refresh()
      toast.success('F24 creato')
    },
  })
}

/** «Sciogli l’F24»: releases the lines of a group nobody has paid. */
export function useUngroupF24() {
  const refresh = useRefreshTaxes()
  return useMutation({
    mutationFn: (groupId: string) =>
      unwrap(
        api.POST('/api/f24-groups/{group_id}/ungroup', {
          params: { path: { group_id: groupId } },
        }),
      ),
    onSuccess: () => {
      refresh()
      toast.success('F24 sciolto')
    },
  })
}

export function useAttachReceipt(groupId: string) {
  const refresh = useRefreshTaxes()
  return useMutation({
    mutationFn: (inboxItemId: string) =>
      unwrap(
        api.POST('/api/f24-groups/{group_id}/receipts', {
          params: { path: { group_id: groupId } },
          body: { inbox_item_id: inboxItemId },
        }),
      ),
    onSuccess: refresh,
  })
}

export function useDetachReceipt(groupId: string) {
  const refresh = useRefreshTaxes()
  return useMutation({
    mutationFn: (inboxItemId: string) =>
      unwrap(
        api.DELETE('/api/f24-groups/{group_id}/receipts/{inbox_item_id}', {
          params: { path: { group_id: groupId, inbox_item_id: inboxItemId } },
        }),
      ),
    onSuccess: refresh,
  })
}
