import { QueryClient } from '@tanstack/react-query'

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      // A 401 means the session expired; retrying just delays the redirect. A 403
      // means the actor's role will not change mid-request either. Both are terminal
      // for this request -- everything else gets the library's usual couple of
      // retries for a transient blip.
      retry: (failureCount, error) => {
        const status = (error as { status?: number }).status
        if (status === 401 || status === 403) return false
        return failureCount < 2
      },
    },
  },
})

export const queryKeys = {
  me: ['me'] as const,
  customers: (params?: unknown) => ['customers', params ?? {}] as const,
  customer: (id: string) => ['customer', id] as const,
  people: (params?: unknown) => ['people', params ?? {}] as const,
  person: (id: string) => ['person', id] as const,
  deals: (params?: unknown) => ['deals', params ?? {}] as const,
  deal: (id: string) => ['deal', id] as const,
  timeline: (entity: string, id: string) => ['timeline', entity, id] as const,
  fields: (entityType: string) => ['field-definitions', entityType] as const,
  schema: (entityType: string) => ['schema', entityType] as const,
  stages: ['pipeline-stages'] as const,
  users: ['users'] as const,
  // Scoped by user id, not a bare `['tokens']`: `GET /api/tokens` already
  // scopes the *response* to the caller (`PatService.list` filters on
  // `actor.id`), and `logout()`'s `queryClient.clear()` already wipes this
  // cache before another user's session could read it either way -- but a
  // per-user key is what makes a stale cross-user read impossible by
  // construction, rather than merely unreproduced today.
  tokens: (userId: string) => ['tokens', userId] as const,
  // `owner` is the discriminated `{customerId} | {dealId}` object, so a customer's
  // documents and a deal's documents can never share a cache entry, and
  // `invalidateQueries({queryKey: ['documents']})` still matches both.
  documents: (owner?: unknown) => ['documents', owner ?? {}] as const,
  document: (id: string) => ['document', id] as const,
  documentVersions: (id: string) => ['document-versions', id] as const,
  templates: () => ['templates'] as const,
  templateDescription: (id: string) => ['template-description', id] as const,
  emitter: ['emitter'] as const,
  invoices: (params?: unknown) => ['invoices', params ?? {}] as const,
  // Distinct from `invoices` above: that key holds one `InvoicePage`, this one holds
  // an `useInfiniteQuery`'s own `{pages, pageParams}` shape (`useInvoicesPaged`,
  // REB-231). A caller reading one cache entry under the other's key would misread
  // `data.items` as `data.pages` or vice versa with no type error to catch it, so the
  // two never share a key even where their filters happen to coincide.
  invoicesList: (params?: unknown) => ['invoices-list', params ?? {}] as const,
  invoice: (id: string) => ['invoice', id] as const,
  invoiceLines: (id: string) => ['invoice-lines', id] as const,
  // The document id is in the key for the null -> id transition (a proforma's first
  // render); a regeneration is a new *version* under the same id, so it is reached by
  // invalidating the prefix `invoicePdfs(id)` instead (`useInvoiceInvalidation`).
  invoicePdfs: (id: string) => ['invoice-pdf', id] as const,
  invoicePdf: (id: string, documentId: string) => ['invoice-pdf', id, documentId] as const,
  fiscalProfile: ['fiscal-profile'] as const,
  timeEntries: (params?: unknown) => ['time-entries', params ?? {}] as const,
  // One key per month, and the prefix `['calendario']` on purpose: a commitment whose
  // date moved is stale in two months at once, so every write invalidates the prefix
  // rather than the month the page happens to be showing.
  calendarMonth: (mese: string) => ['calendario', mese] as const,
  attivita: (params?: unknown) => ['attivita', params ?? {}] as const,
  /** The caller's own running timer -- one row or null, so one key with no argument. */
  timer: ['timer'] as const,
  timeEntry: (id: string) => ['time-entry', id] as const,
  dealTimeSummary: (dealId: string) => ['deal-time-summary', dealId] as const,
  // Keyed by both ids: the answer depends on the deal's rate *and* the user's default,
  // so a single-id key would serve one user's rate for another's.
  dealRates: (dealId: string, userId: string) => ['deal-rates', dealId, userId] as const,
  costs: (params?: unknown) => ['costs', params ?? {}] as const,
  costCategories: (includeArchived: boolean) => ['cost-categories', includeArchived] as const,
  periodLocks: (anno?: number) => ['period-locks', anno ?? null] as const,
  dealPnl: (dealId: string) => ['deal-pnl', dealId] as const,
  // Keyed on the whole query object, window included: the same customer read over two
  // different periods is two different answers, and a key that dropped the dates would
  // serve January's report for December's.
  periodPnl: (params?: unknown) => ['period-pnl', params ?? {}] as const,
  fiscalEstimate: (anno: number) => ['fiscal-estimate', anno] as const,
  // The term is part of the key so an in-flight response for "ross" cannot overwrite the
  // rendering of "rossi": TanStack Query discards the stale entry rather than the
  // component having to compare what came back with what was typed.
  search: (term: string) => ['search', term] as const,
  // The period is part of the key, so switching period is a different cache entry rather
  // than a refetch that briefly shows March's numbers under April's heading. Every
  // dashboard key starts with the literal 'dashboard' so a mutation that cannot know which
  // period is on screen can invalidate all of them by prefix.
  dashboard: (kind: 'commerciale' | 'economica' | 'panoramica', params: Record<string, string>) =>
    ['dashboard', kind, params] as const,
  automations: () => ['automations'] as const,
}
