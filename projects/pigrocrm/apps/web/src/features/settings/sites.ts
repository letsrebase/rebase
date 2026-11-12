import type { LegalEntitySite } from './queries'

type Place = Pick<LegalEntitySite, 'city' | 'address'>

/** «Comune, indirizzo» of a site, or `null` when it states neither. */
export function sitePlace(site: Place): string | null {
  const parts = [site.city, site.address].filter((part): part is string => part !== null && part !== undefined && part !== '')
  return parts.length > 0 ? parts.join(', ') : null
}

/** How a site is named in a choice: its place, else the region it is in. */
export function siteLabel(site: Place & Pick<LegalEntitySite, 'region_name' | 'region'>): string {
  return sitePlace(site) ?? site.region_name ?? site.region
}
