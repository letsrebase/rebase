import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from '@tanstack/react-query'
import { useInvoiceInvalidation } from '@/features/invoices/queries'
import { api, fetchWithRefresh, toProblem, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { queryKeys } from '@/lib/query'

export type BankAccount = components['schemas']['BankAccountRead']
export type BankAccountCreate = components['schemas']['BankAccountCreate']
export type BankAccountUpdate = components['schemas']['BankAccountUpdate']
export type AccountCoverage = components['schemas']['AccountCoverageRead']
export type CoverageMonth = components['schemas']['CoverageMonthRead']
export type Statement = components['schemas']['StatementRead']
export type StatementImportResult = components['schemas']['StatementImportResult']
export type Movement = components['schemas']['MovementRead']
export type MovementMatch = components['schemas']['MovementMatchRead']
export type MatchItem = components['schemas']['MatchItemRead']
export type MatchCandidate = components['schemas']['MatchCandidate']
export type MatchPreview = components['schemas']['MatchPreview']
export type MovementDetail = components['schemas']['MovementDetail']
export type MatchReason = MovementMatch['reasons'][number]

/** Statements and movements are read this many at a time; «Mostra altri» asks for the next page. */
export const PAGE_SIZE = 50

/** What every account read lives under, so one write refreshes the balance, the strip, the
 *  statements and the movements together. */
const ACCOUNTS = ['bank-accounts'] as const

const keys = {
  all: ACCOUNTS,
  list: (includeArchived: boolean) => [...ACCOUNTS, 'list', { includeArchived }] as const,
  coverage: (id: string) => [...ACCOUNTS, id, 'coverage'] as const,
  statements: (id: string) => [...ACCOUNTS, id, 'statements'] as const,
  movements: (id: string, state: MovementFilter, statementId: string | null) =>
    [...ACCOUNTS, id, 'movements', { state, statementId }] as const,
  // Under the accounts' key on purpose: any write that refreshes the account refreshes these.
  detail: (movementId: string) => [...ACCOUNTS, 'movement', movementId] as const,
  openItems: (movementId: string, search: string) => [...ACCOUNTS, 'movement', movementId, 'open-items', search] as const,
  preview: (movementId: string, dueItemIds: readonly string[]) =>
    [...ACCOUNTS, 'movement', movementId, 'preview', dueItemIds] as const,
}

export type MovementFilter = 'unmatched' | 'all'

/** After a write the account's own reads and the cash schedule (its balances and its alerts)
 *  are stale: the schedule lives under the dashboard key. */
function refreshAll(queryClient: QueryClient) {
  void queryClient.invalidateQueries({ queryKey: keys.all })
  void queryClient.invalidateQueries({ queryKey: queryKeys.dashboardAll })
}

/**
 * Every account of the space, the archived ones too, with the balance the server computed.
 * There is no read of one account: the page picks its own out of this list, which is also
 * what lets an archived account still open.
 */
export function useBankAccounts(enabled = true, includeArchived = true) {
  return useQuery({
    enabled,
    queryKey: keys.list(includeArchived),
    queryFn: () =>
      unwrap(api.GET('/api/bank-accounts', { params: { query: { include_archived: includeArchived } } })),
  })
}

/** The last twelve months of an account and whether a statement covers each. */
export function useCoverage(accountId: string, enabled = true) {
  return useQuery({
    enabled,
    queryKey: keys.coverage(accountId),
    queryFn: () =>
      unwrap(api.GET('/api/bank-accounts/{account_id}/coverage', { params: { path: { account_id: accountId } } })),
  })
}

/** The statements, newest period first, a page at a time. */
export function useStatements(accountId: string, enabled = true) {
  const query = useInfiniteQuery({
    enabled,
    queryKey: keys.statements(accountId),
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET('/api/bank-accounts/{account_id}/statements', {
          params: { path: { account_id: accountId }, query: { limit: PAGE_SIZE, cursor: pageParam } },
        }),
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  })
  const last = query.data?.pages.at(-1)
  return {
    items: query.data?.pages.flatMap((page) => page.items) ?? [],
    totalCount: last?.total_count,
    isLoading: query.isLoading,
    isError: query.isError,
    error: query.error,
    hasMore: query.hasNextPage,
    isFetchingMore: query.isFetchingNextPage,
    loadMore: () => void query.fetchNextPage(),
    retry: () => void query.refetch(),
  }
}

/** The movements across the account's statements, newest booking first, a page at a time. */
export function useMovements(
  accountId: string,
  state: MovementFilter,
  statementId: string | null,
  enabled = true,
) {
  const query = useInfiniteQuery({
    enabled,
    queryKey: keys.movements(accountId, state, statementId),
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET('/api/bank-accounts/{account_id}/movements', {
          params: {
            path: { account_id: accountId },
            query: {
              state,
              limit: PAGE_SIZE,
              cursor: pageParam,
              ...(statementId === null ? {} : { statement_id: statementId }),
            },
          },
        }),
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  })
  const last = query.data?.pages.at(-1)
  return {
    items: query.data?.pages.flatMap((page) => page.items) ?? [],
    totalCount: last?.total_count,
    isLoading: query.isLoading,
    isError: query.isError,
    error: query.error,
    hasMore: query.hasNextPage,
    isFetchingMore: query.isFetchingNextPage,
    loadMore: () => void query.fetchNextPage(),
    retry: () => void query.refetch(),
  }
}

export function useCreateBankAccount() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: BankAccountCreate) => unwrap(api.POST('/api/bank-accounts', { body })),
    onSuccess: () => refreshAll(queryClient),
  })
}

