import { formatMoney as formatEuro } from '@rebase/ui/format'
import { formatOccurredAt } from '@/components/activityLabels'
import { formatIsoDateItalian } from '@/lib/dates'
import type { components } from '@/lib/api-types'

/**
 * What a proposal says, in words, read out of its `proposed_fields`.
 *
 * `proposed_fields` is a plain JSON object on the wire: the core validates it against
 * `ContractProposalFields` or `WorkUnitProposalFields` when the agent files it, but the
 * generated schema can only say `{ [key: string]: unknown }`. Everything below therefore
 * reads it defensively and shows what is there rather than assuming a shape: a proposal
 * the screen cannot describe still lists its raw fields, and Accetta stays the server's
 * call (it re-validates and answers with its own sentence).
 *
 * A plain module, not a `.tsx`, like `features/dashboard/format.ts`: no component is
 * exported, so nothing here trips `react-refresh/only-export-components`. Nothing parses
 * a number: decimals reach `Intl.NumberFormat` as the strings the API sent, which is the
 * rule `no-float-money.test.ts` holds the rest of the app to.
 */

export type Proposal = components['schemas']['ProposalRead']
export type ProposalStatus = 'pending' | 'accepted' | 'rejected'
export type ProposalTargetType = 'contract' | 'work_unit'

export const Statuses: readonly { id: ProposalStatus; label: string }[] = [
  { id: 'pending', label: 'In attesa' },
  { id: 'accepted', label: 'Accettate' },
  { id: 'rejected', label: 'Rifiutate' },
]

export const StatusLabels: Record<string, string> = {
  pending: 'In attesa',
  accepted: 'Accettata',
  rejected: 'Rifiutata',
}

export const TARGET_LABELS: Record<string, string> = {
  contract: 'Contratto',
  work_unit: 'Giornata',
}

const RENEWAL_LABELS: Record<string, string> = {
  none: 'Nessun rinnovo',
  explicit: 'Rinnovo esplicito',
  counterparty_option: 'Opzione della controparte',
  tacit: 'Tacito rinnovo',
}

const RATE_KIND_LABELS: Record<string, string> = {
  fixed_recurring: 'Canone ricorrente',
  daily: 'A giornata',
  hourly: 'A ore',
  one_off: 'Una tantum',
}

const RATE_UNIT_LABELS: Record<string, string> = {
  ora: 'ora',
  day: 'giorno',
  month: 'mese',
  year: 'anno',
  flat: 'forfait',
}

const PERIOD_LABELS: Record<string, string> = {
  monthly: 'Mensile',
  quarterly: 'Trimestrale',
  annual: 'Annuale',
  one_off: 'Una tantum',
}

export const CHANNEL_LABELS: Record<string, string> = {
  email: 'Email',
  certified_mail: 'PEC',
  registered_mail: 'Raccomandata',
  courier: 'Corriere',
  other: 'Altro',
}

export interface FieldRow {
  label: string
  value: string
}

export interface FieldGroup {
  title: string
  rows: FieldRow[]
}

export interface ProposalDescription {
  /** The heading of the row and of the detail page. */
  title: string
  /** The customer a contract proposal is for, when the fields name one. */
  customerId: string | null
  /** The contract a day proposal is for, when the fields name one. */
  contractId: string | null
  groups: FieldGroup[]
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value !== '' ? value : null
}

function isoDate(value: unknown): string | null {
  const raw = text(value)
  return raw === null ? null : formatIsoDateItalian(raw)
}

const quantityFormatter = new Intl.NumberFormat('it-IT', { maximumFractionDigits: 4 })

/**
 * A decimal as the text it was written in. `proposed_fields` is free JSON: the REST door
 * keeps a `Decimal` as the string the producer sent, while a tool call over MCP stores
 * `1` or `0.5` as a JSON number, and both are the same quantity. A number is turned back
 * into its own digits and never computed with.
 */
function decimal(value: unknown): Intl.StringNumericLiteral | null {
  if (typeof value === 'number' && Number.isFinite(value)) return String(value) as Intl.StringNumericLiteral
  return text(value) as Intl.StringNumericLiteral | null
}

/** A decimal as the number it names, without ever being computed with. */
export function formatQuantity(value: unknown): string | null {
  const raw = decimal(value)
  return raw === null ? null : quantityFormatter.format(raw)
}

/** `amount` in the contract's currency; an unknown currency code prints as «12,00 XYZ». */
export function formatAmount(value: unknown, currency: unknown): string | null {
  const raw = decimal(value)
  if (raw === null) return null
  const code = text(currency) ?? 'EUR'
  if (code === 'EUR') return formatEuro(raw)
  try {
    return new Intl.NumberFormat('it-IT', {
      style: 'currency',
      currency: code,
      useGrouping: 'always',
    }).format(raw)
  } catch {
    return `${raw.replace('.', ',')} ${code}`
  }
}

