import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { useCallback, useMemo } from 'react'
import { useOrganization } from '@/features/organizations/queries'
import { useDeal } from '@/features/deals/queries'
import { useInvoice } from '@/features/invoices/queries'
import { usePerson } from '@/features/people/queries'
import { useUsers } from '@/features/settings/queries'
import { api, unwrap } from '@/lib/api'
import { useAuth, useIsAdmin } from '@/lib/auth'
import { useModules } from '@/lib/modules'
import type { Task } from '@/features/calendar/queries'
import type { components } from '@/lib/api-types'
import type { LinkKind, LinkValue } from './links'
import { queryKeys } from '@/lib/query'

/**
 * The name of the record a task hangs from, read by id.
 *
 * Four reads behind one hook, three of them switched off: a hook cannot be called
 * conditionally, so each is `enabled` only for its own kind and an empty id never reaches
 * the network. The row already holds the id and nothing else, and a link drawn as a bare
 * UUID would be a link nobody could read.
 */
export function useRecordLabel(link: LinkValue | null): string | undefined {
  // A deal is not read while `deals` is off (REB-953): its route answers 404.
  const dealsOn = useModules().on('deals')
  const is = (kind: LinkKind) => link?.kind === kind
  const id = link?.id ?? ''
  const customer = useOrganization(id, { enabled: is('organization') })
  const person = usePerson(id, { enabled: is('person') })
  const deal = useDeal(id, { enabled: is('deal') && dealsOn })
  const invoice = useInvoice(is('invoice') ? id : '')

  if (link === null) return undefined
  switch (link.kind) {
    case 'organization':
      return customer.data?.legal_name
    case 'person':
      return person.data ? [person.data.first_name, person.data.last_name].filter(Boolean).join(' ') : undefined
    case 'deal':
      return deal.data?.name
    case 'invoice': {
      const data = invoice.data
      if (!data) return undefined
      // The number is assigned at emission: until then the state stands in for it, the
      // same rule the global search's label follows.
      const prefix = data.year !== null && data.number !== null ? `${data.year}/${data.number}` : data.status
      return data.memo ? `${prefix} · ${data.memo}` : prefix
    }
  }
}

/**
 * The link a task shows: the record it hangs from, or `null` when that record's page is not
 * there (REB-953, spec 2026-11-09 A2): a task linked to a deal while `deals` is off shows no
 * link rather than one to a 404. The link itself stays on the task and returns with the
 * module; only what is drawn changes.
 */
export function useVisibleLink(link: LinkValue | null): LinkValue | null {
  const dealsOn = useModules().on('deals')
  return link?.kind === 'deal' && !dealsOn ? null : link
}

export interface Assignee {
  id: string
  name: string
}

/**
 * The people a task can be given to, and how to name whoever it already is.
 *
 * `GET /api/users` is the administrators' (`UserService.list`), so for anyone else the
 * list is the person themselves and a colleague is named «Un collega»: the API accepts
 * any active user as an assignee, but the web has no directory of the space to offer a
 * collaborator and does not invent one here.
 */
export function useAssignees() {
  const { user } = useAuth()
  const admin = useIsAdmin()
  const users = useUsers({ enabled: admin })

  const options = useMemo<Assignee[]>(() => {
    if (admin) {
      return (users.data ?? []).filter((item) => item.active).map(({ id, name: name }) => ({ id, name }))
    }
    return user ? [{ id: user.id, name: user.name }] : []
  }, [admin, users.data, user])

  const nameOf = useCallback(
    (id: string | null): string | null => {
      if (id === null) return null
      const known = (admin ? users.data : undefined)?.find((item) => item.id === id)
      if (known) return known.name
      return user?.id === id ? user.name : 'Un collega'
    },
    [admin, users.data, user],
  )

  return { options, nameOf, me: user?.id ?? null }
}

