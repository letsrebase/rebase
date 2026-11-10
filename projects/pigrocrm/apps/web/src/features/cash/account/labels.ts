import type { StatusTone } from '@/components/StatusPill'
import { formatIsoDateItalian, formatIsoMonthYear } from '@/lib/dates'
import { money } from '@/features/dashboard/format'
import { daysCount } from '../days'
import type { BankAccount, CoverageMonth, MatchItem, MatchReason, Movement, MovementMatch, Statement } from './queries'

/** The format a reader reports, as a person names it; a format nobody taught this build about
 *  is shown as the reader returned it (frame § A5: the readers' formats are «to verify»). */
const STATEMENT_FORMAT_LABELS: Record<string, string> = {
  camt053: 'CAMT.053 (XML)',
  banco_bpm_xlsx: 'Banco BPM, Saldo e movimenti (XLSX)',
}

export function statementFormatLabel(format: string): string {
  return Object.hasOwn(STATEMENT_FORMAT_LABELS, format) ? STATEMENT_FORMAT_LABELS[format]! : format
}

export const STATEMENT_STATE_LABELS: Record<Statement['state'], string> = {
  imported: 'Importato',
  proposed: 'Da confermare',
  reverted: 'Annullato',
}

export const STATEMENT_STATE_TONES: Record<Statement['state'], StatusTone> = {
  imported: 'ink',
  proposed: 'gold',
  reverted: 'muted',
}

/** The pill of a month with no statement in the statements table, beside the other states. */
export const COVERAGE_STATE_LABELS: Record<CoverageMonth['state'], string> = {
  covered: 'In regola',
  missing: 'Manca',
  pending: 'Non ancora dovuto',
}

export const COVERAGE_STATE_TONES: Record<CoverageMonth['state'], StatusTone> = {
  covered: 'ink',
  missing: 'danger',
  pending: 'muted',
}

export const MOVEMENT_STATE_LABELS: Record<Movement['state'], string> = {
  matched: 'Abbinato',
  unmatched: 'Da abbinare',
  transfer: 'Giroconto',
  ignored: 'Ignorato',
}

export const MOVEMENT_STATE_TONES: Record<Movement['state'], StatusTone> = {
  matched: 'ink',
  unmatched: 'gold',
  transfer: 'muted',
  ignored: 'muted',
}

/** The pill of a movement: an unmatched one past the server's threshold says how long it waits,
 *  by the server's `unmatched_days` and flag, never by a constant of this build. */
export function movementPill(movement: Movement): { tone: StatusTone; label: string } {
  if (movement.state === 'unmatched' && movement.is_overdue_unmatched && movement.unmatched_days !== null) {
    return { tone: 'danger', label: `Da abbinare da ${daysCount(movement.unmatched_days)}` }
  }
  return { tone: MOVEMENT_STATE_TONES[movement.state], label: MOVEMENT_STATE_LABELS[movement.state] }
}

export const ARRIVAL_LABELS: Record<BankAccount['arrival'], string> = {
  upload: 'Caricamento a mano',
  mailbox: 'Dalla casella email degli estratti conto',
  api: 'Collegamento con la banca',
}

export const CADENCE_LABELS: Record<BankAccount['cadence'], string> = {
  monthly: 'Mensile',
  quarterly: 'Trimestrale',
}

/** An IBAN in groups of four, as it is printed on a statement. */
export function groupIban(iban: string): string {
  return (
    iban
      .replace(/\s+/g, '')
      .match(/.{1,4}/g)
      ?.join(' ') ?? iban
  )
}

/** «al 30/09/2026», and «, da 41 giorni» once the server says the balance is stale. */
export function balanceDateLine(account: Pick<BankAccount, 'balance_as_of' | 'is_stale' | 'stale_days'>): string | null {
  if (account.balance_as_of === null) return null
  const date = `al ${formatIsoDateItalian(account.balance_as_of)}`
  return account.is_stale && account.stale_days !== null ? `${date}, da ${daysCount(account.stale_days)}` : date
}

/** «01/10/2026 – 31/10/2026». */
export function periodLabel(from: string, to: string): string {
  return `${formatIsoDateItalian(from)} – ${formatIsoDateItalian(to)}`
}

