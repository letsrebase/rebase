import { formatMoney } from '@/features/invoices/format'
import type { StatusTone } from '@/components/StatusPill'
import type { ContractExpense, ContractState, RateCard, RateCardCreateBody } from './queries'

/**
 * What the contract screens call each closed value the API stores, and the tone a state
 * reads as. Italian here because it is what the product says; the values themselves are
 * the ones `contracts/schemas.py` closes (`ContractState`, `RenewalType`, the rate card
 * literals), and a `Record` over each union makes a value added there a compile error
 * here instead of a blank label.
 */
export const STATE_LABELS: Record<ContractState, string> = {
  draft: 'Bozza',
  active: 'Attivo',
  terminated: 'Terminato',
  expired: 'Scaduto',
}

export const STATE_TONE: Record<ContractState, StatusTone> = {
  draft: 'muted',
  active: 'ink',
  terminated: 'danger',
  expired: 'gold',
}

export const STATES = Object.keys(STATE_LABELS) as ContractState[]

/** The states a contract can move to from `status`: all but the one it is in. */
export function nextStates(status: string): ContractState[] {
  return STATES.filter((state) => state !== status)
}

export function stateLabel(status: string): string {
  return status in STATE_LABELS ? STATE_LABELS[status as ContractState] : status
}

export function stateTone(status: string): StatusTone {
  return status in STATE_TONE ? STATE_TONE[status as ContractState] : 'muted'
}

export const RENEWAL_TYPES = ['none', 'explicit', 'counterparty_option', 'tacit'] as const
export type RenewalType = (typeof RENEWAL_TYPES)[number]

export const RENEWAL_LABELS: Record<RenewalType, string> = {
  none: 'Nessun rinnovo',
  explicit: 'Rinnovo esplicito',
  counterparty_option: 'Opzione della controparte',
  tacit: 'Rinnovo tacito',
}

export function renewalLabel(value: string): string {
  return value in RENEWAL_LABELS ? RENEWAL_LABELS[value as RenewalType] : value
}

/** `billing_cadence` has no CHECK in the database (the value set is the
 *  engagement's own to pin, `contracts/models.py`), so the form offers these four and
 *  keeps whatever else a contract already holds. */
export const CADENCES = ['monthly', 'quarterly', 'annual', 'on_completion'] as const

export const CADENCE_LABELS: Record<(typeof CADENCES)[number], string> = {
  monthly: 'Mensile',
  quarterly: 'Trimestrale',
  annual: 'Annuale',
  on_completion: 'A completamento',
}

export function cadenceLabel(value: string): string {
  return value in CADENCE_LABELS ? CADENCE_LABELS[value as (typeof CADENCES)[number]] : value
}

/** The three shapes of `expense_policy`, tagged by `kind`
 *  (`contract_expenses/models.py`). `amount_cap` rides only on the capped one;
 *  `requires_pre_authorization` on the two that reimburse. */
export const EXPENSE_POLICY_KINDS = [
  'not_reimbursable',
  'reimbursable',
  'reimbursable_with_cap',
] as const
export type ExpensePolicyKind = (typeof EXPENSE_POLICY_KINDS)[number]

export const EXPENSE_POLICY_LABELS: Record<ExpensePolicyKind, string> = {
  not_reimbursable: 'Non rimborsate',
  reimbursable: 'Rimborsate a costo',
  reimbursable_with_cap: 'Rimborsate fino a un tetto',
}

export interface ExpensePolicy {
  kind: ExpensePolicyKind
  amount_cap?: string
  requires_pre_authorization?: boolean
}

/** Reads the open JSONB back as one of the three shapes, or `null` for a document the
 *  form did not write (an agent's `{"tipo": ...}`): shown as stated, never guessed. */
export function readExpensePolicy(raw: Record<string, unknown>): ExpensePolicy | null {
  const kind = raw.kind
  if (!EXPENSE_POLICY_KINDS.includes(kind as ExpensePolicyKind)) return null
  return {
    kind: kind as ExpensePolicyKind,
    amount_cap:
      typeof raw.amount_cap === 'string' || typeof raw.amount_cap === 'number'
        ? String(raw.amount_cap)
        : undefined,
    requires_pre_authorization:
      typeof raw.requires_pre_authorization === 'boolean' ? raw.requires_pre_authorization : undefined,
  }
}

export function expensePolicyBody(policy: ExpensePolicy): Record<string, unknown> {
  if (policy.kind === 'not_reimbursable') return { kind: policy.kind }
  const body: Record<string, unknown> = {
    kind: policy.kind,
    requires_pre_authorization: policy.requires_pre_authorization === true,
  }
  if (policy.kind === 'reimbursable_with_cap') body.amount_cap = policy.amount_cap
  return body
}

export const RATE_TYPE_OPTIONS: Record<RateCardCreateBody['type'], string> = {
  fixed_recurring: 'Canone ricorrente',
  daily: 'Giornaliera',
  hourly: 'Oraria',
  one_off: 'Una tantum',
}

export const RateUnit: Record<RateCardCreateBody['unit'], string> = {
  hour: 'ora',
  day: 'giorno',
  month: 'mese',
  year: 'anno',
  flat: 'forfait',
}

export const RatePeriods: Record<NonNullable<RateCardCreateBody['delivery_period']>, string> = {
  monthly: 'Mensile',
  quarterly: 'Trimestrale',
  annual: 'Annuale',
  one_off: 'Una tantum',
}

/** Where an expense stands in the cycle to the client. `reimbursable` is the database's
 *  own verdict (the contract's policy against this expense's authorisation), never
 *  recomputed here, and `invoice_line_id` is what says it already went out. */
export function expenseStanding(expense: ContractExpense): {
  tone: StatusTone
  label: string
} {
  if (expense.invoice_line_id !== null) return { tone: 'ink', label: 'In fattura' }
  if (expense.reimbursable) return { tone: 'gold', label: 'Da fatturare' }
  return { tone: 'muted', label: 'Non rimborsabile' }
}

/** An amount in the contract's own currency. `formatMoney` is euro-only, and nothing
 *  here converts: another currency shows the API's decimal string and its code. */
export function formatAmount(value: string, currency: string): string {
  return currency === 'EUR' ? formatMoney(value) : `${value} ${currency}`
}

/** What an amount field's label shows: the euro sign, or the contract's own code. */
export function currencySign(currency: string): string {
  return currency === 'EUR' ? '€' : currency
}

const capPercent = new Intl.NumberFormat('it-IT', {
  style: 'unit',
  unit: 'percent',
  maximumFractionDigits: 2,
})

/** A contract's cap on the customer's share («50.00», a percentage as the API stores it)
 *  as the page prints it. Intl reads the decimal string itself, so nothing is parsed or
 *  scaled here: the figure on screen is the one the server holds. */
export function formatCapPercent(value: string): string {
  return capPercent.format(value as `${number}`)
}

/**
 * The day or hour card in force on `start`, when one began earlier: the one a new day or
 * hour rate from `start` would overlap, and the one «chiudi il giorno prima» ends. Fee
 * cards overlap freely and are never replaced this way. A hint to the person only; the
 * server decides what it closes and the database what it refuses.
 */
export function cardInForceOn(
  cards: readonly RateCard[],
  start: string,
  except?: string,
): RateCard | undefined {
  if (start === '') return undefined
  return cards.find(
    (card) =>
      (card.type === 'daily' || card.type === 'hourly') &&
      card.id !== except &&
      card.valid_from < start &&
      (card.valid_to === null || card.valid_to >= start),
  )
}
