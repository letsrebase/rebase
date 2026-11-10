/**
 * The registry's views: «Clienti», «Fornitori» and «Tutte», the `role` of
 * `GET /api/organizations` (the approved brief of 2026-11-10). One page serves all three;
 * the view is a search parameter, so the sidebar's two entries, the tabs and a bookmark all
 * say the same thing.
 */
export type RegistryRole = 'customer' | 'supplier' | 'any'

export const REGISTRY_ROLES: readonly RegistryRole[] = ['customer', 'supplier', 'any']

/** The view a page is on when the URL names none: the customers, as the page always was. */
export const DEFAULT_REGISTRY_ROLE: RegistryRole = 'customer'

export function isRegistryRole(value: unknown): value is RegistryRole {
  return typeof value === 'string' && (REGISTRY_ROLES as readonly string[]).includes(value)
}

/** The route's `validateSearch`: a term and a view, each dropped rather than carried when it
 *  is empty or not one of ours, so the URL never claims a filter that is not applied. */
export function parseOrganizationsSearch(search: Record<string, unknown>): {
  search?: string
  role?: RegistryRole
} {
  return {
    search:
      typeof search.search === 'string' && search.search.length > 0 ? search.search : undefined,
    role: isRegistryRole(search.role) ? search.role : undefined,
  }
}