const percentFormatter = new Intl.NumberFormat('it-IT', {
  style: 'percent',
  maximumFractionDigits: 0,
})

/** The agent's declared confidence, `"0.85"`, as «85%». */
export function formatConfidence(value: string): string {
  return percentFormatter.format(value as Intl.StringNumericLiteral)
}

/** The three bands the queue reads a confidence in: a number alone says little. */
export function confidenceBand(value: string): 'alta' | 'media' | 'low' {
  // Compared as the decimal strings the API sends (`Numeric(3,2)`, always `0.xx` or
  // `1.00`), so no float is parsed: fixed width makes the string order the numeric one.
  if (value >= '0.80') return 'alta'
  if (value >= '0.50') return 'media'
  return 'low'
}

export const BAND_LABELS = { alta: 'Confidenza alta', media: 'Confidenza media', low: 'Confidenza bassa' } as const

function row(label: string, value: string | null): FieldRow | null {
  return value === null ? null : { label, value }
}

function rows(...candidates: (FieldRow | null)[]): FieldRow[] {
  return candidates.filter((candidate): candidate is FieldRow => candidate !== null)
}

function yesNo(value: unknown): string | null {
  if (value === 'true') return 'Sì'
  if (value === 'false') return 'No'
  return typeof value === 'boolean' ? (value ? 'Sì' : 'No') : null
}

/**
 * A whole number as the digits it was written in. The REST door validates `proposed_fields`
 * when it is filed but stores the JSON it was sent, so `30` and `"30"` (and `true` and
 * `"true"`) are both real and mean the same thing; MCP sends the typed one.
 */
function wholeNumber(value: unknown): string | null {
  if (typeof value === 'number' && Number.isInteger(value)) return String(value)
  return typeof value === 'string' && /^\d+$/.test(value) ? value : null
}

function days(value: unknown): string | null {
  const count = wholeNumber(value)
  return count === null ? null : `${count} ${count === '1' ? 'giorno' : 'giorni'}`
}

/** «1 giorno», «0,5 giorni»: the singular is the quantity written exactly «1». */
function quantityInDays(quantity: string | null): string | null {
  return quantity === null ? null : `${quantity} ${quantity === '1' ? 'giorno' : 'giorni'}`
}

/** A value the screen has no word for, as the JSON it is. */
function raw(value: unknown): string {
  return typeof value === 'string' ? value : JSON.stringify(value)
}

/**
 * `expense_policy` is an open tagged union (`{"kind": ..., ...payload}`): the tag in words
 * and whatever else it carries as `chiave: valore`, so a reviewer sees the whole policy a
 * contract would be created with and not only its name.
 */
function expensePolicy(value: unknown): string | null {
  if (!isRecord(value)) return null
  const { kind, ...payload } = value
  return [
    typeof kind === 'string' ? kind.replace(/_/g, ' ') : null,
    ...Object.entries(payload).map(([key, item]) => `${key.replace(/_/g, ' ')}: ${raw(item)}`),
  ]
    .filter((part) => part !== null)
    .join(' · ')
}

/** The user's own fields, which no screen can have a label for. */
function customFields(value: unknown): string | null {
  if (!isRecord(value)) return null
  const entries = Object.entries(value)
  return entries.length === 0 ? null : entries.map(([key, item]) => `${key}: ${raw(item)}`).join(' · ')
}

function fractions(value: unknown): string | null {
  if (!Array.isArray(value) || value.length === 0) return null
  return value.map((item) => formatQuantity(item) ?? raw(item)).join(', ')
}

