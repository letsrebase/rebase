import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, fetchWithRefresh, toProblem, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { useIsAdmin } from '@/lib/auth'
import { queryKeys } from '@/lib/query'
import {
  PAGE_SIZE,
  TABS,
  type InboxItem,
  type InboxKind,
  type InboxState,
  type InboxTab,
} from './labels'

type KindBody = components['schemas']['InboxKindSet']

/** The most one request of the waiting count reads: past it the sidebar says «99+» anyway. */
const COUNT_LIMIT = 200

function listState(state: InboxState | null, kind: InboxKind | null) {
  return {
    enabled: state !== null,
    queryKey: queryKeys.inboxList(state ?? 'none', kind),
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }: { pageParam: string | undefined }) =>
      unwrap(
        api.GET('/api/inbox/items', {
          params: {
            query: {
              state: state ?? undefined,
              kind: kind ?? undefined,
              limit: PAGE_SIZE,
              cursor: pageParam,
            },
          },
        }),
      ),
    getNextPageParam: (page: { next_cursor: string | null }) => page.next_cursor ?? undefined,
  }
}

/**
 * One tab's items, newest first. A tab may list two states (`new` and `proposed` wait for
 * a person, `discarded` and `duplicate` were put aside) and the API filters by one, so each
 * state is its own keyset list and the tab merges them by id, which is time-ordered (UUIDv7).
 * «Mostra altri» reads one more page of whichever state still has one.
 */
export function useInboxList(tab: InboxTab, kind: InboxKind | null) {
  const [first, second] = TABS.find((candidate) => candidate.id === tab)?.states ?? []
  const one = useInfiniteQuery(listState(first ?? null, kind))
  const two = useInfiniteQuery(listState(second ?? null, kind))
  const lists = [one, ...(second === undefined ? [] : [two])]
  const items: InboxItem[] = lists
    .flatMap((query) => query.data?.pages.flatMap((page) => page.items) ?? [])
    .sort((a, b) => (a.id < b.id ? 1 : a.id > b.id ? -1 : 0))
  // A list that failed to load is `failed`; one whose later page failed keeps its rows and is
  // `failedMore`, which the page says under them instead of replacing them.
  const failed = lists.find((query) => query.isError && !query.isFetchNextPageError)
  const failedMore = lists.find((query) => query.isFetchNextPageError)
  // What the filter matches in all: each state's own total, read off the page last fetched.
  const total = lists.reduce((sum, query) => sum + (query.data?.pages.at(-1)?.total ?? 0), 0)
  return {
    items,
    total,
    isLoading: lists.some((query) => query.isPending),
    isError: failed !== undefined,
    error: failed?.error,
    isFetchMoreError: failedMore !== undefined,
    fetchMoreError: failedMore?.error,
    hasMore: lists.some((query) => query.hasNextPage),
    isFetchingMore: lists.some((query) => query.isFetchingNextPage),
    fetchMore: () => {
      for (const query of lists) if (query.hasNextPage) void query.fetchNextPage()
    },
    refetch: () => {
      for (const query of lists) void query.refetch()
    },
  }
}

/**
 * How many items each state holds, as the API counts them (`total` of a one-item page, REB-991):
 * the number the tabs wear and the sidebar's count is made of. The reader's own, refreshed every
 * minute; a failed read draws nothing and is asked again on the next tick.
 */
export function useStateTotals(): Partial<Record<InboxState, number>> {
  const { data } = useQuery({
    queryKey: queryKeys.inboxCount,
    staleTime: 30_000,
    refetchInterval: 60_000,
    retry: false,
    queryFn: async () => {
      const states: readonly InboxState[] = ['new', 'proposed', 'filed', 'duplicate', 'discarded']
      const pages = await Promise.all(
        states.map((state) =>
          unwrap(api.GET('/api/inbox/items', { params: { query: { state, limit: 1 } } })),
        ),
      )
      return Object.fromEntries(states.map((state, index) => [state, pages[index]?.total ?? 0]))
    },
  })
  return data ?? {}
}

/** How many items wait for a person: the number beside «In arrivo» in the sidebar and in «Altro». */
export function useWaitingCount(): number {
  const totals = useStateTotals()
  return (totals.new ?? 0) + (totals.proposed ?? 0)
}

/** How many items nobody has said what they are yet: what «Leggi i documenti in attesa» would read. */
export function useUnreadCount(enabled: boolean): number {
  const { data } = useQuery({
    enabled,
    queryKey: queryKeys.inboxUnread,
    retry: false,
    queryFn: () =>
      unwrap(
        api.GET('/api/inbox/items', { params: { query: { classified: false, limit: COUNT_LIMIT } } }),
      ),
    select: (page) => page.items.filter((item) => item.has_file).length,
  })
  return data ?? 0
}

