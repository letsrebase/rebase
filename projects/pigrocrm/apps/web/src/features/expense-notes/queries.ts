import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { useIsAdmin } from '@/lib/auth'
import { useLegalEntityScope } from '@/lib/legalEntity'

export type ExpenseNote = components['schemas']['ExpenseNoteRead']
export type ExpenseNoteBody = components['schemas']['ExpenseNoteCreate']
export type ExpenseNoteSummary = components['schemas']['ExpenseNoteSummary']
export type ReimburseBody = components['schemas']['ExpenseNoteReimburse']
export type NoteStatus = ExpenseNote['status']
export type NoteKind = ExpenseNote['kind']
export type Bucket = 'to_approve' | 'to_reimburse' | 'closed'

/** A screen reads fifty notes at a time (the brief: «Mostra altri»). */
export const NOTES_PAGE_SIZE = 50

/** The most one request reads: the staff behind «Per chi?» and «Persona» are a company's own
 *  people, far fewer than a page of the API. */
const STAFF_CHOICES_LIMIT = 200

export const expenseNoteKeys = {
  all: ['expense-notes'] as const,
  summary: ['expense-notes', 'summary'] as const,
  list: (params: NotesParams) => ['expense-notes', 'list', params] as const,
  one: (id: string) => ['expense-notes', 'one', id] as const,
  staff: ['expense-notes', 'staff'] as const,
}

/**
 * Who the caller is among the staff, the count of each tab and the euro still to be paid back
 * (`GET /api/expense-notes/summary`). It is also how the web learns whether a caller is a staff
 * member at all: `GET /api/staff` is an administrator's. Asked again every minute, since the
 * sidebar's count rides on it; a failed read draws nothing and is asked again on the next tick.
 */
export function useExpenseSummary(enabled = true) {
  return useQuery({
    enabled,
    queryKey: expenseNoteKeys.summary,
    staleTime: 30_000,
    refetchInterval: 60_000,
    retry: false,
    queryFn: () => unwrap(api.GET('/api/expense-notes/summary')),
  })
}

/** How many notes wait for an administrator's decision: the number on the entry. 0 until the
 *  server answers, and for anyone who is not an administrator, whose summary counts their own. */
export function useNotesToApproveCount(enabled = true): number {
  const admin = useIsAdmin()
  const summary = useExpenseSummary(enabled && admin)
  return admin ? (summary.data?.to_approve ?? 0) : 0
}

export interface NotesParams {
  bucket?: Bucket
  status?: NoteStatus
  staff_member_id?: string
  category_id?: string
  legal_entity_id?: string
}

/**
 * The notes of one reading, a page at a time with the cursor, and the figures of the whole
 * selection the server answers with (`total_count`, `total_amount`): never summed from the rows
 * on screen, which are only the pages read so far. A staff member's list is their own and the
 * server says so; an administrator's follows the sidebar's azienda unless the page names one.
 */
export function useExpenseNotes(params: NotesParams, { scoped }: { scoped: boolean }) {
  const scopedParams = useLegalEntityScope(params)
  const sent = scoped && params.legal_entity_id === undefined ? scopedParams : params
  const query = useInfiniteQuery({
    queryKey: expenseNoteKeys.list(sent),
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET('/api/expense-notes', {
          params: { query: { ...sent, limit: NOTES_PAGE_SIZE, cursor: pageParam } },
        }),
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  })
  const pages = query.data?.pages
  const last = pages?.at(-1)
  return {
    items: pages?.flatMap((page) => page.items) ?? [],
    /** Undefined until the first page lands, so a screen never draws a figure it has not got. */
    totalCount: last?.total_count,
    totalAmount: last?.total_amount,
    isLoading: query.isLoading,
    isError: query.isError,
    error: query.error,
    hasMore: query.hasNextPage,
    isFetchingMore: query.isFetchingNextPage,
    loadMore: () => void query.fetchNextPage(),
    retry: () => void query.refetch(),
  }
}

/** One note, for a link that opens it when the list has not (or not yet) read it. */
export function useExpenseNote(id: string | undefined, enabled: boolean) {
  return useQuery({
    enabled: enabled && id !== undefined,
    queryKey: expenseNoteKeys.one(id ?? ''),
    retry: false,
    queryFn: () =>
      unwrap(api.GET('/api/expense-notes/{note_id}', { params: { path: { note_id: id ?? '' } } })),
  })
}

/** The people a note can be for, an administrator's read (`GET /api/staff`). */
export function useStaffChoices(enabled: boolean) {
  return useQuery({
    enabled,
    queryKey: expenseNoteKeys.staff,
    queryFn: () =>
      unwrap(api.GET('/api/staff', { params: { query: { limit: STAFF_CHOICES_LIMIT } } })),
  })
}

/** A note moves the lists, the counts, and, once approved, the purchases it becomes. */
function useRefresh() {
  const client = useQueryClient()
  return () =>
    Promise.all([
      client.invalidateQueries({ queryKey: expenseNoteKeys.all }),
      client.invalidateQueries({ queryKey: ['purchases'] }),
    ])
}

export function useSubmitNote() {
  const refresh = useRefresh()
  return useMutation({
    mutationFn: (body: ExpenseNoteBody) => unwrap(api.POST('/api/expense-notes', { body })),
    onSuccess: refresh,
  })
}

export function useApproveNote() {
  const refresh = useRefresh()
  return useMutation({
    mutationFn: (id: string) =>
      unwrap(api.POST('/api/expense-notes/{note_id}/approve', { params: { path: { note_id: id } } })),
    onSuccess: refresh,
  })
}

export function useRejectNote() {
  const refresh = useRefresh()
  return useMutation({
    mutationFn: (args: { id: string; reason: string }) =>
      unwrap(
        api.POST('/api/expense-notes/{note_id}/reject', {
          params: { path: { note_id: args.id } },
          body: { reason: args.reason },
        }),
      ),
    onSuccess: refresh,
  })
}

/**
 * The way of paying one note back. A group of notes is one call per note, sent by the caller one
 * after the other, so the lists are read again whatever came of it: a note decided before the
 * call that failed is already out of the tab.
 */
export function useReimburseNote() {
  const refresh = useRefresh()
  return useMutation({
    mutationFn: (args: { id: string; body: ReimburseBody }) =>
      unwrap(
        api.POST('/api/expense-notes/{note_id}/reimburse', {
          params: { path: { note_id: args.id } },
          body: args.body,
        }),
      ),
    onSettled: refresh,
  })
}
