import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, toProblem, unwrap } from '@/lib/api'
import { useIsAdmin } from '@/lib/auth'
import { useLegalEntity } from '@/lib/legalEntity'
import { queryKeys, retryUnlessMissing } from '@/lib/query'
import { PAGE_SIZE, type BuildRequest, type ExportKind } from './labels'

/**
 * Every read of «Esporta per il commercialista». All of them share one root key, so a build
 * refreshes the list, its count and an open panel in one `invalidateQueries`.
 */
const ROOT = ['accountant-export'] as const

export interface ExportFilter {
  /** The company the exports are of, or `null` for every one the reader may see. */
  legalEntityId: string | null
  year: number | null
}

/** One page of the list at a time, newest first, `PAGE_SIZE` rows to a request. */
export function useExportList(filter: ExportFilter) {
  // Asked whatever the role: a collaborator on a stale link gets the server's own refusal, which
  // the page shows, instead of a wait that never ends.
  return useInfiniteQuery({
    queryKey: [...ROOT, 'list', filter.legalEntityId, filter.year] as const,
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET('/api/accountant-export/exports', {
          params: {
            query: {
              legal_entity_id: filter.legalEntityId ?? undefined,
              year: filter.year ?? undefined,
              offset: pageParam,
              limit: PAGE_SIZE,
            },
          },
        }),
      ),
    getNextPageParam: (last, pages) => {
      const loaded = pages.reduce((sum, page) => sum + page.items.length, 0)
      return loaded < last.total_count && last.items.length > 0 ? loaded : undefined
    },
  })
}

/** How many exports there are in all, which the tab wears. A one-row page reads only the count. */
export function useExportTotal() {
  return useQuery({
    queryKey: [...ROOT, 'total'] as const,
    queryFn: async () =>
      (
        await unwrap(
          api.GET('/api/accountant-export/exports', { params: { query: { limit: 1 } } }),
        )
      ).total_count,
  })
}

/** One export, read on its own so a link to one that is not on the first page still opens. */
export function useExport(id: string) {
  return useQuery({
    // A link to an export that is not there is an answer, not a blip to retry through.
    retry: retryUnlessMissing,
    queryKey: [...ROOT, 'one', id] as const,
    queryFn: () =>
      unwrap(
        api.GET('/api/accountant-export/exports/{export_id}', {
          params: { path: { export_id: id } },
        }),
      ),
  })
}

/** The parts a build for this company would hold, from the server: the page keeps no list. A part
 *  the admin asks for (the payslips) comes with `opt_in`, the name to ask it by. */
export function useExportParts(
  legalEntityId: string | null,
  allEntities: boolean,
  enabled: boolean,
  kind: ExportKind = 'accountant',
) {
  return useQuery({
    enabled,
    queryKey: [...ROOT, 'parts', legalEntityId, allEntities, kind] as const,
    queryFn: () =>
      unwrap(
        api.GET('/api/accountant-export/parts', {
          params: {
            query: {
              legal_entity_id: legalEntityId ?? undefined,
              all_entities: allEntities ? true : undefined,
              kind,
            },
          },
        }),
      ),
  })
}

/**
 * «Il periodo è pronto?»: the checklist of a period as the books stand now, from the server. It is
 * read again for every company, period and pay part chosen and never kept past that: the page
 * computes nothing, and the build computes it once more and keeps that with the export.
 */
export function useExportChecklist(
  request: {
    date_from: string
    date_to: string
    legal_entity_id: string | null
    all_entities: boolean
    payroll_parts: readonly string[]
  } | null,
) {
  return useQuery({
    enabled: request !== null,
    queryKey: [...ROOT, 'checklist', request] as const,
    queryFn: () =>
      unwrap(
        api.GET('/api/accountant-export/checklist', {
          params: {
            query: {
              date_from: request?.date_from ?? '',
              date_to: request?.date_to ?? '',
              legal_entity_id: request?.legal_entity_id ?? undefined,
              all_entities: request?.all_entities ? true : undefined,
              payroll_parts: request?.payroll_parts.length ? [...request.payroll_parts] : undefined,
            },
          },
        }),
      ),
    // A period read stays right for the moment it was asked; a returning window asks again.
    staleTime: 0,
  })
}

/**
 * How often the company settles its IVA, which decides the period the form opens on: `null` for a
 * company with no fiscal profile (a 404 is an answer, and the form opens on the last month). The
 * key and the read are the ones `useFiscalProfile` of the invoices uses, so the profile is asked
 * once whichever page reaches it first.
 */
export function useIvaFrequency(legalEntityId: string | null) {
  return useQuery({
    enabled: legalEntityId !== null,
    queryKey: queryKeys.fiscalProfile(legalEntityId ?? ''),
    retry: retryUnlessMissing,
    queryFn: async () => {
      const { data, error, response } = await api.GET(
        '/api/legal-entities/{legal_entity_id}/fiscal-profile',
        { params: { path: { legal_entity_id: legalEntityId ?? '' } } },
      )
      if (response.status === 404) return null
      if (error !== undefined) throw toProblem(error, response.status)
      return data ?? null
    },
    select: (profile) => profile?.iva_frequency ?? null,
  })
}

