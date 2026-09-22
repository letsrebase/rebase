import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo } from 'react'
import { api, fetchWithRefresh, toProblem, unwrap } from '@/lib/api'
import type { StatusTone } from '@/components/StatusPill'
import type { components } from '@/lib/api-types'
import { queryKeys } from '@/lib/query'

export type Invoice = components['schemas']['InvoiceRead']
export type InvoiceLine = components['schemas']['InvoiceLineRead']
export type InvoiceLineInput = components['schemas']['InvoiceLineIn']
export type InvoicePage = components['schemas']['InvoicePage']
export type InvoiceArtifact = components['schemas']['InvoiceArtifact']
export type FiscalProfile = components['schemas']['FiscalProfileRead']
export type Activity = components['schemas']['ActivityRead']

export type InvoiceTipo = 'fattura' | 'proforma'
export type InvoiceStato = 'bozza' | 'emessa' | 'annullata' | 'confermata' | 'consumata'
export type StatoPagamento = 'da_incassare' | 'incassato'

/**
 * A customer or a deal, following the same discriminated shape
 * `features/documents/queries.ts`'s `DocumentOwner` already established: there is no
 * "empty id" spelling to get wrong (B1), and a call site cannot ask for both.
 */
export type InvoiceOwner = { customerId: string } | { dealId: string }

export interface InvoiceFilters {
  customer_id?: string
  deal_id?: string
  tipo?: InvoiceTipo
  stato?: InvoiceStato
  anno?: number
  stato_pagamento?: StatoPagamento
  /**
   * The "scaduto e non incassato" drill-through of the operational dashboard (slice 6
   * §6.2). Server-side, and not a filter applied to the fetched page here, because the
   * card's count and this list share one predicate in `InvoiceRepository`
   * (`_overdue_predicate`): a second version of "overdue" written in the browser would
   * disagree with the count beside it on exactly the boundary cases -- an invoice due
   * today, one already collected -- that the predicate exists to settle.
   */
  scadute?: boolean
  /**
   * The fattura a consumed proforma was issued as. The fattura carries
   * `origine_proforma_id` and the proforma carries nothing, so the proforma's page asks
   * the list for the one row that points at it (ORB-134).
   */
  origine_proforma_id?: string
  /**
   * Leave out the proformas that became a fattura. Under «Tutte» the list otherwise
   * shows each issued proforma twice, once as itself pilled «Consumata» and once as its
   * number (ORB-169). Sent whenever no `stato` is chosen; the «Consumata» chip asks by
   * state and gets them. Server-side, like `scadute`, so the page and its cursor are
   * the API's and not a filtered copy.
   */
  escludi_consumate?: boolean
  limit?: number
  cursor?: string
}

export const INVOICE_TYPE_LABELS: Record<InvoiceTipo, string> = {
  fattura: 'Fattura',
  proforma: 'Proforma',
}

export const INVOICE_STATE_LABELS: Record<InvoiceStato, string> = {
  bozza: 'Bozza',
  emessa: 'Emessa',
  annullata: 'Annullata',
  confermata: 'Confermata',
  consumata: 'Consumata',
}

export const PAYMENT_STATE_LABELS: Record<StatoPagamento, string> = {
  da_incassare: 'Da incassare',
  incassato: 'Incassato',
}

/**
 * The tone each fiscal state reads as in a `StatusPill` (design spec §4). Next to the
 * labels, and a total `Record`, so a state added to `InvoiceStato` fails to compile here
 * rather than rendering as a default nobody chose. Note what that does and does not buy:
 * `InvoiceStato` is a union written by hand a few lines above, not a generated type, so
 * the compile breaks when somebody widens *it* -- adding a state to `STATO_TRANSITIONS`
 * on the server does not by itself reach this file.
 *
 * `emessa` and `confermata` are settled: a number is assigned, or a proforma is ready to
 * become one, and neither is a problem to be looked at. `bozza` and `consumata` are
 * quiet because neither claims anything about money -- a draft is not a document and a
 * consumed proforma has already become the invoice beside it. `annullata` is the one
 * state that has to read as a warning: an annulled invoice keeps its number and stays in
 * the register, so a row that looked ordinary would be read as a live document.
 */
