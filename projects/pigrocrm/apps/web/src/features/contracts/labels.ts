import { formatMoney } from '@/features/invoices/format'
import type { StatusTone } from '@/components/StatusPill'
import type { ContractExpense, ContractState, RateCardCreateBody } from './queries'

/**
 * What the contract screens call each closed value the API stores, and the tone a state
 * reads as. Italian here because it is what the product says; the values themselves are
 * the ones `contracts/schemas.py` closes (`ContractState`, `RenewalType`, the rate card
 * literals), and a `Record` over each union makes a value added there a compile error
 * here instead of a blank label.
 */
export const STATE_LABELS: Record<ContractState, string> = {
  bozza: 'Bozza',
  attivo: 'Attivo',
  terminato: 'Terminato',
  scaduto: 'Scaduto',
}

export const STATE_TONE: Record<ContractState, StatusTone> = {
  bozza: 'muted',
  attivo: 'ink',
  terminato: 'danger',
  scaduto: 'gold',
}

export const STATES = Object.keys(STATE_LABELS) as ContractState[]

/** The states a contract can move to from `stato`: all but the one it is in. */
export function nextStates(stato: string): ContractState[] {
  return STATES.filter((state) => state !== stato)
}

export function stateLabel(stato: string): string {
  return stato in STATE_LABELS ? STATE_LABELS[stato as ContractState] : stato
}

export function stateTone(stato: string): StatusTone {
  return stato in STATE_TONE ? STATE_TONE[stato as ContractState] : 'muted'
}

export const RENEWAL_TYPES = ['nessuno', 'esplicito', 'opzione_controparte', 'tacito'] as const
export type RenewalType = (typeof RENEWAL_TYPES)[number]

export const RENEWAL_LABELS: Record<RenewalType, string> = {
  nessuno: 'Nessun rinnovo',
  esplicito: 'Rinnovo esplicito',
  opzione_controparte: 'Opzione della controparte',
  tacito: 'Rinnovo tacito',
}

export function renewalLabel(value: string): string {
  return value in RENEWAL_LABELS ? RENEWAL_LABELS[value as RenewalType] : value
}

/** `cadenza_fatturazione` has no CHECK in the database (the value set is the
 *  engagement's own to pin, `contracts/models.py`), so the form offers these four and
 *  keeps whatever else a contract already holds. */
export const CADENCES = ['mensile', 'trimestrale', 'annuale', 'a_completamento'] as const

export const CADENCE_LABELS: Record<(typeof CADENCES)[number], string> = {
  mensile: 'Mensile',
  trimestrale: 'Trimestrale',
  annuale: 'Annuale',
  a_completamento: 'A completamento',
}

export function cadenceLabel(value: string): string {
  return value in CADENCE_LABELS ? CADENCE_LABELS[value as (typeof CADENCES)[number]] : value
}

/** The three shapes of `politica_spese`, tagged by `kind`
 *  (`contract_expenses/models.py`). `importo_tetto` rides only on the capped one;
 *  `richiede_preautorizzazione` on the two that reimburse. */
export const EXPENSE_POLICY_KINDS = [
  'non_rimborsabile',
  'rimborsabile',
  'rimborsabile_con_tetto',
] as const
export type ExpensePolicyKind = (typeof EXPENSE_POLICY_KINDS)[number]

export const EXPENSE_POLICY_LABELS: Record<ExpensePolicyKind, string> = {
  non_rimborsabile: 'Non rimborsate',
  rimborsabile: 'Rimborsate a costo',
  rimborsabile_con_tetto: 'Rimborsate fino a un tetto',
}

export interface ExpensePolicy {
  kind: ExpensePolicyKind
  importo_tetto?: string
  richiede_preautorizzazione?: boolean
}

/** Reads the open JSONB back as one of the three shapes, or `null` for a document the
 *  form did not write (an agent's `{"tipo": ...}`): shown as stated, never guessed. */
export function readExpensePolicy(raw: Record<string, unknown>): ExpensePolicy | null {
  const kind = raw.kind
  if (!EXPENSE_POLICY_KINDS.includes(kind as ExpensePolicyKind)) return null
  return {
    kind: kind as ExpensePolicyKind,
    importo_tetto:
      typeof raw.importo_tetto === 'string' || typeof raw.importo_tetto === 'number'
        ? String(raw.importo_tetto)
        : undefined,
    richiede_preautorizzazione:
      typeof raw.richiede_preautorizzazione === 'boolean' ? raw.richiede_preautorizzazione : undefined,
  }
}

export function expensePolicyBody(policy: ExpensePolicy): Record<string, unknown> {
  if (policy.kind === 'non_rimborsabile') return { kind: policy.kind }
  const body: Record<string, unknown> = {
    kind: policy.kind,
    richiede_preautorizzazione: policy.richiede_preautorizzazione === true,
  }
  if (policy.kind === 'rimborsabile_con_tetto') body.importo_tetto = policy.importo_tetto
  return body
}

export const RATE_TIPI: Record<RateCardCreateBody['tipo'], string> = {
  ricorrente_fisso: 'Canone ricorrente',
  giornaliero: 'Giornaliera',
  orario: 'Oraria',
  una_tantum: 'Una tantum',
}

export const RATE_UNITA: Record<RateCardCreateBody['unita'], string> = {
  ora: 'ora',
  giorno: 'giorno',
  mese: 'mese',
  anno: 'anno',
  forfait: 'forfait',
}

export const RATE_PERIODI: Record<NonNullable<RateCardCreateBody['periodo_erogazione']>, string> = {
  mensile: 'Mensile',
  trimestrale: 'Trimestrale',
  annuale: 'Annuale',
  una_tantum: 'Una tantum',
}

/** Where an expense stands in the cycle to the client. `rimborsabile` is the database's
 *  own verdict (the contract's policy against this expense's authorisation), never
 *  recomputed here, and `invoice_line_id` is what says it already went out. */
export function expenseStanding(expense: ContractExpense): {
  tone: StatusTone
  label: string
} {
  if (expense.invoice_line_id !== null) return { tone: 'ink', label: 'In fattura' }
  if (expense.rimborsabile) return { tone: 'gold', label: 'Da fatturare' }
  return { tone: 'muted', label: 'Non rimborsabile' }
}

/** An amount in the contract's own currency. `formatMoney` is euro-only, and nothing
 *  here converts: another currency shows the API's decimal string and its code. */
export function formatAmount(value: string, divisa: string): string {
  return divisa === 'EUR' ? formatMoney(value) : `${value} ${divisa}`
}

/** What an amount field's label shows: the euro sign, or the contract's own code. */
export function currencySign(divisa: string): string {
  return divisa === 'EUR' ? '€' : divisa
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