/**
 * Builds and stores an export. The request returns when the files are written, which may take a
 * while for a year of PDFs; the invalidation is the mutation's own so that it still runs when the
 * admin has left the page, and the list shows the export when they come back.
 */
export function useBuildExport() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (request: BuildRequest) =>
      unwrap(
        api.POST('/api/accountant-export/exports', {
          body: {
            date_from: request.date_from,
            date_to: request.date_to,
            legal_entity_id: request.legal_entity_id,
            all_entities: request.all_entities,
            kind: request.kind,
            payroll_parts: request.payroll_parts,
          },
        }),
      ),
    // On a failure too: a request that dropped after the server stored the export would otherwise
    // leave the list without it.
    onSettled: () => queryClient.invalidateQueries({ queryKey: ROOT }),
  })
}

/** Who built an export, as a name for a column and as the words «Creata da …» takes. */
export interface Builder {
  name: string
  by: string
}

/** The people who may have built an export, by id; an admin reads the space's users. */
export function useBuilderNames() {
  const admin = useIsAdmin()
  const users = useQuery({
    enabled: admin,
    queryKey: queryKeys.users,
    queryFn: () => unwrap(api.GET('/api/users')),
  })
  return (id: string | null, me: string | null): Builder => {
    if (id === null) return { name: 'Lo spazio', by: 'dallo spazio' }
    if (id === me) return { name: 'Tu', by: 'te' }
    const name = users.data?.find((user) => user.id === id)?.name
    return name === undefined ? { name: 'Un collega', by: 'un collega' } : { name, by: name }
  }
}

/** How an export or a link is named wherever it is listed: its company, or «Tutte le aziende». */
export function useCompanyName() {
  const { byId } = useLegalEntity()
  return (item: { scope: string; legal_entity_id: string | null }): string =>
    item.scope === 'all_entities'
      ? 'Tutte le aziende'
      : (byId(item.legal_entity_id)?.name ?? 'Azienda non più attiva')
}

/** The links of one export, newest first. */
export function useExportLinks(exportId: string) {
  return useQuery({
    queryKey: [...ROOT, 'links', 'of', exportId] as const,
    queryFn: () =>
      unwrap(
        api.GET('/api/accountant-export/share-links', {
          params: { query: { export_id: exportId, limit: 200 } },
        }),
      ),
    select: (page) => page.items,
  })
}

/** Every link of the space, 50 at a time, newest first: the tab «Link condivisi». */
export function useAllLinks() {
  return useInfiniteQuery({
    queryKey: [...ROOT, 'links', 'all'] as const,
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET('/api/accountant-export/share-links', {
          params: { query: { offset: pageParam, limit: PAGE_SIZE } },
        }),
      ),
    getNextPageParam: (last, pages) => {
      const loaded = pages.reduce((sum, page) => sum + page.items.length, 0)
      return loaded < last.total_count && last.items.length > 0 ? loaded : undefined
    },
  })
}

/** One link with its latest openings: when, which file and from which address. */
export function useLink(id: string) {
  return useQuery({
    retry: retryUnlessMissing,
    queryKey: [...ROOT, 'links', 'one', id] as const,
    queryFn: () =>
      unwrap(
        api.GET('/api/accountant-export/share-links/{link_id}', {
          params: { path: { link_id: id } },
        }),
      ),
  })
}

/** Makes a link for one export. The address comes back once, in this answer, and the dialog that
 *  asked is the only place it is held; the lists refresh so the new link is in them. */
export function useCreateLink(exportId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    // `recipient_email` makes a link with a one-time code: an export with pay has no other kind.
    mutationFn: (link: { days: number; recipient_email?: string }) =>
      unwrap(
        api.POST('/api/accountant-export/exports/{export_id}/share-links', {
          params: { path: { export_id: exportId } },
          body: { days: link.days, recipient_email: link.recipient_email ?? null },
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [...ROOT, 'links'] }),
  })
}

/** Ends a link at once, with no confirmation: the page says so in a toast. */
export function useRevokeLink() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) =>
      unwrap(
        api.POST('/api/accountant-export/share-links/{link_id}/revoke', {
          params: { path: { link_id: id } },
        }),
      ),
    onSettled: () => queryClient.invalidateQueries({ queryKey: [...ROOT, 'links'] }),
  })
}

/** Whether an export can be written to the space's Drive: the page draws the button only then. */
export function useDriveStatus() {
  return useQuery({
    queryKey: [...ROOT, 'drive'] as const,
    queryFn: () => unwrap(api.GET('/api/accountant-export/drive')),
  })
}

/** Where an export was written to Drive, newest first. */
export function useDriveWrites(exportId: string) {
  return useQuery({
    queryKey: [...ROOT, 'drive-writes', exportId] as const,
    queryFn: () =>
      unwrap(
        api.GET('/api/accountant-export/exports/{export_id}/drive-writes', {
          params: { path: { export_id: exportId } },
        }),
      ),
  })
}

/** Copies an export to the space's Drive. A person's act: the button is the only way. */
export function useWriteToDrive(exportId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () =>
      unwrap(
        api.POST('/api/accountant-export/exports/{export_id}/drive-writes', {
          params: { path: { export_id: exportId } },
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [...ROOT, 'drive-writes'] }),
  })
}