function contractGroups(fields: Record<string, unknown>): { title: string; groups: FieldGroup[]; customerId: string | null } {
  const contract = isRecord(fields.contract) ? fields.contract : {}
  const rate = isRecord(fields.rate_card) ? fields.rate_card : {}
  const currency = text(contract.currency) ?? 'EUR'
  // `payment_days` null is the contract inheriting the customer's own term, which is a
  // fact worth saying and not a blank to hide.
  const paymentDays = wholeNumber(contract.payment_days)
  const payment =
    paymentDays === null
      ? 'Come da condizioni del cliente'
      : `${paymentDays} giorni data fattura${contract.payment_end_of_month === true || contract.payment_end_of_month === 'true' ? ' fine mese' : ''}`
  const period = [isoDate(contract.start), isoDate(contract.end) ?? 'a tempo indeterminato']
  const unit = text(rate.unit)
  // A flat fee is not «per forfait»: the other units are, and read «/ mese».
  const cadence = unit === null ? '' : unit === 'flat' ? '(forfait)' : `/ ${RATE_UNIT_LABELS[unit] ?? unit}`
  const rateAmount = formatAmount(rate.amount, currency)
  return {
    title: text(contract.title) ?? 'Nuovo contratto',
    customerId: text(contract.customer_id),
    groups: [
      {
        title: 'Contratto',
        rows: rows(
          row('Titolo', text(contract.title)),
          row('Periodo', period[0] === null ? null : `${period[0]} – ${period[1]}`),
          row('Rinnovo', RENEWAL_LABELS[String(contract.renewal_type)] ?? text(contract.renewal_type)),
          row('Preavviso di rinnovo', days(contract.renewal_notice_days)),
          row('Preavviso di disdetta', days(contract.termination_notice_days)),
          row('Pagamento', payment),
          row('Fatturazione', text(contract.billing_cadence)),
          row('Divisa', text(contract.currency)),
          row('Approvazione preventiva', yesNo(contract.requires_prior_approval)),
          row('Rivalsa INPS', yesNo(contract.applies_social_charge)),
          row('Politica spese', expensePolicy(contract.expense_policy)),
          row('Campi personalizzati', customFields(contract.custom_fields)),
          row('Note', text(contract.note)),
        ),
      },
      {
        title: 'Prima scheda tariffaria',
        rows: rows(
          row('Tipo', RATE_KIND_LABELS[String(rate.type)] ?? text(rate.type)),
          row('Importo', rateAmount === null ? null : `${rateAmount} ${cadence}`.trim()),
          row(
            'Validità',
            isoDate(rate.valid_from) === null
              ? null
              : `dal ${isoDate(rate.valid_from)}${isoDate(rate.valid_to) ? ` al ${isoDate(rate.valid_to)}` : ''}`,
          ),
          row('Frazioni ammesse', fractions(rate.allowed_fractions)),
          row('Ore minime', formatQuantity(rate.minimum_hours)),
          row('Erogazione', PERIOD_LABELS[String(rate.delivery_period)] ?? text(rate.delivery_period)),
        ),
      },
    ],
  }
}

function dayGroups(fields: Record<string, unknown>): { title: string; groups: FieldGroup[]; contractId: string | null } {
  const approval = isRecord(fields.approval) ? fields.approval : {}
  const date = isoDate(fields.date)
  const quantity = formatQuantity(fields.quantity)
  return {
    title: [date === null ? 'Giornata' : `Giornata del ${date}`, quantity === null ? null : `${quantity} gg`]
      .filter((part) => part !== null)
      .join(' · '),
    contractId: text(fields.contract_id),
    groups: [
      {
        title: 'Giornata',
        rows: rows(
          row('Data', date),
          row('Quantità', quantityInDays(quantity)),
          row('Descrizione', text(fields.description)),
        ),
      },
      {
        title: 'Approvazione del cliente',
        rows: rows(
          row('Canale', CHANNEL_LABELS[String(approval.channel)] ?? text(approval.channel)),
          row('Mittente', text(approval.sender)),
          // `received_at` is an instant; the day is its first ten characters.
          row('Ricevuta il', isoDate(text(approval.received_at)?.slice(0, 10))),
          row('Messaggio', text(approval.message_id)),
        ),
      },
    ],
  }
}

/**
 * The proposal's fields, grouped and worded for a reader: what was proposed while it
 * waits and when it was refused, and what was **accepted** once it was, since that is what
 * the contract or the day was built from (`accepted_fields` is the proposal as the
 * reviewer left it, which an API or MCP caller may have corrected).
 */
export function describeProposal(
  proposal: Pick<Proposal, 'target_type' | 'proposed_fields' | 'accepted_fields' | 'status'>,
): ProposalDescription {
  const fields =
    proposal.status === 'accepted' && proposal.accepted_fields ? proposal.accepted_fields : proposal.proposed_fields
  if (proposal.target_type === 'contract') {
    const { title, groups, customerId } = contractGroups(fields)
    return { title, groups: groups.filter((group) => group.rows.length > 0), customerId, contractId: null }
  }
  const { title, groups, contractId } = dayGroups(fields)
  return { title, groups: groups.filter((group) => group.rows.length > 0), customerId: null, contractId }
}

/** «Accettata da Lorenzo Fiore · 26 ott 2026, 14:36»: who decided and when, as far as known. */
export function decisionLine(proposal: Pick<Proposal, 'status' | 'decided_by' | 'decided_at'>): string {
  const verb = proposal.status === 'accepted' ? 'Accettata' : 'Rifiutata'
  return [
    proposal.decided_by ? `${verb} da ${proposal.decided_by}` : verb,
    proposal.decided_at ? formatOccurredAt(proposal.decided_at) : null,
  ]
    .filter((part) => part !== null)
    .join(' · ')
}

/** One line of the excerpt, for the queue: whitespace flattened, cut at `limit` characters. */
export function excerptPreview(excerpt: string, limit = 180): string {
  const flat = excerpt.replace(/\s+/g, ' ').trim()
  return flat.length <= limit ? flat : `${flat.slice(0, limit - 1).trimEnd()}…`
}