/** The filters of `GET /api/activities` a screen sends; every one is optional. */
export interface TaskFilters {
  status?: Task['status']
  assigned_to?: string
  include_unassigned?: boolean
  customer_id?: string
  person_id?: string
  deal_id?: string
  invoice_id?: string
  due_within?: string
  without_due_date?: boolean
  archived?: boolean
  sort?: 'created_at' | 'due_date'
  dir?: 'asc' | 'desc'
  limit?: number
}

/**
 * One page of the tasks list, under the `queryKeys.activitiesAll` prefix every task write
 * invalidates (`invalidateAfterWrite`): a task created from the calendar shows on the
 * record's tab and on the page without anybody refetching by hand.
 */
export function useTaskList(filters: TaskFilters, options: { enabled?: boolean } = {}) {
  return useQuery({
    enabled: options.enabled ?? true,
    queryKey: [...queryKeys.activitiesAll, 'list', filters] as const,
    queryFn: () => unwrap(api.GET('/api/activities', { params: { query: filters } })),
  })
}

/**
 * The same list walked page by page, for the screen that is the list itself. A fresh
 * `filters` object starts a fresh walk at the first page (it is part of the key), so a
 * chip pressed halfway down never resumes a cursor issued for another query.
 */
export function useTaskPages(filters: TaskFilters) {
  const query = useInfiniteQuery({
    queryKey: [...queryKeys.activitiesAll, 'pages', filters] as const,
    queryFn: ({ pageParam }) =>
      unwrap(api.GET('/api/activities', { params: { query: { ...filters, cursor: pageParam } } })),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  })
  // Memoised on the page array's reference: a fresh `items` on every render would
  // rebuild the table's row model for renders the fetched pages had no part in.
  const items = useMemo(
    () => query.data?.pages.flatMap((page) => page.items) ?? [],
    [query.data?.pages],
  )
  return {
    items,
    isLoading: query.isLoading,
    isError: query.isError,
    error: query.error,
    hasMore: query.hasNextPage,
    isFetchingMore: query.isFetchingNextPage,
    loadMore: () => void query.fetchNextPage(),
  }
}

export type TaskToday = components['schemas']['TaskToday']

/**
 * The four sections of the Oggi panel, cut by the server on the emitter's own «oggi» so
 * the sidebar count and the page can never disagree with the digest about what is late.
 * Under the `queryKeys.activitiesAll` prefix every task write invalidates: completing the last
 * overdue one empties the section and the sidebar count in the same refetch.
 *
 * `assigned_to` undefined is the whole space; `enabled` is how a caller waits for the
 * session's user id before asking for «mine», rather than asking for everybody's first.
 */
export function useTaskToday(
  params: { assigned_to?: string; include_unassigned?: boolean; limit?: number },
  options: { enabled?: boolean } = {},
) {
  return useQuery({
    enabled: options.enabled ?? true,
    queryKey: [...queryKeys.activitiesAll, 'today', params] as const,
    queryFn: () => unwrap(api.GET('/api/activities/today', { params: { query: params } })),
  })
}

/** How many open tasks assigned to the signed-in user are past their deadline: the
 *  number the sidebar shows next to «Attività». `undefined` until it is known or when there
 *  is nothing to count, so the badge is drawn only for a real figure. */
export function useOverdueCount(): number | undefined {
  const { user } = useAuth()
  const query = useTaskToday(
    { assigned_to: user?.id, include_unassigned: true, limit: 1 },
    { enabled: Boolean(user?.id) },
  )
  const total = query.data?.overdue.total
  return total ? total : undefined
}

/** One tasks by id, for a screen that arrives holding only the id (the global search). */
export function useTask(id: string | undefined) {
  return useQuery({
    // An absent id must never produce a request (residuo B1).
    enabled: id !== undefined && id !== '',
    queryKey: [...queryKeys.activitiesAll, 'one', id] as const,
    queryFn: () =>
      unwrap(
        api.GET('/api/activities/{task_id}', { params: { path: { task_id: id ?? '' } } }),
      ),
  })
}
