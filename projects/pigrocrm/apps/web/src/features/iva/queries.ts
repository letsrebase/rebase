import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { useAuth } from '@/lib/auth'
import { useExpenseSummary } from '@/features/expense-notes/queries'
import { useModules } from '@/lib/modules'
import { can } from '@/lib/permissions'
import { queryKeys } from '@/lib/query'
import type { NavGate } from '@/components/nav'

/** The wire shapes come from the generated OpenAPI schema; every amount is a decimal string. */
export type IvaEntity = components['schemas']['IvaEntityRead']
export type IvaPeriod = components['schemas']['IvaPeriodRead']
export type IvaLadder = components['schemas']['IvaLadderRead']
export type IvaSalesRegister = components['schemas']['IvaSalesRegisterRead']
export type IvaSalesRow = components['schemas']['IvaSalesRowRead']
export type IvaPurchaseRegister = components['schemas']['IvaPurchaseRegisterRead']
export type IvaPurchaseRow = components['schemas']['IvaPurchaseRowRead']
export type IvaPeriodLock = components['schemas']['IvaPeriodLockRead']

/** A register is read 50 rows at a time (the brief: «Mostra altri»). */
export const REGISTER_PAGE_SIZE = 50

/**
 * The legal entities whose pack settles IVA by period (`GET /api/iva-periods/entities`): the ones
 * that have registers. `enabled` is the caller's role and module gate, so a role that may not read
 * it, or a space with `purchases` off, makes no request.
 */
export function useIvaEntities(enabled = true) {
  return useQuery({
    enabled,
    queryKey: queryKeys.ivaEntities(),
    queryFn: () => unwrap(api.GET('/api/iva-periods/entities')),
  })
}

/**
 * The entries the sidebar and «Altro» draw that need more than a module (`NavGate`): «IVA» is an
 * administrator's, in a space with `purchases` on and at least one legal entity whose pack settles
 * IVA by period; «Note spese» is an administrator's or a user linked to a staff member's, with
 * `staff` and `purchases` both on (REB-1046). Role alone cannot say «linked», so that gate reads
 * the expense notes' summary, which also carries the count the entry shows. Until an answer is in
 * the entry that waits on it is not drawn, so it never flashes and goes.
 */
export function useNavGates(): ReadonlySet<NavGate> {
  const { user } = useAuth()
  const modules = useModules()
  const allowed = user !== null && can(user.role, 'list_iva_periods')
  const purchases = modules.on('purchases')
  const entities = useIvaEntities(allowed && purchases)
  const notesOn = user !== null && modules.on('staff', 'purchases')
  const summary = useExpenseSummary(notesOn)
  const gates = new Set<NavGate>()
  if (allowed && purchases && (entities.data?.length ?? 0) > 0) gates.add('iva')
  if (
    user !== null &&
    notesOn &&
    (can(user.role, 'approve_expense_note') || (summary.data?.staff_member_id ?? null) !== null)
  ) {
    gates.add('expense-notes')
  }
  return gates
}

/** A year's periods of one legal entity, each with its ladder and state (`GET /api/iva-periods`). */
export function useIvaPeriods(year: number, legalEntityId: string | null, enabled = true) {
  return useQuery({
    enabled: enabled && legalEntityId !== null,
    queryKey: queryKeys.ivaPeriods(year, legalEntityId),
    queryFn: () =>
      unwrap(
        api.GET('/api/iva-periods', {
          params: { query: { year, legal_entity_id: legalEntityId ?? undefined } },
        }),
      ),
  })
}

/**
 * One period's sales register, a page at a time by offset, with the summary by rate and the
 * period's totals the server answers with: never added from the rows on screen, which are only the
 * pages read so far.
 */
export function useSalesRegister(
  year: number,
  number: number | null,
  legalEntityId: string | null,
  enabled = true,
) {
  const query = useInfiniteQuery({
    enabled: enabled && number !== null && legalEntityId !== null,
    queryKey: queryKeys.ivaRegister('sales', year, number ?? 0, legalEntityId),
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET('/api/iva-registers/sales', {
          params: {
            query: {
              year,
              number: number ?? 1,
              legal_entity_id: legalEntityId ?? undefined,
              limit: REGISTER_PAGE_SIZE,
              offset: pageParam,
            },
          },
        }),
      ),
    initialPageParam: 0,
    getNextPageParam: (lastPage) => lastPage.next_offset ?? undefined,
  })
  const first = query.data?.pages[0]
  return {
    rows: query.data?.pages.flatMap((page) => page.items) ?? [],
    /** Undefined until the first page lands, so a screen never draws a figure it has not got. */
    totals: first?.totals,
    summary: first?.summary,
    totalCount: first?.total_count,
    isLoading: query.isLoading,
    isError: query.isError,
    error: query.error,
    hasMore: query.hasNextPage,
    isFetchingMore: query.isFetchingNextPage,
    loadMore: () => void query.fetchNextPage(),
    retry: () => void query.refetch(),
  }
}

/** One period's purchase register, as `useSalesRegister` reads the sales one. */
export function usePurchaseRegister(
  year: number,
  number: number | null,
  legalEntityId: string | null,
  enabled = true,
) {
  const query = useInfiniteQuery({
    enabled: enabled && number !== null && legalEntityId !== null,
    queryKey: queryKeys.ivaRegister('purchases', year, number ?? 0, legalEntityId),
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET('/api/iva-registers/purchases', {
          params: {
            query: {
              year,
              number: number ?? 1,
              legal_entity_id: legalEntityId ?? undefined,
              limit: REGISTER_PAGE_SIZE,
              offset: pageParam,
            },
          },
        }),
      ),
    initialPageParam: 0,
    getNextPageParam: (lastPage) => lastPage.next_offset ?? undefined,
  })
  const first = query.data?.pages[0]
  return {
    rows: query.data?.pages.flatMap((page) => page.items) ?? [],
    totals: first?.totals,
    summary: first?.summary,
    totalCount: first?.total_count,
    isLoading: query.isLoading,
    isError: query.isError,
    error: query.error,
    hasMore: query.hasNextPage,
    isFetchingMore: query.isFetchingNextPage,
    loadMore: () => void query.fetchNextPage(),
    retry: () => void query.refetch(),
  }
}

/**
 * Closes a period that has ended (`POST /api/iva-period-locks`): the server settles it and locks it
 * in one transaction. The periods are read again after it, since the closed state, the pill and the
 * ladder all come from the server.
 */
export function useCloseIvaPeriod() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: { year: number; number: number; legalEntityId: string }) =>
      unwrap(
        api.POST('/api/iva-period-locks', {
          body: { year: body.year, number: body.number, legal_entity_id: body.legalEntityId },
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.ivaPeriodsAll() }),
  })
}

/** Reopens a closed period (`DELETE /api/iva-period-locks/{year}/{number}`), leaving a trace. */
export function useReopenIvaPeriod() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (target: { year: number; number: number; legalEntityId: string }) =>
      unwrap(
        api.DELETE('/api/iva-period-locks/{year}/{number}', {
          params: {
            path: { year: target.year, number: target.number },
            query: { legal_entity_id: target.legalEntityId },
          },
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.ivaPeriodsAll() }),
  })
}