export function useUpdateBankAccount(accountId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: BankAccountUpdate) =>
      unwrap(
        api.PATCH('/api/bank-accounts/{account_id}', { params: { path: { account_id: accountId } }, body }),
      ),
    onSuccess: () => refreshAll(queryClient),
  })
}

export function useArchiveBankAccount(accountId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () =>
      unwrap(
        api.POST('/api/bank-accounts/{account_id}/archive', { params: { path: { account_id: accountId } } }),
      ),
    onSuccess: () => refreshAll(queryClient),
  })
}

export function useRevertStatement() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (statementId: string) =>
      unwrap(
        api.POST('/api/bank-statements/{statement_id}/revert', {
          params: { path: { statement_id: statementId } },
        }),
      ),
    onSuccess: () => refreshAll(queryClient),
  })
}

export interface StatementUpload {
  file: File
  /** Both or none: what a file that states no period covers. */
  period?: { from: string; to: string }
}

/**
 * The statement goes up as a multipart body through `fetchWithRefresh`, so the session cookie,
 * the space's prefix and one renewal of an expired session travel with it. A refused request
 * (the unreadable file, the period the file does not state) is a problem with its sentence and,
 * for the period, `field: 'period'`.
 */
async function uploadStatement(accountId: string, upload: StatementUpload): Promise<StatementImportResult> {
  const body = new FormData()
  body.append('file', upload.file, upload.file.name)
  if (upload.period !== undefined) {
    body.append('period_from', upload.period.from)
    body.append('period_to', upload.period.to)
  }
  const response = await fetchWithRefresh(`/api/bank-accounts/${accountId}/statements`, {
    method: 'POST',
    body,
  })
  const payload: unknown = await response.json().catch(() => null)
  if (!response.ok) throw toProblem(payload, response.status)
  return payload as StatementImportResult
}

export function useUploadStatement() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ accountId, upload }: { accountId: string; upload: StatementUpload }) =>
      uploadStatement(accountId, upload),
    onSuccess: () => refreshAll(queryClient),
  })
}

/** One movement with its candidates and its trace: what the matching panel draws. */
export function useMovementDetail(movementId: string | null) {
  return useQuery({
    enabled: movementId !== null,
    queryKey: keys.detail(movementId ?? ''),
    queryFn: () =>
      unwrap(api.GET('/api/bank-movements/{movement_id}', { params: { path: { movement_id: movementId! } } })),
  })
}

/** The open items of the movement's company, currency and direction, for «Cerca un'altra voce». */
export function useOpenItems(movementId: string, search: string, enabled: boolean) {
  return useQuery({
    enabled,
    queryKey: keys.openItems(movementId, search),
    queryFn: () =>
      unwrap(
        api.GET('/api/bank-movements/{movement_id}/open-items', {
          params: { path: { movement_id: movementId }, query: { search, limit: 20 } },
        }),
      ),
  })
}

/** The server's sums for a selection; the browser adds nothing. */
export function useMatchPreview(movementId: string, dueItemIds: readonly string[]) {
  return useQuery({
    enabled: dueItemIds.length > 0,
    queryKey: keys.preview(movementId, dueItemIds),
    queryFn: () =>
      unwrap(
        api.POST('/api/bank-movements/{movement_id}/match-preview', {
          params: { path: { movement_id: movementId } },
          body: { due_item_ids: [...dueItemIds] },
        }),
      ),
  })
}

/**
 * What a settlement or its undoing changes beyond the account: the invoice's and the purchase's own
 * payment state, which their lists, the year's register and the ceilings read. Mirrors what
 * marking an item paid from the schedule refreshes.
 */
function useRefreshSources() {
  const queryClient = useQueryClient()
  const invalidateInvoice = useInvoiceInvalidation()
  return (items: readonly MatchItem[]) => {
    for (const item of items) {
      if (item.source_kind === 'sales_invoice') {
        invalidateInvoice(item.source_id ?? undefined)
      } else if (item.source_kind === 'purchase') {
        void queryClient.invalidateQueries({ queryKey: queryKeys.purchases() })
        if (item.source_id) void queryClient.invalidateQueries({ queryKey: queryKeys.purchase(item.source_id) })
      }
    }
  }
}

export function useConfirmMatch(movementId: string) {
  const queryClient = useQueryClient()
  const refreshSources = useRefreshSources()
  return useMutation({
    mutationFn: (body: { due_item_ids: string[]; accept_partial: boolean }) =>
      unwrap(api.POST('/api/bank-movements/{movement_id}/match', { params: { path: { movement_id: movementId } }, body })),
    onSuccess: (detail) => {
      queryClient.setQueryData(keys.detail(movementId), detail)
      refreshAll(queryClient)
      refreshSources(detail.movement.match?.items ?? [])
    },
  })
}

export function useUnmatch() {
  const queryClient = useQueryClient()
  const refreshSources = useRefreshSources()
  return useMutation({
    mutationFn: ({ movementId, reason }: { movementId: string; reason?: string }) =>
      unwrap(
        api.POST('/api/bank-movements/{movement_id}/unmatch', {
          params: { path: { movement_id: movementId } },
          body: reason === undefined ? {} : { reason },
        }),
      ),
    onSuccess: (detail, { movementId }) => {
      queryClient.setQueryData(keys.detail(movementId), detail)
      refreshAll(queryClient)
      refreshSources(detail.history.at(-1)?.items ?? [])
    },
  })
}
