import type { Organization } from './queries'

/**
 * The words of the registry's roles and fiscal kinds (the approved brief of 2026-11-10,
 * `organizations/roles.py` for the values). One registry row per company, with the roles it
 * plays: what a role or a kind is called on screen lives here and nowhere else, so the table,
 * the cards, the form and the detail page cannot drift apart.
 */

export type OrganizationRole = 'customer' | 'supplier' | 'freelancer'

/** The order roles are drawn in, wherever a set of them is. */
export const ROLE_ORDER: readonly OrganizationRole[] = ['customer', 'supplier', 'freelancer']

/** A role as a chip reads («Cliente»). */
export const ROLE_LABELS: Record<OrganizationRole, string> = {
  customer: 'Cliente',
  supplier: 'Fornitore',
  freelancer: 'Freelance',
}

/** The roles of a row in the order above, an unknown one kept after them rather than lost. */
export function orderedRoles(roles: readonly string[]): string[] {
  const known = ROLE_ORDER.filter((role) => roles.includes(role))
  return [...known, ...roles.filter((role) => !(ROLE_ORDER as readonly string[]).includes(role))]
}

/** A role as a chip reads, an unknown one as it is stored. */
export function roleLabel(role: string): string {
  return ROLE_LABELS[role as OrganizationRole] ?? role
}

/** The roles in a sentence, lower case: «cliente», «cliente e fornitore». */
export function rolesInWords(roles: readonly string[]): string {
  const words = orderedRoles(roles).map((role) => roleLabel(role).toLowerCase())
  if (words.length <= 1) return words.join('')
  return `${words.slice(0, -1).join(', ')} e ${words[words.length - 1]}`
}


/**
 * The comparison form of a VAT number: upper case, letters and digits only, the rule of
 * `normalise_vat` in `organizations/roles.py`, so `nl 1234.56.789 b01` and `NL123456789B01`
 * are one number to the form and to the server.
 */
export function normaliseVat(value: string): string {
  return value.toUpperCase().replace(/[^0-9A-Z]/g, '')
}

export const SUPPLIER_KINDS = ['company', 'professional', 'flat_rate', 'occasional', 'foreign'] as const
export type SupplierKind = (typeof SUPPLIER_KINDS)[number]

/** What a payment to a supplier involves (`SupplierKind`). */
export const SUPPLIER_KIND_LABELS: Record<SupplierKind, string> = {
  company: 'Società',
  professional: 'Professionista',
  flat_rate: 'Forfettario',
  occasional: 'Occasionale',
  foreign: 'Estero',
}

export const SUPPLIER_AREAS = ['eu', 'extra_eu'] as const
export type SupplierArea = (typeof SUPPLIER_AREAS)[number]
export const SUPPLIER_AREA_LABELS: Record<SupplierArea, string> = {
  eu: 'UE',
  extra_eu: 'extra UE',
}

export const SUPPLIER_SUPPLIES = ['goods', 'services'] as const
export type SupplierSupplies = (typeof SUPPLIER_SUPPLIES)[number]
export const SUPPLIER_SUPPLIES_LABELS: Record<SupplierSupplies, string> = {
  goods: 'beni',
  services: 'servizi',
}

export const CUSTOMER_KINDS = ['business', 'consumer', 'public_body'] as const
export type CustomerKind = (typeof CUSTOMER_KINDS)[number]
export const CUSTOMER_KIND_LABELS: Record<CustomerKind, string> = {
  business: 'Impresa o professionista',
  consumer: 'Privato',
  public_body: 'Ente pubblico',
}

const EMPTY = '—'

function labelled<T extends string>(labels: Record<T, string>, value: string | null): string | null {
  if (value === null || value === '') return null
  return labels[value as T] ?? value
}

/**
 * «Professionista», or for a foreign supplier «Estero · extra UE · servizi»: the kind, then
 * where it is and what it supplies, each only when the row says it. The dash for a supplier
 * whose kind nobody has set.
 */
export function supplierKindText(
  organization: Pick<Organization, 'supplier_kind' | 'supplier_area' | 'supplier_supplies'>,
): string {
  const parts = [
    labelled(SUPPLIER_KIND_LABELS, organization.supplier_kind),
    labelled(SUPPLIER_AREA_LABELS, organization.supplier_area),
    labelled(SUPPLIER_SUPPLIES_LABELS, organization.supplier_supplies),
  ].filter((part): part is string => part !== null)
  return parts.length === 0 ? EMPTY : parts.join(' · ')
}

/** What the supplier grants us, short: «30 gg», «30 gg f.m.»; the dash when it names no days. */
export function supplierTermsShort(
  organization: Pick<Organization, 'supplier_payment_days' | 'supplier_payment_end_of_month'>,
): string {
  if (organization.supplier_payment_days === null) return EMPTY
  return `${organization.supplier_payment_days} gg${organization.supplier_payment_end_of_month ? ' f.m.' : ''}`
}

/** The same terms in the words a contract uses, for the detail page. */
export function supplierTermsLong(
  organization: Pick<Organization, 'supplier_payment_days' | 'supplier_payment_end_of_month'>,
): string {
  if (organization.supplier_payment_days === null) return EMPTY
  const endMonth = organization.supplier_payment_end_of_month ? ' fine mese' : ''
  return `${organization.supplier_payment_days} giorni data fattura${endMonth}`
}

/** «Impresa o professionista», or the dash. */
export function customerKindText(organization: Pick<Organization, 'customer_kind'>): string {
  return labelled(CUSTOMER_KIND_LABELS, organization.customer_kind) ?? EMPTY
}
