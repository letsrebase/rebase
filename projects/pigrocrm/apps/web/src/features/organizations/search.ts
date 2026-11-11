/**
 * The two registry pages (REB-1053): «Clienti» is `/app/customers` and «Fornitori» is
 * `/app/suppliers`, each with its own list, the `role` of `GET /api/organizations` (a page
 * asks for its own name). An organization with both roles is on both. A freelancer is always a
 * supplier (REB-1061), so none is on neither list while `purchases`, the module of «Fornitori», is
 * on. A read that wants every organization (the pickers, the duplicate check) asks the API for
 * `role: 'any'` itself and is no page.
 */
export type RegistryPage = 'customer' | 'supplier'

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
 * and only while `purchases` is on (the page is that module's); «Clienti» for every other. A
 * freelancer is always a supplier (REB-1061), so one that is not also a customer is read as
 * «Fornitori».
 */
export function registryPageOf(roles: readonly string[], purchasesOn: boolean): RegistryPage {
  return purchasesOn && roles.includes('supplier') && !roles.includes('customer')
    ? 'supplier'
    : 'customer'
}
