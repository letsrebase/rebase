import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from '@tanstack/react-query'
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
