/**
 * The two registry pages (REB-1053): «Clienti» is `/app/customers` and «Fornitori» is
 * `/app/suppliers`, each with its own list, the `role` of `GET /api/organizations`. An
 * organization with both roles is on both. A read that wants every organization (the pickers,
 * the duplicate check) asks the API for `role: 'any'` itself and is no page.
 */
export type RegistryPage = 'customer' | 'supplier'

/**
 * The `role` each page asks `GET /api/organizations` for. «Fornitori» is the suppliers; «Clienti» is
 * the customers and every organization that is neither a customer nor a supplier (a freelancer-only
 * record), so no organization is on no list.
 */
export const REGISTRY_API_ROLE = {
  customer: 'customer_or_neither',
  supplier: 'supplier',
} as const satisfies Record<RegistryPage, string>

/** The path each registry page lives at. */
export const REGISTRY_PAGE_PATHS = {
  customer: '/app/customers',
  supplier: '/app/suppliers',
} as const satisfies Record<RegistryPage, string>

/** The route's `validateSearch` of a registry page: a term, dropped rather than carried when it
 *  is empty, so the URL never claims a filter that is not applied. */
export function parseRegistrySearch(search: Record<string, unknown>): { search?: string } {
  return {
    search:
      typeof search.search === 'string' && search.search.length > 0 ? search.search : undefined,
  }
}

/**
 * `/app/organizations` was one page with a view in `?role=` (REB-1023, reversed by REB-1053):
 * a bookmark or a link already sent still carries it. `supplier` is «Fornitori»; every other
 * value, `any` of the retired «Tutte» tab included, is «Clienti», the page the list always was.
 */
export function legacyRegistryPage(search: Record<string, unknown>): RegistryPage {
  return search.role === 'supplier' ? 'supplier' : 'customer'
}

/**
 * The list an organization is read as: «Fornitori» for one that is a supplier and not a customer,
 * and only while `purchases` is on (the page is that module's); «Clienti» for every other, a
 * freelancer-only record included, which «Clienti» lists (REB-1038, REB-1053).
 */
export function registryPageOf(roles: readonly string[], purchasesOn: boolean): RegistryPage {
  return purchasesOn && roles.includes('supplier') && !roles.includes('customer')
    ? 'supplier'
    : 'customer'
}
