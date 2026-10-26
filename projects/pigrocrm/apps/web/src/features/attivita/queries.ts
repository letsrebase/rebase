import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { useCallback, useMemo } from 'react'
import { useCustomer } from '@/features/customers/queries'
import { useDeal } from '@/features/deals/queries'
import { useInvoice } from '@/features/invoices/queries'
import { usePerson } from '@/features/people/queries'
import { useUsers } from '@/features/settings/queries'
import { api, unwrap } from '@/lib/api'
import { useAuth, useIsAdmin } from '@/lib/auth'
import type { Attivita } from '@/features/calendar/queries'
import type { LinkKind, LinkValue } from './links'

/**
 * The name of the record an attività hangs from, read by id.
 *
 * Four reads behind one hook, three of them switched off: a hook cannot be called
 * conditionally, so each is `enabled` only for its own kind and an empty id never reaches
 * the network. The row already holds the id and nothing else, and a link drawn as a bare
 * UUID would be a link nobody could read.
 */
export function useRecordLabel(link: LinkValue | null): string | undefined {
  const is = (kind: LinkKind) => link?.kind === kind
  const id = link?.id ?? ''
  const customer = useCustomer(id, { enabled: is('customer') })
  const person = usePerson(id, { enabled: is('person') })
  const deal = useDeal(id, { enabled: is('deal') })
  const invoice = useInvoice(is('invoice') ? id : '')

  if (link === null) return undefined
  switch (link.kind) {
    case 'customer':
      return customer.data?.ragione_sociale
    case 'person':
      return person.data ? [person.data.nome, person.data.cognome].filter(Boolean).join(' ') : undefined
    case 'deal':
      return deal.data?.nome
    case 'invoice': {
      const data = invoice.data
      if (!data) return undefined
      // The number is assigned at emission: until then the state stands in for it, the
      // same rule the global search's label follows.
      const prefix = data.anno !== null && data.numero !== null ? `${data.anno}/${data.numero}` : data.stato
      return data.causale ? `${prefix} · ${data.causale}` : prefix
    }
  }
}

export interface Assignee {
  id: string
  nome: string
}

/**
 * The people an attività can be given to, and how to name whoever it already is.
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
      return (users.data ?? []).filter((item) => item.attivo).map(({ id, nome }) => ({ id, nome }))
    }
    return user ? [{ id: user.id, nome: user.nome }] : []
  }, [admin, users.data, user])

  const nameOf = useCallback(
    (id: string | null): string | null => {
      if (id === null) return null
      const known = (admin ? users.data : undefined)?.find((item) => item.id === id)
      if (known) return known.nome
      return user?.id === id ? user.nome : 'Un collega'
    },
    [admin, users.data, user],
  )

  return { options, nameOf, me: user?.id ?? null }
}

/** The filters of `GET /api/activities` a screen sends; every one is optional. */
export interface AttivitaFilters {
  stato?: Attivita['stato']
  assegnata_a?: string
  customer_id?: string
  person_id?: string
  deal_id?: string
  invoice_id?: string
  scade_entro?: string
  senza_scadenza?: boolean
  archiviate?: boolean
  sort?: 'created_at' | 'scadenza'
  dir?: 'asc' | 'desc'
  limit?: number
}

/**
 * One page of the attività list, under the `['attivita']` prefix every attività write
 * invalidates (`invalidateAfterWrite`): a task created from the calendar shows on the
 * record's tab and on the page without anybody refetching by hand.
 */
export function useAttivitaList(filters: AttivitaFilters, options: { enabled?: boolean } = {}) {
  return useQuery({
    enabled: options.enabled ?? true,
    queryKey: ['attivita', 'list', filters] as const,
    queryFn: () => unwrap(api.GET('/api/activities', { params: { query: filters } })),
  })
}

/**
 * The same list walked page by page, for the screen that is the list itself. A fresh
 * `filters` object starts a fresh walk at the first page (it is part of the key), so a
 * chip pressed halfway down never resumes a cursor issued for another query.
 */
export function useAttivitaPages(filters: AttivitaFilters) {
  const query = useInfiniteQuery({
    queryKey: ['attivita', 'pages', filters] as const,
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

/** One attività by id, for a screen that arrives holding only the id (the global search). */
export function useAttivita(id: string | undefined) {
  return useQuery({
    // An absent id must never produce a request (residuo B1).
    enabled: id !== undefined && id !== '',
    queryKey: ['attivita', 'one', id] as const,
    queryFn: () =>
      unwrap(
        api.GET('/api/activities/{attivita_id}', { params: { path: { attivita_id: id ?? '' } } }),
      ),
  })
}