/** One item with what was read of it: its parsed fields and its text. */
export function useInboxItem(id: string | null) {
  return useQuery({
    enabled: id !== null,
    queryKey: queryKeys.inboxItem(id ?? ''),
    queryFn: () =>
      unwrap(
        api.GET('/api/inbox/items/{item_id}', { params: { path: { item_id: id ?? '' } } }),
      ),
  })
}

/** The items that are copies of one that came first: what the panel says «arrivata anche» for. */
export function useCopiesOf(id: string, enabled: boolean) {
  return useQuery({
    enabled,
    queryKey: queryKeys.inboxCopies,
    queryFn: () =>
      unwrap(
        api.GET('/api/inbox/items', {
          params: { query: { state: 'duplicate', limit: COUNT_LIMIT } },
        }),
      ),
    select: (page) => page.items.filter((item) => item.duplicate_of_id === id),
  })
}

/** The newest files, for the picker of «Segna come doppione»: any that is not a copy itself. */
export function useOriginalCandidates(excludeId: string, enabled: boolean) {
  return useQuery({
    enabled,
    queryKey: queryKeys.inboxOriginals,
    queryFn: () =>
      unwrap(api.GET('/api/inbox/items', { params: { query: { limit: COUNT_LIMIT } } })),
    select: (page) =>
      page.items.filter(
        (item) => item.id !== excludeId && item.state !== 'duplicate' && item.kind !== 'archive',
      ),
  })
}

/**
 * Whether PigroCRM's own model reads documents, for «Leggi i documenti in attesa». Only an
 * administrator with no company limit may read the model's settings (`GET /api/settings/ai-model`,
 * REB-972): nobody else can know, so the button is drawn for that administrator alone, as the
 * card says. A refusal (a limited administrator) reads as off.
 */
export function useModelReads(): boolean {
  const admin = useIsAdmin()
  const { data } = useQuery({
    enabled: admin,
    queryKey: queryKeys.aiModel,
    retry: false,
    queryFn: () => unwrap(api.GET('/api/settings/ai-model')),
  })
  return admin && data?.enabled === true
}

/**
 * `POST /api/inbox/upload`: the files of one request, one item each. Outside `openapi-fetch`
 * for the reason `putVersion` documents (it cannot send a `FormData`), through
 * `fetchWithRefresh` so the cookie and the space's prefix travel and an expired session is
 * renewed once. A refused request is a 422 with the sentence that says why and nothing written.
 */
export async function uploadInboxFiles(files: readonly File[]): Promise<InboxItem[]> {
  const body = new FormData()
  for (const file of files) body.append('file', file, file.name)
  const response = await fetchWithRefresh('/api/inbox/upload', { method: 'POST', body })
  const payload: unknown = await response.json().catch(() => null)
  if (!response.ok) throw toProblem(payload, response.status)
  return (payload as components['schemas']['InboxUploadResult']).items
}

/** Everything a write to an item may have moved: the lists, the count, the proposals queue. */
function useInvalidateInbox() {
  const queryClient = useQueryClient()
  return () => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.inbox })
    void queryClient.invalidateQueries({ queryKey: ['proposals'] })
  }
}

export function useAfterUpload() {
  return useInvalidateInbox()
}

export function useDiscardItem() {
  const invalidate = useInvalidateInbox()
  return useMutation({
    mutationFn: (args: { id: string; reason: string }) =>
      unwrap(
        api.POST('/api/inbox/items/{item_id}/discard', {
          params: { path: { item_id: args.id } },
          body: { reason: args.reason },
        }),
      ),
    onSettled: invalidate,
  })
}

export function useMarkDuplicate() {
  const invalidate = useInvalidateInbox()
  return useMutation({
    mutationFn: (args: { id: string; originalId: string }) =>
      unwrap(
        api.POST('/api/inbox/items/{item_id}/duplicate', {
          params: { path: { item_id: args.id } },
          body: { original_id: args.originalId },
        }),
      ),
    onSettled: invalidate,
  })
}

export function useSetKind() {
  const invalidate = useInvalidateInbox()
  return useMutation({
    mutationFn: (args: { id: string } & KindBody) =>
      unwrap(
        api.POST('/api/inbox/items/{item_id}/kind', {
          params: { path: { item_id: args.id } },
          body: { kind: args.kind, legal_entity_id: args.legal_entity_id },
        }),
      ),
    onSettled: invalidate,
  })
}

/** «Leggi con il modello» on one item. */
export function useClassifyItem() {
  const invalidate = useInvalidateInbox()
  return useMutation({
    mutationFn: (id: string) =>
      unwrap(
        api.POST('/api/inbox/items/{item_id}/classify', { params: { path: { item_id: id } } }),
      ),
    onSettled: invalidate,
  })
}

/** «Leggi i documenti in attesa»: the oldest unread files, as many as one request reads. */
export function useClassifyPending() {
  const invalidate = useInvalidateInbox()
  return useMutation({
    mutationFn: () => unwrap(api.POST('/api/inbox/classify', { params: { query: {} } })),
    onSettled: invalidate,
  })
}
