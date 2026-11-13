import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { useIsAdmin } from '@/lib/auth'
import { queryKeys, retryUnlessMissing } from '@/lib/query'
import { PAGE_SIZE, type EngagementBucket, type EngagementState } from './labels'

/**
 * Everything the pages of «Collaboratori» ask the API (REB-1035). Every read here is an
 * administrator's (`read_engagements`, `read_engagement_report`): a page asks only after the role
 * is known, so a collaborator's screen never sends a request that would be refused.
 */

type Create = components['schemas']['EngagementCreate']
type Update = components['schemas']['EngagementUpdate']
type Letter = components['schemas']['LetterRequest']
type Approval = components['schemas']['MonthlyReportApproval']

/** The filters of the list, besides the tab: the freelancer, the client and the state. */
export interface EngagementFilters {
  freelancerId?: string
  clientId?: string
  state?: EngagementState
  /** The engagement the hub opened under this id (REB-1105). */
  hubMatchId?: string
}

/**
 * One tab's engagements, newest first, 50 at a time: «Mostra altri» reads one more page. `null` is
 * every engagement of the filters, the freelancer's own tab on the registry's page.
 */
export function useEngagementList(bucket: EngagementBucket | null, filters: EngagementFilters = {}) {
  const admin = useIsAdmin()
  const query = useInfiniteQuery({
    enabled: admin,
    queryKey: queryKeys.engagementList({ bucket, ...filters }),
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET('/api/engagements', {
          params: {
            query: {
              bucket: bucket ?? undefined,
              freelancer_id: filters.freelancerId,
              client_organization_id: filters.clientId,
              state: filters.state,
              hub_match_id: filters.hubMatchId,
              limit: PAGE_SIZE,
              cursor: pageParam,
            },
          },
        }),
      ),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  })
  return {
    items: query.data?.pages.flatMap((page) => page.items) ?? [],
    isLoading: query.isPending && admin,
    isError: query.isError,
    error: query.error,
    hasMore: query.hasNextPage,
    isFetchingMore: query.isFetchingNextPage,
    fetchMore: () => void query.fetchNextPage(),
    refetch: () => void query.refetch(),
  }
}

/**
 * How many engagements each tab holds, as the server counts them over everything the reader
 * sees: the number the tabs wear and `todo` is the count beside «Collaboratori» in the sidebar and in
 * «Altro». Asked every minute; a failed read draws nothing and is asked again on the next tick.
 */
export function useEngagementCounts(enabled = true) {
  const admin = useIsAdmin()
  return useQuery({
    enabled: admin && enabled,
    queryKey: queryKeys.engagementCounts,
    staleTime: 30_000,
    refetchInterval: 60_000,
    retry: false,
    queryFn: () => unwrap(api.GET('/api/engagements/counts')),
  })
}

/** How many engagements wait for a person: the number on the entry (0 until the server answers). */
export function useEngagementsToDoCount(enabled = true): number {
  return useEngagementCounts(enabled).data?.todo ?? 0
}

/** One engagement with its names, what it waits for, its warnings and its stored letter. */
export function useEngagement(id: string) {
  const admin = useIsAdmin()
  return useQuery({
    enabled: admin,
    queryKey: queryKeys.engagement(id),
    retry: retryUnlessMissing,
    queryFn: () =>
      unwrap(api.GET('/api/engagements/{engagement_id}', { params: { path: { engagement_id: id } } })),
  })
}

/**
 * The engagement's days read live from the freelancer's space, month by month, with the
 * «Rendiconto» of each and what is payable. It can take long, so it is a read of its own: a
 * failure errors the months table and nothing else on the page.
 */
export function useEngagementReport(id: string, enabled = true) {
  const admin = useIsAdmin()
  return useQuery({
    enabled: admin && enabled,
    queryKey: queryKeys.engagementReport(id),
    // A 409 is the server's answer (the report is the leads space's alone): asking again says the same.
    retry: (count: number, error: unknown) =>
      (error as { status?: number }).status !== 409 && retryUnlessMissing(count, error),
    queryFn: () =>
      unwrap(
        api.GET('/api/engagements/{engagement_id}/report', {
          params: { path: { engagement_id: id }, query: {} },
        }),
      ),
  })
}

/** What the engagement pays the freelancer: the due items it wrote, with the month each pays for. */
export function useEngagementPayments(id: string, enabled = true) {
  const admin = useIsAdmin()
  return useQuery({
    enabled: admin && enabled,
    queryKey: queryKeys.engagementPayments(id),
    retry: retryUnlessMissing,
    queryFn: () =>
      unwrap(
        api.GET('/api/engagements/{engagement_id}/payments', {
          params: { path: { engagement_id: id } },
        }),
      ),
  })
}

/**
 * A freelancer's committed days a week, one cell a week from this one: twelve by default.
 * `excludingEngagementId` leaves an engagement out, so the page of a draft is not warned against
 * itself; `dateFrom` starts the strip on another week (the form's start date).
 */
