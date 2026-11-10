import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { correspondentKeys } from '@/features/gmail/correspondents'
import { useLegalEntityScope } from '@/lib/legalEntity'
import { api, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { asBody } from '@/lib/formValues'
import { keepWhileTyping } from '@/lib/keepWhileTyping'
import { queryKeys } from '@/lib/query'
import { useCursorPages } from '@/lib/useCursorPages'
import type { RegistryRole } from './search'

/**
 * Wire shape of one customer, taken directly from the generated OpenAPI schema
 * (`components['schemas']['OrganizationRead']`) rather than hand-declared -- the same
 * reasoning as Timeline.tsx's `ActivityEntry` alias: the single source of truth is
 * `OrganizationRead` in packages/core/src/pigrocrm/core/organizations/schemas.py, and this
 * alias tracks it through `pnpm generate:api` automatically. A hand-written
 * interface here would silently drift the day that schema grows a field.
 */
export type Organization = components['schemas']['OrganizationRead']
export type OrganizationPage = components['schemas']['OrganizationPage']

type OrganizationCreateBody = components['schemas']['OrganizationCreate']
type OrganizationUpdateBody = components['schemas']['OrganizationUpdate']

interface OrganizationsListParams {
  search?: string
  limit?: number
  legal_entity_id?: string
  /** One role, or `any` for the registry across roles; customers only when left out. */
  role?: 'customer' | 'supplier' | 'freelancer' | 'any'
  /** A supplier's fiscal kind (`company`, `professional`, ...): the supplier view's «Tipo fiscale». */
  supplier_kind?: string
}

/**
 * `/api/organizations`, never the bare `/organizations`: every router in
 * apps/api/src/pigrocrm_api/routers/*.py declares its own "/api" prefix, and the
 * shared client (lib/api.ts) is built with `baseUrl: ''` to match -- confirmed live,
 * the bare path 404s.
 */
function fetchOrganizationsPage(params: OrganizationsListParams, cursor?: string) {
  return unwrap(
    api.GET('/api/organizations', {
      params: {
        query: {
          search: params.search,
          limit: params.limit,
          legal_entity_id: params.legal_entity_id,
          role: params.role,
          supplier_kind: params.supplier_kind,
          cursor,
        },
      },
    }),
  )
}

/**
 * One page of organizations, for a picker that asks the server for the few that match what
 * was typed (`OrganizationPicker`). The Clienti list itself walks the pages: `useOrganizationPages`.
 */
export function useOrganizations(
  params: OrganizationsListParams = {},
  options: { enabled?: boolean } = {},
) {
  // The sidebar's legal entity, when one is selected (REB-625): on every picker that offers
  // the space's organizations, which under azienda A are A's. A legal entity the caller names is
  // its own scope and wins over the sidebar's.
  const sidebarScoped = useLegalEntityScope(params)
  const scoped = params.legal_entity_id === undefined ? sidebarScoped : params
  return useQuery({
    queryKey: queryKeys.organizations(scoped),
    enabled: options.enabled ?? true,
    queryFn: () => fetchOrganizationsPage(scoped),
    // The picker's term is typed: keep the previous matches up until the next ones land.
    placeholderData: keepWhileTyping(queryKeys.organizations(scoped)),
  })
}

/**
 * The Clienti list, every page reachable: the first on arrival and one more per
 * `loadMore()`, so a space with more organizations than a page holds is not cut at the first
 * page with only the search to reach the rest. Under the `['organizations']` prefix every
 * customer write invalidates; `pages` in the key keeps its infinite-query data apart from
 * `useOrganizations`' single pages.
 */
export function useOrganizationPages(
  params: Pick<OrganizationsListParams, 'search' | 'supplier_kind'> & { role?: RegistryRole } = {},
) {
  const scoped = useLegalEntityScope(params)
  const queryClient = useQueryClient()
  // The role is in `scoped`, so each view keeps a cache of its own: a list of suppliers is
  // never what the customers' tab shows while it loads.
  const queryKey = queryKeys.organizations({ ...scoped, pages: true })
  const pages = useCursorPages({
    queryKey,
    fetchPage: (cursor) => fetchOrganizationsPage(scoped, cursor),
  })
  // «Riprova» on the error banner: the same walk, asked again from its first page.
  return { ...pages, retry: () => void queryClient.invalidateQueries({ queryKey }) }
}

/**
 * How many organizations a role has, for the number beside a tab: one request of one row that
 * reads `total` (the whole registry for that role, the sidebar's legal entity honoured like
 * the list does). Under the `['organizations']` prefix, so every organization write refreshes
 * it. `undefined` while it is on its way or when it failed: a number that never arrived is
 * not drawn, and never drawn as 0.
 */
export function useOrganizationCount(role: RegistryRole, enabled = true): number | undefined {
  const scoped = useLegalEntityScope({ role })
  const count = useQuery({
    enabled,
    queryKey: queryKeys.organizations({ ...scoped, count: true }),
    queryFn: () => fetchOrganizationsPage({ ...scoped, limit: 1 }),
  })
  return typeof count.data?.total === 'number' ? count.data.total : undefined
}

/**
 * The organizations, in any role, that a VAT number typed in the form may already belong to
 * (design § 1: one record per company). Not scoped to the sidebar's legal entity: the unique
 * index is across them, so a number held under another entity is still taken. Under the
 * `['organizations']` prefix like every list, so a write refreshes it.
 */
export function useOrganizationsByVat(term: string, enabled: boolean) {
  return useQuery({
    enabled: enabled && term !== '',
    // Not under the `organizations` prefix every write invalidates: saving the form this lookup
    // belongs to would refetch it while the dialog is still closing, find the record just
    // created and draw «Esiste già: <itself>». A reopened form asks again on mount.
    queryKey: ['organization-vat-lookup', term] as const,
    queryFn: () => fetchOrganizationsPage({ search: term, role: 'any', limit: 10 }),
  })
}

/**
 * «Aggiungi il ruolo Fornitore»: the roles of an organization that already exists, grown by
 * one. The caller sends the union of what it has and the new role; a role is never taken away
 * from here (the brief's non-goal).
 */
export function useAddOrganizationRole() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, roles }: { id: string; roles: string[] }) =>
      unwrap(
        api.PATCH('/api/organizations/{organization_id}', {
          params: { path: { organization_id: id } },
          body: asBody<OrganizationUpdateBody>({ roles }),
        }),
      ),
    onSuccess: (updated) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.customer(updated.id) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.organizations() })
      void queryClient.invalidateQueries({ queryKey: queryKeys.timeline('organization', updated.id) })
    },
  })
}

