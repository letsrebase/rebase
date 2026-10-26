import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { queryKeys } from '@/lib/query'

/**
 * Wire shape of one person, taken directly from the generated OpenAPI schema
 * (`components['schemas']['PersonRead']`) rather than hand-declared -- same
 * reasoning as `customers/queries.ts`'s `Customer` alias: the single source of
 * truth is `PersonRead` in packages/core/src/pigrocrm/core/people/schemas.py, and
 * this alias tracks it through `pnpm generate:api` automatically.
 */
export type Person = components['schemas']['PersonRead']
export type PersonPage = components['schemas']['PersonPage']

type PersonCreateBody = components['schemas']['PersonCreate']
type PersonUpdateBody = components['schemas']['PersonUpdate']

interface PeopleListParams {
  search?: string
  customer_id?: string
  limit?: number
}

/**
 * `/api/people`, never the bare `/people`: every router in
 * apps/api/src/pigrocrm_api/routers/*.py declares its own "/api" prefix, and the
 * shared client (lib/api.ts) is built with `baseUrl: ''` to match -- confirmed
 * live for the identical case on `/api/customers` (see that file's own comment).
 */
export function usePeople(params: PeopleListParams = {}) {
  return useQuery({
    queryKey: queryKeys.people(params),
    queryFn: () =>
      unwrap(
        api.GET('/api/people', {
          params: {
            query: {
              search: params.search,
              customer_id: params.customer_id,
              limit: params.limit,
            },
          },
        }),
      ),
  })
}

export function usePerson(personId: string, options: { enabled?: boolean } = {}) {
  return useQuery({
    // `enabled` for a caller that only sometimes needs the row (the attività dialog
    // names the person a task hangs on); an empty id must never produce a request.
    enabled: (options.enabled ?? true) && personId !== '',
    queryKey: queryKeys.person(personId),
    queryFn: () =>
      unwrap(api.GET('/api/people/{person_id}', { params: { path: { person_id: personId } } })),
  })
}

/**
 * `body` arrives as the loosely-typed `Record<string, unknown>` that
 * `PersonForm.submit` builds from whatever `DynamicForm` (plus the dedicated
 * customer picker) collected -- the same idiom `customers/queries.ts` uses for
 * the identical reason: a dynamic mix of native columns and tenant-defined
 * custom fields that no fixed interface can describe. `as unknown as
 * PersonCreateBody` says so honestly rather than a bare `as never` that hides
 * the target type entirely.
 */
export function useCreatePerson() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      unwrap(api.POST('/api/people', { body: body as unknown as PersonCreateBody })),
    onSuccess: () => {
      // `queryKeys.people()` (params defaulting to `{}`) partially matches every
      // cached people query, search/customer_id/limit combination alike -- same
      // wildcard-invalidation trick `useCreateCustomer` uses, and it also covers
      // `useCustomerPeople`'s own cache entries (`queryKeys.people({customer_id})`)
      // in `features/customers/queries.ts`, so creating a person here keeps that
      // Collegamenti preview list in sync too.
      void queryClient.invalidateQueries({ queryKey: queryKeys.people() })
    },
  })
}

export function useUpdatePerson(personId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      unwrap(
        api.PATCH('/api/people/{person_id}', {
          params: { path: { person_id: personId } },
          body: body as unknown as PersonUpdateBody,
        }),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.person(personId) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.people() })
      void queryClient.invalidateQueries({ queryKey: queryKeys.timeline('person', personId) })
    },
  })
}

/**
 * `DELETE /api/people/{id}` is a soft delete: the server sets `deleted_at`, and,
 * unlike `CustomerService.soft_delete`, `PersonService.soft_delete` raises no
 * conflict for anything that still references this person -- no entity in this
 * slice has a foreign key to a person (`Deal` only ever references `customer_id`,
 * confirmed by reading packages/core/src/pigrocrm/core/deals/schemas.py). There is
 * therefore no active-deals-style count to surface here, unlike
 * `useDeleteCustomer`'s docstring on the identical-looking call.
 */
export function useDeletePerson() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (personId: string) =>
      unwrap(api.DELETE('/api/people/{person_id}', { params: { path: { person_id: personId } } })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.people() }),
  })
}