export const INVOICE_STATE_TONE: Record<InvoiceStato, StatusTone> = {
  bozza: 'muted',
  confermata: 'ink',
  emessa: 'ink',
  consumata: 'muted',
  annullata: 'danger',
}

/**
 * Collection is the one place gold is right: «Da incassare» is not an error and not a
 * finished state -- it is money waiting on somebody, which is exactly what Royal Gold
 * says everywhere else in this product. Overdue is not a state of its own on the server
 * (it is `da_incassare` plus a date in the past, the `scadute` filter); the day the list
 * renders it as its own pill it reads `danger`, per the design spec.
 */
export const PAYMENT_STATE_TONE: Record<StatoPagamento, StatusTone> = {
  da_incassare: 'gold',
  incassato: 'ink',
}

/**
 * Which buttons a row can offer, mirroring `STATO_TRANSITIONS` in
 * `packages/core/src/pigrocrm/core/invoices/schemas.py`.
 *
 * A mirror for rendering only: the server validates every transition and its 409
 * message is what gets shown. Same convention as `OFFER_TRANSITIONS` in
 * `features/documents/queries.ts`.
 */
export const INVOICE_TRANSITIONS: Record<InvoiceStato, InvoiceStato[]> = {
  bozza: ['emessa', 'confermata'],
  confermata: ['consumata'],
  emessa: ['annullata'],
  annullata: [],
  consumata: [],
}

function ownerQuery(owner: InvoiceOwner): { customer_id?: string; deal_id?: string } {
  return 'customerId' in owner ? { customer_id: owner.customerId } : { deal_id: owner.dealId }
}

export function useInvoices(filters: InvoiceFilters = {}) {
  return useQuery({
    queryKey: queryKeys.invoices(filters),
    queryFn: () => unwrap(api.GET('/api/invoices', { params: { query: filters } })),
  })
}

export type InvoicesListFilters = Omit<InvoiceFilters, 'limit' | 'cursor'>

export interface InvoicesListResult {
  items: Invoice[]
  isLoading: boolean
  isError: boolean
  error: unknown
  /** True once the last loaded page's own `next_cursor` says there is more beyond it. */
  hasMore: boolean
  isFetchingMore: boolean
  /** True if the *next* page's own fetch failed; the pages already shown are
   *  unaffected, `error` carries the failure, and `loadMore` can simply be tried
   *  again. */
  loadMoreError: boolean
  loadMore: () => void
}

/**
 * Walks `next_cursor` one page at a time, the way `fetchAllDeals` in
 * `features/deals/queries.ts` walks it -- except that hook exhausts the cursor eagerly
 * because the Kanban needs every deal for its column totals, and this one stops after
 * each page because the invoice list is a fiscal register a person reads, not a total
 * to compute (REB-231): the caller decides when to fetch the next fifty with
 * `loadMore`, and that is the "Carica altre" button `InvoicesList` and `InvoicesTab`
 * both show next to a truncated page.
 *
 * `filters` excludes `limit`/`cursor`: both are `useInfiniteQuery`'s own job here, so a
 * state chip or the type select changing `filters` starts a fresh walk at the first
 * page instead of resuming a stale cursor against a different query.
 */
