import { useOrganization } from '@/features/organizations/queries'

/** The supplier's name, read from its id: a row carries the id only, and one organization is
 *  read once however many rows name it. */
export function SupplierName({ id }: { id: string }) {
  const organization = useOrganization(id)
  return <>{organization.data?.legal_name ?? '—'}</>
}