/** «Ottobre 2026»: the month of a period's first day, capitalised as a row title. */
export function monthTitle(periodFrom: string): string {
  const named = formatIsoMonthYear(periodFrom)
  return named.charAt(0).toUpperCase() + named.slice(1)
}

/** What a statement's check says, in the brief's words; `null` when it holds or states nothing. */
export function checkSentence(check: Statement['check']): string | null {
  if (check === null || check.ok) return null
  const difference = check.difference === null ? '' : `: differenza ${money(check.difference)}`
  if (check.kind === 'gap') {
    return `Il saldo iniziale non coincide con quello finale dell’estratto precedente${difference}`
  }
  return `Il saldo dichiarato non torna${difference}`
}

/** «1 movimento», «41 movimenti». */
export function movementsCount(count: number): string {
  return count === 1 ? '1 movimento' : `${count} movimenti`
}

/** «41 movimenti, 2 già presenti, 3 con una proposta»; the proposals are left out when none. */
export function importSentence(added: number, duplicates: number, proposed: number): string {
  const moved = movementsCount(added)
  const already = duplicates === 1 ? '1 già presente' : `${duplicates} già presenti`
  if (proposed === 0) return `${moved}, ${already}`
  return `${moved}, ${already}, ${proposed === 1 ? '1 con una proposta' : `${proposed} con una proposta`}`
}

/** The reasons a proposal was ranked, as outlined chips (never colour). */
export const REASON_LABELS: Record<MatchReason, string> = {
  exact_amount: 'importo totale coincide',
  items_total: 'le voci tornano al totale',
  document_number: 'numeri nella causale',
  known_iban: 'IBAN noto',
  counterparty_name: 'nome nella causale',
  already_marked_paid: 'già segnata come pagata',
}

/** What an item is called in a sentence: «Fattura 2026/35», else its description, else who it is for. */
export function itemTitle(item: MatchItem): string {
  if (item.document_number !== null) return `Fattura ${item.document_number}`
  return item.description ?? item.counterparty_name ?? 'Voce aperta'
}

/** «Fattura 2026/35», «Fattura 2026/35 e un’altra», «Fattura 2026/35 e altre 2». */
export function itemsSummary(items: readonly MatchItem[]): string {
  const first = items[0]
  if (first === undefined) return ''
  const others = items.length - 1
  if (others === 0) return itemTitle(first)
  return `${itemTitle(first)} e ${others === 1 ? 'un’altra' : `altre ${others}`}`
}

/**
 * The line under a movement's description: what is proposed, or what it settled. A proposal that
 * no longer fits (`is_current` false) is not shown as one.
 */
export function matchLine(match: MovementMatch | null): string | null {
  if (match === null || match.items.length === 0) return null
  if (match.state === 'confirmed') return `abbinato a ${itemsSummary(match.items)}`
  if (match.state !== 'proposed' || !match.is_current) return null
  const who = match.proposed_by_type === 'mcp' ? 'proposta dall’assistente' : 'proposta'
  return `${who}: ${itemsSummary(match.items)}`
}

export const REVERT_SENTENCE = 'Rimuove i suoi movimenti e gli abbinamenti automatici'

/**
 * Italian input as the decimal string the API stores, or `null` when it is ambiguous. A comma is
 * the decimal mark and dots are thousands («12.400,50»); without a comma, dots in groups of three
 * are thousands («12.400» is twelve thousand four hundred) and a dot before one or two digits is
 * a decimal point («12.4», «1234.50»). Anything else («12.4567») is refused rather than guessed.
 */
export function decimalString(text: string): string | null {
  const trimmed = text.trim().replace(/\s/g, '')
  if (trimmed === '') return ''
  const plain = trimmed.includes(',')
    ? trimmed.replace(/\./g, '').replace(',', '.')
    : /^-?\d{1,3}(\.\d{3})+$/.test(trimmed)
      ? trimmed.replace(/\./g, '')
      : trimmed
  return /^-?\d+(\.\d{1,2})?$/.test(plain) ? plain : null
}