function useInvoicesPaged(filters: InvoicesListFilters): InvoicesListResult {
  const query = useInfiniteQuery({
    queryKey: queryKeys.invoicesList(filters),
    queryFn: ({ pageParam }) =>
      unwrap(api.GET('/api/invoices', { params: { query: { ...filters, cursor: pageParam } } })),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage: InvoicePage) => lastPage.next_cursor ?? undefined,
  })
  // Memoized on the page array's own reference, not recomputed on every render: a
  // fresh `items` array on each render would defeat `DataTable`'s row-model memo
  // (keyed on `data`'s identity), rebuilding every loaded row on a re-render the
  // fetched pages had no part in -- opening the type `Select`, pressing a chip.
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
    loadMoreError: query.isFetchNextPageError,
    loadMore: () => void query.fetchNextPage(),
  }
}

/**
 * The invoice list page's own data. Every filter it takes is server-side (`tipo`,
 * `stato`, `escludi_consumate`, `scadute`), so a chip or the type select changing
 * mid-walk asks the server for a fresh first page rather than filtering the rows
 * already on screen -- the state chips and the type select keep working across
 * however many pages are loaded (REB-231).
 */
export function useInvoicesList(filters: InvoicesListFilters = {}): InvoicesListResult {
  return useInvoicesPaged(filters)
}

/**
 * The invoices belonging to a customer or a deal, for the "Fatture" tab on either
 * entity's detail page. Shares `useInvoicesPaged` with the plain list page, so both
 * walk the cursor the same way and land in the same cache entry once their filters
 * match.
 */
export function useInvoicesForOwner(owner: InvoiceOwner): InvoicesListResult {
  // The tab has no state chips, so it never asks for the consumed ones by state: the
  // fattura stands for its proforma here as it does on the list page (ORB-169).
  return useInvoicesPaged({ ...ownerQuery(owner), escludi_consumate: true })
}

export function useInvoice(invoiceId: string) {
  return useQuery({
    // B1: an empty id must not produce a request at all. `useCustomer('')` once
    // redirected to the list endpoint with an absolute URL, bypassing the Vite proxy.
    enabled: invoiceId !== '',
    queryKey: queryKeys.invoice(invoiceId),
    queryFn: () =>
      unwrap(
        api.GET('/api/invoices/{invoice_id}', { params: { path: { invoice_id: invoiceId } } }),
      ),
  })
}

export function useInvoiceLines(invoiceId: string) {
  return useQuery({
    enabled: invoiceId !== '',
    queryKey: queryKeys.invoiceLines(invoiceId),
    queryFn: () =>
      unwrap(
        api.GET('/api/invoices/{invoice_id}/lines', {
          params: { path: { invoice_id: invoiceId } },
        }),
      ),
  })
}

export function useInvoiceTimeline(invoiceId: string) {
  return useQuery({
    enabled: invoiceId !== '',
    queryKey: queryKeys.timeline('invoice', invoiceId),
    queryFn: () =>
      unwrap(
        api.GET('/api/invoices/{invoice_id}/timeline', {
          params: { path: { invoice_id: invoiceId } },
        }),
      ),
  })
}

export function useFiscalProfile() {
  return useQuery({
    queryKey: queryKeys.fiscalProfile,
    queryFn: async (): Promise<FiscalProfile | null> => {
      // A 404 here means "not configured yet", which is a legitimate state and not an
      // error -- the same treatment `useEmitter` in features/settings/queries.ts gives
      // its own singleton row.
      const { data, error, response } = await api.GET('/api/fiscal-profile')
      if (response.status === 404) return null
      if (error !== undefined) throw toProblem(error, response.status)
      return data ?? null
    },
  })
}

function useInvoiceInvalidation() {
  const queryClient = useQueryClient()
  return (invoiceId?: string) => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.invoices() })
    // `invoicesList` is a distinct key (see `lib/query.ts`), so a new or changed
    // invoice needs its own invalidation to reach `InvoicesList`/`InvoicesTab`.
    void queryClient.invalidateQueries({ queryKey: ['invoices-list'] })
    if (invoiceId !== undefined) {
      void queryClient.invalidateQueries({ queryKey: queryKeys.invoice(invoiceId) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.invoiceLines(invoiceId) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.timeline('invoice', invoiceId) })
      // Prefix match: «Rigenera documenti» and emission store a new version under the
      // same `pdf_document_id`, so the preview's key does not change on its own.
      void queryClient.invalidateQueries({ queryKey: queryKeys.invoicePdfs(invoiceId) })
    }
  }
}