export function useFreelancerAvailability(args: {
  freelancerId: string | null
  dateFrom?: string
  excludingEngagementId?: string
}) {
  const admin = useIsAdmin()
  return useQuery({
    enabled: admin && args.freelancerId !== null,
    queryKey: queryKeys.freelancerAvailability(args),
    retry: retryUnlessMissing,
    queryFn: () =>
      unwrap(
        api.GET('/api/freelancers/availability', {
          params: {
            query: {
              freelancer_id: args.freelancerId ?? undefined,
              date_from: args.dateFrom,
              excluding_engagement_id: args.excludingEngagementId,
            },
          },
        }),
      ),
  })
}

/** Everything a write to an engagement may have moved: the lists, the counts, the one page. */
function useInvalidateEngagements() {
  const queryClient = useQueryClient()
  return () => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.engagements })
  }
}

export function useCreateEngagement() {
  const invalidate = useInvalidateEngagements()
  return useMutation({
    mutationFn: (body: Create) => unwrap(api.POST('/api/engagements', { body })),
    onSettled: invalidate,
  })
}

export function useUpdateEngagement(id: string) {
  const invalidate = useInvalidateEngagements()
  return useMutation({
    mutationFn: (body: Update) =>
      unwrap(
        api.PATCH('/api/engagements/{engagement_id}', {
          params: { path: { engagement_id: id } },
          body,
        }),
      ),
    onSettled: invalidate,
  })
}

/** Moves the engagement along its states: sent for signature, signed, closed on a date, cancelled. */
export function useChangeEngagementState(id: string) {
  const invalidate = useInvalidateEngagements()
  return useMutation({
    mutationFn: (body: { state: EngagementState; ended_on?: string | null }) =>
      unwrap(
        api.POST('/api/engagements/{engagement_id}/state', {
          params: { path: { engagement_id: id } },
          body,
        }),
      ),
    onSettled: invalidate,
  })
}

/**
 * «Invia per la firma»: the freelancer's framework agreement first when they have none active, else
 * the letter (REB-1019). An installation without the signing site answers 503 `signing_unavailable`:
 * the page then offers the manual state change.
 */
export function useSendEngagement(id: string) {
  const invalidate = useInvalidateEngagements()
  return useMutation({
    mutationFn: () =>
      unwrap(api.POST('/api/engagements/{engagement_id}/send', { params: { path: { engagement_id: id } } })),
    onSettled: invalidate,
  })
}

/** «Anteprima»: the letter as it would be written now, nothing stored. */
export function usePreviewLetter(id: string) {
  return useMutation({
    mutationFn: (body: Letter) =>
      unwrap(
        api.POST('/api/engagements/{engagement_id}/letter/preview', {
          params: { path: { engagement_id: id } },
          body,
        }),
      ),
  })
}

/** «Crea la lettera»: writes the letter as a document of the freelancer, once, while a draft. */
export function useCreateLetter(id: string) {
  const invalidate = useInvalidateEngagements()
  return useMutation({
    mutationFn: (body: Letter) =>
      unwrap(
        api.POST('/api/engagements/{engagement_id}/letter', {
          params: { path: { engagement_id: id } },
          body,
        }),
      ),
    onSettled: invalidate,
  })
}

/**
 * «Registra l'arrivo»: rebase received the freelancer's report of `month` (`2026-10`) on a day the
 * person types. A person's act: no tool exists for it.
 */
export function useRecordMonthlyReport(id: string) {
  const invalidate = useInvalidateEngagements()
  return useMutation({
    mutationFn: (body: { month: string; received_on: string }) =>
      unwrap(
        api.POST('/api/engagements/{engagement_id}/monthly-reports', {
          params: { path: { engagement_id: id } },
          body,
        }),
      ),
    onSettled: invalidate,
  })
}

/**
 * «Approva il mese» (no body: nothing held) and «Contesta dei giorni…» (the days held and the
 * reason, the rest approved). A person's act: no tool exists for it, with or without `full_access`.
 */
export function useApproveMonthlyReport(id: string) {
  const invalidate = useInvalidateEngagements()
  return useMutation({
    mutationFn: (args: { month: string; body: Approval }) =>
      unwrap(
        api.POST('/api/engagements/{engagement_id}/monthly-reports/{month}/approval', {
          params: { path: { engagement_id: id, month: args.month } },
          body: args.body,
        }),
      ),
    onSettled: invalidate,
  })
}

/**
 * The freelancer's own card on a contract (REB-1035): the engagement rebase holds for them, read
 * from rebase's space by the API. A 404 is an answer, not a failure: the contract did not come from
 * an engagement, so the page draws no card; any other failure is the card's own error banner.
 */
export function useOwnEngagement(contractId: string) {
  // The route is an administrator's (the freelancer is the administrator of their own space): any
  // other role would be refused, so it is not asked.
  const admin = useIsAdmin()
  return useQuery({
    enabled: admin,
    queryKey: queryKeys.ownEngagement(contractId),
    retry: retryUnlessMissing,
    queryFn: () =>
      unwrap(
        api.GET('/api/contracts/{contract_id}/rebase-engagement', {
          params: { path: { contract_id: contractId } },
        }),
      ),
  })
}
