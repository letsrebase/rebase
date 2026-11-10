import { useQuery } from '@tanstack/react-query'
import { api, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'

export type PurchaseCapabilities = components['schemas']['PurchaseCapabilitiesRead']

/**
 * What the buyer's fiscal pack lets a purchase carry (`GET /api/purchases/capabilities`): the
 * form hides what the server would refuse, and reads no regime or pack name itself.
 *
 * The buyer is the legal entity the purchase will belong to: the azienda chosen on the form,
 * else its deal's, else none, which the server resolves to the space's only active legal entity.
 * While the answer is on its way, or when it could not be read, the data is `undefined` and the
 * caller shows everything: a capability that is not known never blocks the form.
 */
export function usePurchaseCapabilities(legalEntityId: string | null | undefined) {
  return useQuery({
    queryKey: ['purchases', 'capabilities', legalEntityId ?? null] as const,
    queryFn: () =>
      unwrap(
        api.GET('/api/purchases/capabilities', {
          params: { query: { legal_entity_id: legalEntityId ?? undefined } },
        }),
      ),
  })
}