/**
 * The invoice's PDF as a `Blob`, for the preview beside the numbers (ORB-30). Through
 * `fetchWithRefresh` like `downloadInvoiceArtifact` below, for the same three reasons:
 * cookie, tenant prefix, one refresh on 401. The Blob is what is cached, never an
 * object URL: a URL's life is the component's (it is revoked on unmount), a Blob's is
 * the cache's, and confusing the two hands a revoked URL to the next mount.
 */
export function useInvoicePdf(invoice: Invoice) {
  const documentId = invoice.pdf_document_id
  return useQuery({
    queryKey: queryKeys.invoicePdf(invoice.id, documentId ?? ''),
    enabled: documentId !== null,
    queryFn: async (): Promise<Blob> => {
      const response = await fetchWithRefresh(`/api/invoices/${invoice.id}/pdf`)
      if (!response.ok) {
        const payload: unknown = await response.json().catch(() => null)
        throw toProblem(payload, response.status)
      }
      return response.blob()
    },
  })
}

export function useCreateInvoice() {
  const invalidate = useInvoiceInvalidation()
  return useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      unwrap(api.POST('/api/invoices', { body: body as never })),
    onSuccess: (invoice) => invalidate(invoice.id),
  })
}

export function useUpdateInvoice(invoiceId: string) {
  const invalidate = useInvoiceInvalidation()
  return useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      unwrap(
        api.PATCH('/api/invoices/{invoice_id}', {
          params: { path: { invoice_id: invoiceId } },
          body: body as never,
        }),
      ),
    onSuccess: () => invalidate(invoiceId),
  })
}

export function useReplaceLines(invoiceId: string) {
  const invalidate = useInvoiceInvalidation()
  return useMutation({
    mutationFn: (righe: InvoiceLineInput[]) =>
      unwrap(
        api.PUT('/api/invoices/{invoice_id}/lines', {
          params: { path: { invoice_id: invoiceId } },
          body: { righe },
        }),
      ),
    onSuccess: () => invalidate(invoiceId),
  })
}

export function useConfirmProforma(invoiceId: string) {
  const invalidate = useInvoiceInvalidation()
  return useMutation({
    mutationFn: () =>
      unwrap(
        api.POST('/api/invoices/{invoice_id}/confirm', {
          params: { path: { invoice_id: invoiceId } },
        }),
      ),
    onSuccess: () => invalidate(invoiceId),
  })
}

export function useIssueInvoice(invoiceId: string) {
  const invalidate = useInvoiceInvalidation()
  return useMutation({
    mutationFn: (body: { data_emissione?: string | null }) =>
      unwrap(
        api.POST('/api/invoices/{invoice_id}/issue', {
          params: { path: { invoice_id: invoiceId } },
          body,
        }),
      ),
    onSuccess: (issued) => {
      // The issued row may be a *different* row when the source was a proforma (which
      // becomes `consumata` and the new fattura is a separate row), so both ids are
      // invalidated.
      invalidate(invoiceId)
      invalidate(issued.id)
    },
  })
}

export function useAnnulInvoice(invoiceId: string) {
  const invalidate = useInvoiceInvalidation()
  return useMutation({
    mutationFn: (motivo: string) =>
      unwrap(
        api.POST('/api/invoices/{invoice_id}/annul', {
          params: { path: { invoice_id: invoiceId } },
          body: { motivo },
        }),
      ),
    onSuccess: () => invalidate(invoiceId),
  })
}

