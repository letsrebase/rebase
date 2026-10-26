import {
  expensePolicyBody,
  readExpensePolicy,
  type ExpensePolicyKind,
  type RenewalType,
} from './labels'
import type { Contract, ContractUpdateBody } from './queries'

/**
 * The form's state: every input as a string or a boolean, because that is what a
 * control gives back, converted to the API's shape once, in `toBody`. Dates stay
 * `YYYY-MM-DD` strings end to end (never a `Date`), and money stays a string.
 */
export interface ContractFormValues {
  titolo: string
  inizio: string
  fine: string
  tipo_rinnovo: RenewalType
  preavviso_rinnovo_giorni: string
  preavviso_disdetta_giorni: string
  /** `false` means «come il cliente»: both payment-term columns stay null. */
  pagamento_proprio: boolean
  giorni_pagamento: string
  pagamento_fine_mese: boolean
  cadenza_fatturazione: string
  divisa: string
  requires_prior_approval: boolean
  applies_social_charge: boolean
  spese_kind: ExpensePolicyKind
  spese_tetto: string
  spese_preautorizzazione: boolean
  note: string
  custom: Record<string, unknown>
}

export function defaultContractFormValues(): ContractFormValues {
  return {
    titolo: '',
    inizio: '',
    fine: '',
    tipo_rinnovo: 'nessuno',
    preavviso_rinnovo_giorni: '',
    preavviso_disdetta_giorni: '30',
    pagamento_proprio: false,
    giorni_pagamento: '',
    pagamento_fine_mese: false,
    cadenza_fatturazione: 'mensile',
    divisa: 'EUR',
    requires_prior_approval: false,
    applies_social_charge: false,
    spese_kind: 'non_rimborsabile',
    spese_tetto: '',
    spese_preautorizzazione: false,
    note: '',
    custom: {},
  }
}

export function contractToFormValues(contract: Contract): ContractFormValues {
  const policy = readExpensePolicy(contract.politica_spese)
  return {
    titolo: contract.titolo,
    inizio: contract.inizio,
    fine: contract.fine ?? '',
    tipo_rinnovo: contract.tipo_rinnovo as RenewalType,
    preavviso_rinnovo_giorni: contract.preavviso_rinnovo_giorni?.toString() ?? '',
    preavviso_disdetta_giorni: contract.preavviso_disdetta_giorni.toString(),
    pagamento_proprio: contract.giorni_pagamento !== null,
    giorni_pagamento: contract.giorni_pagamento?.toString() ?? '',
    pagamento_fine_mese: contract.pagamento_fine_mese ?? false,
    cadenza_fatturazione: contract.cadenza_fatturazione,
    divisa: contract.divisa,
    requires_prior_approval: contract.requires_prior_approval,
    applies_social_charge: contract.applies_social_charge,
    spese_kind: policy?.kind ?? 'non_rimborsabile',
    spese_tetto: policy?.importo_tetto ?? '',
    spese_preautorizzazione: policy?.richiede_preautorizzazione ?? false,
    note: contract.note ?? '',
    custom: { ...contract.custom_fields },
  }
}

function isBlank(value: unknown): boolean {
  if (value === null || value === undefined) return true
  if (typeof value === 'string') return value.trim() === ''
  if (Array.isArray(value)) return value.length === 0
  return false
}

/** An input's text as the integer the API wants, or `null` for an empty box. A value
 *  that is not a whole number is passed on as typed: the server answers with its own 422
 *  for it, and this form never invents a rule of its own. */
function asInt(value: string): number | null {
  const text = value.trim()
  if (text === '') return null
  return /^\d+$/.test(text) ? Number(text) : (text as unknown as number)
}

/**
 * The request body for `values`. On create every field is sent. On edit only what moved
 * since `initial`, so a notice period changed alone is a PATCH of that one field and
 * never rewrites the payment terms next to it, and the two cross-field pairs (payment
 * terms, renewal type and its notice) travel together whenever either half moved.
 */
export function toContractBody(
  values: ContractFormValues,
  initial: ContractFormValues | undefined,
  isRenderedCustomKey: (key: string) => boolean,
): ContractUpdateBody {
  const policy = expensePolicyBody({
    kind: values.spese_kind,
    importo_tetto: values.spese_tetto.trim(),
    richiede_preautorizzazione: values.spese_preautorizzazione,
  })
  const full: ContractUpdateBody = {
    titolo: values.titolo.trim(),
    inizio: values.inizio,
    fine: values.fine === '' ? null : values.fine,
    tipo_rinnovo: values.tipo_rinnovo,
    preavviso_rinnovo_giorni:
      values.tipo_rinnovo === 'nessuno' ? null : asInt(values.preavviso_rinnovo_giorni),
    preavviso_disdetta_giorni: asInt(values.preavviso_disdetta_giorni) ?? undefined,
    giorni_pagamento: values.pagamento_proprio ? asInt(values.giorni_pagamento) : null,
    pagamento_fine_mese: values.pagamento_proprio ? values.pagamento_fine_mese : null,
    cadenza_fatturazione: values.cadenza_fatturazione,
    divisa: values.divisa.trim().toUpperCase(),
    requires_prior_approval: values.requires_prior_approval,
    applies_social_charge: values.applies_social_charge,
    politica_spese: policy,
    note: values.note.trim() === '' ? null : values.note,
  }

  const custom: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(values.custom)) {
    // A stored value whose definition is archived is omitted, so the service carries it
    // over untouched instead of refusing a key no active definition names.
    if (!isRenderedCustomKey(key)) continue
    if (!isBlank(value)) custom[key] = value
    else if (!isBlank(initial?.custom[key])) custom[key] = null
  }
  full.custom_fields = custom

  if (initial === undefined) return full

  const before = toContractBody(initial, undefined, () => false)
  const changed: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(full)) {
    if (key === 'custom_fields') continue
    if (JSON.stringify(value) !== JSON.stringify((before as Record<string, unknown>)[key])) {
      changed[key] = value
    }
  }
  const pairs: [string, string][] = [
    ['giorni_pagamento', 'pagamento_fine_mese'],
    ['tipo_rinnovo', 'preavviso_rinnovo_giorni'],
  ]
  for (const [one, other] of pairs) {
    if (one in changed || other in changed) {
      changed[one] = (full as Record<string, unknown>)[one]
      changed[other] = (full as Record<string, unknown>)[other]
    }
  }
  if (Object.keys(custom).length > 0) changed.custom_fields = custom
  return changed as ContractUpdateBody
}