export function useOrganization(customerId: string, options: { enabled?: boolean } = {}) {
  return useQuery({
    // `enabled` for a caller that only sometimes needs the row (the proforma dialog's
    // issuing legal entity, REB-626); an empty id must never produce a request (residuo B1).
    enabled: (options.enabled ?? true) && customerId !== '',
    queryKey: queryKeys.customer(customerId),
    queryFn: () =>
      unwrap(
        api.GET('/api/organizations/{organization_id}', {
          params: { path: { organization_id: customerId } },
        }),
      ),
  })
}

/**
 * `body` arrives as the loosely-typed `Record<string, unknown>` that
 * `OrganizationForm.submit` builds from whatever `DynamicForm` collected -- a dynamic
 * mix of native columns and tenant-defined custom fields that no fixed interface can
 * describe. `asBody` is where that is said, once.
 */
export function useCreateOrganization() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      unwrap(api.POST('/api/organizations', { body: asBody<OrganizationCreateBody>(body) })),
    onSuccess: () => {
      // `queryKeys.organizations()` (params defaulting to `{}`) is a deliberate
      // wildcard here, not merely "the no-filter case": TanStack Query matches an
      // object filter key by checking only the keys *present* on it, so `{}`
      // partially matches every params object a list query was ever called
      // with -- every cached search/limit combination gets invalidated, not just
      // the unfiltered one.
      void queryClient.invalidateQueries({ queryKey: queryKeys.organizations() })
    },
  })
}

export function useUpdateOrganization(customerId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      unwrap(
        api.PATCH('/api/organizations/{organization_id}', {
          params: { path: { organization_id: customerId } },
          body: asBody<OrganizationUpdateBody>(body),
        }),
      ),
    onSuccess: (_updated, body) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.customer(customerId) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.organizations() })
      void queryClient.invalidateQueries({ queryKey: queryKeys.timeline('organization', customerId) })
      // The addresses the mailbox found are for the domain read from the website and the
      // primary email (REB-766): a corrected website or a changed list of contact points
      // makes that answer the old domain's.
      if ('website' in body || 'contact_points' in body) {
        queryClient.removeQueries({ queryKey: correspondentKeys.customer(customerId) })
      }
    },
  })
}

/**
 * `DELETE /api/organizations/{id}` is a soft delete: the server sets `deleted_at` and
 * refuses with a 409 (naming how many) when active deals still reference this
 * customer -- see `OrganizationService.soft_delete`. Nothing here re-implements that
 * check; this hook only calls the endpoint, and the caller (the detail route) is
 * responsible for surfacing the 409's message instead of swallowing it.
 */
export function useDeleteOrganization() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (customerId: string) =>
      unwrap(
        api.DELETE('/api/organizations/{organization_id}', {
          params: { path: { organization_id: customerId } },
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.organizations() }),
  })
}

// -- Collegamenti: the customer detail page's own "linked records" tab -------------
//
// Deliberately not `usePeoplePages`/`useDeals` from `features/people`/`features/deals`
// (which exist now, with their own, more general list hooks): these two are
// narrower on purpose, exactly enough to render this one tab's short preview list,
// not a general-purpose people/deals data layer. Both list endpoints already
// accept a `customer_id` filter (apps/api/src/pigrocrm_api/routers/{people,deals}.py),
// so this is a real fetch against a real, working endpoint either way.
export type RelatedPerson = components['schemas']['PersonRead']
export type RelatedDeal = components['schemas']['DealRead']

export function useOrganizationPeople(customerId: string) {
  return useQuery({
    queryKey: queryKeys.people({ customer_id: customerId }),
    queryFn: () =>
      unwrap(api.GET('/api/people', { params: { query: { customer_id: customerId } } })),
  })
}

export function useOrganizationDeals(customerId: string, enabled = true) {
  return useQuery({
    enabled,
    queryKey: queryKeys.deals({ customer_id: customerId }),
    queryFn: () => unwrap(api.GET('/api/deals', { params: { query: { customer_id: customerId } } })),
  })
}