export function useMarkTransmitted(invoiceId: string) {
  const invalidate = useInvoiceInvalidation()
  return useMutation({
    mutationFn: (data: string) =>
      unwrap(
        api.POST('/api/invoices/{invoice_id}/transmitted', {
          params: { path: { invoice_id: invoiceId } },
          body: { data },
        }),
      ),
    onSuccess: () => invalidate(invoiceId),
  })
}

export function useSetPaymentState(invoiceId: string) {
  const invalidate = useInvoiceInvalidation()
  return useMutation({
    mutationFn: (body: { stato_pagamento: StatoPagamento; data_incasso?: string | null }) =>
      unwrap(
        api.PATCH('/api/invoices/{invoice_id}/payment', {
          params: { path: { invoice_id: invoiceId } },
          body,
        }),
      ),
    onSuccess: () => invalidate(invoiceId),
  })
}

/**
 * `invoiceId` is the row the bar sits on; `mutate(targetId)` names another one. The
 * one caller that does is «Emetti» on a proforma, whose issued row is a *different*
 * row (spec 5): rendering the proforma there would produce the proforma's PDF and
 * leave the fattura, the document the person came for, unprinted (ORB-134).
 */
export function useProduceArtifacts(invoiceId: string) {
  const invalidate = useInvoiceInvalidation()
  return useMutation({
    mutationFn: (targetId?: string) =>
      unwrap(
        api.POST('/api/invoices/{invoice_id}/artifacts', {
          params: { path: { invoice_id: targetId ?? invoiceId } },
        }),
      ),
    onSuccess: (_data, targetId) => {
      invalidate(invoiceId)
      if (targetId) invalidate(targetId)
    },
  })
}

export function useDeleteInvoice() {
  const invalidate = useInvoiceInvalidation()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (invoiceId: string) =>
      unwrap(
        api.DELETE('/api/invoices/{invoice_id}', {
          params: { path: { invoice_id: invoiceId } },
        }),
      ),
    // The row is gone: its detail, lines and timeline are *removed* from the cache, not
    // invalidated, because a refetch would ask the server for a row it now refuses and
    // paint a 404 on a page that is about to leave. Browser Back within `staleTime`
    // would otherwise show the deleted draft as if it existed. The list is invalidated.
    onSuccess: (_, invoiceId) => {
      invalidate()
      queryClient.removeQueries({ queryKey: queryKeys.invoice(invoiceId) })
      queryClient.removeQueries({ queryKey: queryKeys.invoiceLines(invoiceId) })
      queryClient.removeQueries({ queryKey: queryKeys.timeline('invoice', invoiceId) })
    },
    // A refusal here means the state changed under the person (issued or consumed by
    // someone else meanwhile): refetch the row so the page corrects itself while the
    // banner explains why.
    onError: (_, invoiceId) => invalidate(invoiceId),
  })
}

export function useSaveFiscalProfile() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      unwrap(api.PUT('/api/fiscal-profile', { body: body as never })),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.fiscalProfile })
    },
  })
}

/**
 * A blob download, the one documented exception to "no `fetch` inside components"
 * already established by `downloadDocument` in `features/documents/queries.ts`:
 * `openapi-fetch` cannot express a binary response, and the server's own
 * `Content-Disposition` is what names the file -- which for the XML is the SdI's
 * convention and matters to whoever receives it.
 *
 * Through `fetchWithRefresh`, not a bare `fetch`: being outside the typed client is a
 * statement about response *types*, and it used to silently also mean "and outside the
 * session handling", so a PDF asked for more than fifteen minutes after the last
 * request answered «Autenticazione richiesta». See `lib/api.ts`.
 */
export async function downloadInvoiceArtifact(
  invoiceId: string,
  kind: 'pdf' | 'xml',
): Promise<void> {
  const response = await fetchWithRefresh(`/api/invoices/${invoiceId}/${kind}`)
  if (!response.ok) {
    const payload: unknown = await response.json().catch(() => null)
    throw toProblem(payload, response.status)
  }
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = ''
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}
