import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useAziendaScope } from '@/lib/azienda'
import { api, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { queryKeys } from '@/lib/query'

/**
 * Wire shape of one customer, taken directly from the generated OpenAPI schema
 * (`components['schemas']['CustomerRead']`) rather than hand-declared -- the same
 * reasoning as Timeline.tsx's `ActivityEntry` alias: the single source of truth is
 * `CustomerRead` in packages/core/src/pigrocrm/core/customers/schemas.py, and this
 * alias tracks it through `pnpm generate:api` automatically. A hand-written
 * interface here would silently drift the day that schema grows a field.
 */
export type Customer = components['schemas']['CustomerRead']
export type CustomerPage = components['schemas']['CustomerPage']

type CustomerCreateBody = components['schemas']['CustomerCreate']
type CustomerUpdateBody = components['schemas']['CustomerUpdate']

interface CustomersListParams {
  search?: string
  limit?: number
  azienda_id?: string
}

/**
 * `/api/customers`, never the bare `/customers`: every router in
 * apps/api/src/pigrocrm_api/routers/*.py declares its own "/api" prefix, and the
 * shared client (lib/api.ts) is built with `baseUrl: ''` to match -- confirmed live,
 * the bare path 404s.
 */
export function useCustomers(params: CustomersListParams = {}) {
  // The sidebar's azienda, when one is selected (REB-625): on the list page and on
  // every picker that offers the space's customers, which under azienda A are A's.
  const scoped = useAziendaScope(params)
  return useQuery({
    queryKey: queryKeys.customers(scoped),
    queryFn: () =>
      unwrap(
        api.GET('/api/customers', {
          params: {
            query: { search: scoped.search, limit: scoped.limit, azienda_id: scoped.azienda_id },
          },
        }),
      ),
  })
}

export function useCustomer(customerId: string, options: { enabled?: boolean } = {}) {
  return useQuery({
    // `enabled` for a caller that only sometimes needs the row (the proforma dialog's
    // issuing azienda, REB-626); an empty id must never produce a request (residuo B1).
    enabled: (options.enabled ?? true) && customerId !== '',
    queryKey: queryKeys.customer(customerId),
    queryFn: () =>
      unwrap(
        api.GET('/api/customers/{customer_id}', {
          params: { path: { customer_id: customerId } },
        }),
      ),
  })
}

/**
 * `body` arrives as the loosely-typed `Record<string, unknown>` that
 * `CustomerForm.submit` builds from whatever `DynamicForm` collected -- a dynamic
 * mix of native columns and tenant-defined custom fields that no fixed interface can
 * describe. `as unknown as CustomerCreateBody` says so honestly, the same idiom
 * `lib/schema.ts` already uses for the same reason (`schema as unknown as
 * EntitySchema`), rather than a bare `as never` that hides the target type
 * entirely.
 */
export function useCreateCustomer() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      unwrap(api.POST('/api/customers', { body: body as unknown as CustomerCreateBody })),
    onSuccess: () => {
      // `queryKeys.customers()` (params defaulting to `{}`) is a deliberate
      // wildcard here, not merely "the no-filter case": TanStack Query matches an
      // object filter key by checking only the keys *present* on it, so `{}`
      // partially matches every params object a list query was ever called
      // with -- every cached search/limit combination gets invalidated, not just
      // the unfiltered one.
      void queryClient.invalidateQueries({ queryKey: queryKeys.customers() })
    },
  })
}

export function useUpdateCustomer(customerId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      unwrap(
        api.PATCH('/api/customers/{customer_id}', {
          params: { path: { customer_id: customerId } },
          body: body as unknown as CustomerUpdateBody,
        }),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.customer(customerId) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.customers() })
      void queryClient.invalidateQueries({ queryKey: queryKeys.timeline('customer', customerId) })
    },
  })
}

/**
 * `DELETE /api/customers/{id}` is a soft delete: the server sets `deleted_at` and
 * refuses with a 409 (naming how many) when active deals still reference this
 * customer -- see `CustomerService.soft_delete`. Nothing here re-implements that
 * check; this hook only calls the endpoint, and the caller (the detail route) is
 * responsible for surfacing the 409's message instead of swallowing it.
 */
export function useDeleteCustomer() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (customerId: string) =>
      unwrap(
        api.DELETE('/api/customers/{customer_id}', {
          params: { path: { customer_id: customerId } },
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.customers() }),
  })
}

// -- Collegamenti: the customer detail page's own "linked records" tab -------------
//
// Deliberately not `usePeople`/`useDeals` from `features/people`/`features/deals`
// (which exist now, with their own, more general list hooks): these two are
// narrower on purpose, exactly enough to render this one tab's short preview list,
// not a general-purpose people/deals data layer. Both list endpoints already
// accept a `customer_id` filter (apps/api/src/pigrocrm_api/routers/{people,deals}.py),
// so this is a real fetch against a real, working endpoint either way.
export type RelatedPerson = components['schemas']['PersonRead']
export type RelatedDeal = components['schemas']['DealRead']

export function useCustomerPeople(customerId: string) {
  return useQuery({
    queryKey: queryKeys.people({ customer_id: customerId }),
    queryFn: () =>
      unwrap(api.GET('/api/people', { params: { query: { customer_id: customerId } } })),
  })
}

export function useCustomerDeals(customerId: string) {
  return useQuery({
    queryKey: queryKeys.deals({ customer_id: customerId }),
    queryFn: () => unwrap(api.GET('/api/deals', { params: { query: { customer_id: customerId } } })),
  })
}
