import { formatOccurredAt } from '@/components/activityLabels'
import { formatIsoDateItalian } from '@/lib/dates'
import type { components } from '@/lib/api-types'

/**
 * What a proposal says, in words, read out of its `campi_proposti`.
 *
 * `campi_proposti` is a plain JSON object on the wire: the core validates it against
 * `ContrattoProposalFields` or `GiornataProposalFields` when the agent files it, but the
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
export type ProposalStato = 'in_attesa' | 'accettata' | 'rifiutata'
export type ProposalTargetType = 'contratto' | 'giornata'

export const STATI: readonly { id: ProposalStato; label: string }[] = [
  { id: 'in_attesa', label: 'In attesa' },
  { id: 'accettata', label: 'Accettate' },
  { id: 'rifiutata', label: 'Rifiutate' },
]

export const STATO_LABELS: Record<string, string> = {
  in_attesa: 'In attesa',
  accettata: 'Accettata',
  rifiutata: 'Rifiutata',
}

export const TARGET_LABELS: Record<string, string> = {
  contratto: 'Contratto',
  giornata: 'Giornata',
}

const RENEWAL_LABELS: Record<string, string> = {
  nessuno: 'Nessun rinnovo',
  esplicito: 'Rinnovo esplicito',
  opzione_controparte: 'Opzione della controparte',
  tacito: 'Tacito rinnovo',
}

const RATE_KIND_LABELS: Record<string, string> = {
  ricorrente_fisso: 'Canone ricorrente',
  giornaliero: 'A giornata',
  orario: 'A ore',
  una_tantum: 'Una tantum',
}

const RATE_UNIT_LABELS: Record<string, string> = {
  ora: 'ora',
  giorno: 'giorno',
  mese: 'mese',
  anno: 'anno',
  forfait: 'forfait',
}

const PERIOD_LABELS: Record<string, string> = {
  mensile: 'Mensile',
  trimestrale: 'Trimestrale',
  annuale: 'Annuale',
  una_tantum: 'Una tantum',
}

export const CHANNEL_LABELS: Record<string, string> = {
  email: 'Email',
  posta_certificata: 'PEC',
  raccomandata: 'Raccomandata',
  corriere: 'Corriere',
  altro: 'Altro',
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
 * A decimal as the text it was written in. `campi_proposti` is free JSON: the REST door
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

/** `importo` in the contract's currency; an unknown currency code prints as «12,00 XYZ». */
export function formatAmount(value: unknown, currency: unknown): string | null {
  const raw = decimal(value)
  if (raw === null) return null
  const code = text(currency) ?? 'EUR'
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
export function confidenceBand(value: string): 'alta' | 'media' | 'bassa' {
  // Compared as the decimal strings the API sends (`Numeric(3,2)`, always `0.xx` or
  // `1.00`), so no float is parsed: fixed width makes the string order the numeric one.
  if (value >= '0.80') return 'alta'
  if (value >= '0.50') return 'media'
  return 'bassa'
}

export const BAND_LABELS = { alta: 'Confidenza alta', media: 'Confidenza media', bassa: 'Confidenza bassa' } as const

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
 * A whole number as the digits it was written in. The REST door validates `campi_proposti`
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
 * `politica_spese` is an open tagged union (`{"kind": ..., ...payload}`): the tag in words
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
  const currency = text(contract.divisa) ?? 'EUR'
  // `giorni_pagamento` null is the contract inheriting the customer's own term, which is a
  // fact worth saying and not a blank to hide.
  const paymentDays = wholeNumber(contract.giorni_pagamento)
  const payment =
    paymentDays === null
      ? 'Come da condizioni del cliente'
      : `${paymentDays} giorni data fattura${contract.pagamento_fine_mese === true || contract.pagamento_fine_mese === 'true' ? ' fine mese' : ''}`
  const period = [isoDate(contract.inizio), isoDate(contract.fine) ?? 'a tempo indeterminato']
  const unit = text(rate.unita)
  // A flat fee is not «per forfait»: the other units are, and read «/ mese».
  const cadence = unit === null ? '' : unit === 'forfait' ? '(forfait)' : `/ ${RATE_UNIT_LABELS[unit] ?? unit}`
  const rateAmount = formatAmount(rate.importo, currency)
  return {
    title: text(contract.titolo) ?? 'Nuovo contratto',
    customerId: text(contract.customer_id),
    groups: [
      {
        title: 'Contratto',
        rows: rows(
          row('Titolo', text(contract.titolo)),
          row('Periodo', period[0] === null ? null : `${period[0]} – ${period[1]}`),
          row('Rinnovo', RENEWAL_LABELS[String(contract.tipo_rinnovo)] ?? text(contract.tipo_rinnovo)),
          row('Preavviso di rinnovo', days(contract.preavviso_rinnovo_giorni)),
          row('Preavviso di disdetta', days(contract.preavviso_disdetta_giorni)),
          row('Pagamento', payment),
          row('Fatturazione', text(contract.cadenza_fatturazione)),
          row('Divisa', text(contract.divisa)),
          row('Approvazione preventiva', yesNo(contract.requires_prior_approval)),
          row('Rivalsa INPS', yesNo(contract.applies_social_charge)),
          row('Politica spese', expensePolicy(contract.politica_spese)),
          row('Campi personalizzati', customFields(contract.custom_fields)),
          row('Note', text(contract.note)),
        ),
      },
      {
        title: 'Prima scheda tariffaria',
        rows: rows(
          row('Tipo', RATE_KIND_LABELS[String(rate.tipo)] ?? text(rate.tipo)),
          row('Importo', rateAmount === null ? null : `${rateAmount} ${cadence}`.trim()),
          row(
            'Validità',
            isoDate(rate.valido_da) === null
              ? null
              : `dal ${isoDate(rate.valido_da)}${isoDate(rate.valido_a) ? ` al ${isoDate(rate.valido_a)}` : ''}`,
          ),
          row('Frazioni ammesse', fractions(rate.frazioni_ammesse)),
          row('Ore minime', formatQuantity(rate.ore_minime)),
          row('Erogazione', PERIOD_LABELS[String(rate.periodo_erogazione)] ?? text(rate.periodo_erogazione)),
        ),
      },
    ],
  }
}

