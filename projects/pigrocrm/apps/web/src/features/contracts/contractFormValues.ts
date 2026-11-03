import { isBlank } from '@/lib/formValues'
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
  title: string
  start: string
  end: string
  renewal_type: RenewalType
  renewal_notice_days: string
  termination_notice_days: string
  /** `false` means «come il cliente»: both payment-term columns stay null. */
  payment_custom: boolean
  payment_days: string
  payment_end_of_month: boolean
  billing_cadence: string
  currency: string
  requires_prior_approval: boolean
  applies_social_charge: boolean
  /** The customer's cap on its share of the azienda's invoiced revenue, as a percentage
   *  string («50» or «50.00»); blank means no cap. */
  customer_share_cap: string
  expenses_kind: ExpensePolicyKind
  expenses_cap: string
  expenses_preauthorization: boolean
  note: string
  custom: Record<string, unknown>
}

export function defaultContractFormValues(): ContractFormValues {
  return {
    title: '',
    start: '',
    end: '',
    renewal_type: 'none',
    renewal_notice_days: '',
    termination_notice_days: '30',
    payment_custom: false,
    payment_days: '',
    payment_end_of_month: false,
    billing_cadence: 'monthly',
    currency: 'EUR',
    requires_prior_approval: false,
    applies_social_charge: false,
    customer_share_cap: '',
    expenses_kind: 'not_reimbursable',
    expenses_cap: '',
    expenses_preauthorization: false,
    note: '',
    custom: {},
  }
}

export function contractToFormValues(contract: Contract): ContractFormValues {
  const policy = readExpensePolicy(contract.expense_policy)
  return {
    title: contract.title,
    start: contract.start,
    end: contract.end ?? '',
    renewal_type: contract.renewal_type as RenewalType,
    renewal_notice_days: contract.renewal_notice_days?.toString() ?? '',
    termination_notice_days: contract.termination_notice_days.toString(),
    payment_custom: contract.payment_days !== null,
    payment_days: contract.payment_days?.toString() ?? '',
    payment_end_of_month: contract.payment_end_of_month ?? false,
    billing_cadence: contract.billing_cadence,
    currency: contract.currency,
    requires_prior_approval: contract.requires_prior_approval,
    applies_social_charge: contract.applies_social_charge,
    customer_share_cap: contract.customer_share_cap ?? '',
    expenses_kind: policy?.kind ?? 'not_reimbursable',
    expenses_cap: policy?.amount_cap ?? '',
    expenses_preauthorization: policy?.requires_pre_authorization ?? false,
    note: contract.note ?? '',
    custom: { ...contract.custom_fields },
  }
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
    kind: values.expenses_kind,
    amount_cap: values.expenses_cap.trim().replace(',', '.'),
    requires_pre_authorization: values.expenses_preauthorization,
  })
  const full: ContractUpdateBody = {
    title: values.title.trim(),
    start: values.start,
    end: values.end === '' ? null : values.end,
    renewal_type: values.renewal_type,
    renewal_notice_days:
      values.renewal_type === 'none' ? null : asInt(values.renewal_notice_days),
    termination_notice_days: asInt(values.termination_notice_days),
    payment_days: values.payment_custom ? asInt(values.payment_days) : null,
    payment_end_of_month: values.payment_custom ? values.payment_end_of_month : null,
    billing_cadence: values.billing_cadence,
    currency: values.currency.trim().toUpperCase(),
    requires_prior_approval: values.requires_prior_approval,
    applies_social_charge: values.applies_social_charge,
    customer_share_cap:
      values.customer_share_cap.trim() === ''
        ? null
        : values.customer_share_cap.trim().replace(',', '.'),
    expense_policy: policy,
    note: values.note.trim() === '' ? null : values.note,
  }

  // Only the custom keys that moved: re-sending a value nobody touched would re-validate
  // it against a definition that may have tightened since, and refuse an unrelated edit.
  const custom: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(values.custom)) {
    // A stored value whose definition is archived is omitted, so the service carries it
    // over untouched instead of refusing a key no active definition names.
    if (!isRenderedCustomKey(key)) continue
    const before = initial?.custom[key]
    if (initial !== undefined && JSON.stringify(value) === JSON.stringify(before)) continue
    if (!isBlank(value)) custom[key] = value
    else if (!isBlank(before)) custom[key] = null
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
    ['payment_days', 'payment_end_of_month'],
    ['renewal_type', 'renewal_notice_days'],
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
