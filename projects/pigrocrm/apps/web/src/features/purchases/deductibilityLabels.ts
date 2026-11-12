import type { components } from '@/lib/api-types'

/**
 * How the law treats a cost (REB-1089), named for a person. The values are the API's own
 * (`purchases/deductibility.py`); the rule behind each one (the percentage, the cap, the
 * coefficient) is the pack's data and is never repeated here.
 */
export type DeductibilityKind = NonNullable<
  components['schemas']['PurchaseCreate']['deductibility_kind']
>
export type AssetGroup = NonNullable<components['schemas']['PurchaseCreate']['asset_group']>

export const DEDUCTIBILITY_KIND_LABELS: Record<DeductibilityKind, string> = {
  full: 'Interamente deducibile',
  vehicle: 'Veicolo, uso promiscuo',
  vehicle_agent: 'Veicolo di agente o rappresentante',
  vehicle_employee: 'Veicolo assegnato a un dipendente',
  phone: 'Telefonia',
  hospitality: 'Alberghi e ristoranti',
  representation: 'Spese di rappresentanza',
}

export const ASSET_GROUP_LABELS: Record<AssetGroup, string> = {
  office_furniture: 'Mobili e arredi d’ufficio',
  office_electronics: 'Macchine d’ufficio elettroniche',
  cars: 'Autovetture',
  goods_vehicles: 'Autocarri e veicoli da trasporto',
  other: 'Altro (gruppo non in tabella)',
}

/** A `Select` needs a non-empty string for «nothing stated», which the API spells `null`. */
export const NOT_STATED = 'non-indicata'