function dayGroups(fields: Record<string, unknown>): { title: string; groups: FieldGroup[]; contractId: string | null } {
  const approval = isRecord(fields.approvazione) ? fields.approvazione : {}
  const date = isoDate(fields.data)
  const quantity = formatQuantity(fields.quantita)
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
          row('Descrizione', text(fields.descrizione)),
        ),
      },
      {
        title: 'Approvazione del cliente',
        rows: rows(
          row('Canale', CHANNEL_LABELS[String(approval.canale)] ?? text(approval.canale)),
          row('Mittente', text(approval.mittente)),
          // `ricevuto_il` is an instant; the day is its first ten characters.
          row('Ricevuta il', isoDate(text(approval.ricevuto_il)?.slice(0, 10))),
          row('Messaggio', text(approval.message_id)),
        ),
      },
    ],
  }
}

/**
 * The proposal's fields, grouped and worded for a reader: what was proposed while it
 * waits and when it was refused, and what was **accepted** once it was, since that is what
 * the contract or the day was built from (`campi_accettati` is the proposal as the
 * reviewer left it, which an API or MCP caller may have corrected).
 */
export function describeProposal(
  proposal: Pick<Proposal, 'target_type' | 'campi_proposti' | 'campi_accettati' | 'stato'>,
): ProposalDescription {
  const fields =
    proposal.stato === 'accettata' && proposal.campi_accettati ? proposal.campi_accettati : proposal.campi_proposti
  if (proposal.target_type === 'contratto') {
    const { title, groups, customerId } = contractGroups(fields)
    return { title, groups: groups.filter((group) => group.rows.length > 0), customerId, contractId: null }
  }
  const { title, groups, contractId } = dayGroups(fields)
  return { title, groups: groups.filter((group) => group.rows.length > 0), customerId: null, contractId }
}

/** «Accettata da Lorenzo Fiore · 26 ott 2026, 14:36»: who decided and when, as far as known. */
export function decisionLine(proposal: Pick<Proposal, 'stato' | 'deciso_da' | 'deciso_il'>): string {
  const verb = proposal.stato === 'accettata' ? 'Accettata' : 'Rifiutata'
  return [
    proposal.deciso_da ? `${verb} da ${proposal.deciso_da}` : verb,
    proposal.deciso_il ? formatOccurredAt(proposal.deciso_il) : null,
  ]
    .filter((part) => part !== null)
    .join(' · ')
}

/** One line of the excerpt, for the queue: whitespace flattened, cut at `limit` characters. */
export function excerptPreview(excerpt: string, limit = 180): string {
  const flat = excerpt.replace(/\s+/g, ' ').trim()
  return flat.length <= limit ? flat : `${flat.slice(0, limit - 1).trimEnd()}…`
}
