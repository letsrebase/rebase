import type { StatusTone } from '@/components/StatusPill'
import { formatItalianQuantity } from '@/lib/decimal'
import { formatIsoDateItalian, formatIsoMonthYear, toIsoMonth } from '@/lib/dates'
import type { CashItem, MemberMonth, StaffMember } from './queries'

/**
 * Words of «Persone dell'azienda» (the approved brief of 2026-11-10, REB-1027). Nothing here
 * computes a figure: every amount, date and reason is what the server sent.
 */

export const KIND_LABELS: Record<StaffMember['kind'], string> = {
  employee: 'Dipendente',
  director: 'Amministratore',
}

export const CONTRACT_TYPE_LABELS: Record<NonNullable<StaffMember['contract_type']>, string> = {
  permanent: 'Tempo indeterminato',
  fixed_term: 'Tempo determinato',
  seasonal: 'Stagionale',
  apprentice: 'Apprendistato',
  training: 'Formazione lavoro',
}

/** Where a person's month stands: «Stimato» (nothing arrived), «Ricevuto» (the inbox read a payslip
 *  and nobody accepted it), «Accettato» (a person did) and «Chiuso» (the month was locked). */
export type MonthState = 'estimated' | 'received' | 'accepted' | 'closed'

export const STATE_LABELS: Record<MonthState, string> = {
  estimated: 'Stimato',
  received: 'Ricevuto',
  accepted: 'Accettato',
  closed: 'Chiuso',
}

export const STATE_TONES: Record<MonthState, StatusTone> = {
  estimated: 'muted',
  received: 'gold',
  accepted: 'ink',
  closed: 'ink',
}

/** One line under the pill. */
export const STATE_LINES: Record<MonthState, string> = {
  estimated: 'In attesa del cedolino',
  received: 'Da accettare',
  accepted: 'Cedolino accettato',
  closed: 'Mese chiuso',
}

/** The line under a person's pill: an accepted payslip says when it was accepted. */
export function stateLine(month: MemberMonth | undefined, state: MonthState): string {
  if (state === 'accepted' && month?.payslip != null) return `Cedolino accettato il ${formatDay(month.payslip.created_at.slice(0, 10))}`
  return STATE_LINES[state]
}

/** What each cash item is called. */
export const CASH_LABELS: Record<CashItem['kind'], string> = {
  net_pay: 'Stipendio netto',
  extra_pay: 'Mensilità aggiuntiva netta',
  withholding: 'Ritenute IRPEF',
  inps_contributions: 'Contributi INPS',
  inail_premium: 'Premio INAIL',
}

/** The kinds that go in the month's F24: grouped there, never one per person. */
export const F24_KINDS: ReadonlySet<CashItem['kind']> = new Set([
  'withholding',
  'inps_contributions',
  'inail_premium',
])

/** What a part that could not be estimated is missing, one sentence each; the server names
 *  the input and this says it in words. A name this table does not know is shown as sent. */
export const NOT_ESTIMATED_REASONS: Record<string, string> = {
  accepted_payslip: 'Serve il primo cedolino accettato',
  pay_day: 'Manca il giorno di paga',
  payroll_law: 'Le date dei versamenti di questa azienda non sono ancora verificate',
  employer_inps_rate: 'Manca l’aliquota INPS a carico dell’azienda',
  employee_inps_rate: 'Manca la quota INPS a carico della persona',
  employer_inail_rate: 'Manca il tasso applicato INAIL',
  thirteenth_month: 'Manca il mese della tredicesima',
  fourteenth_month: 'Manca il mese della quattordicesima',
  fee_schedule: 'Il compenso non è mensile: manca quando viene pagato',
  fee_withholding: 'La ritenuta sul compenso è ancora da verificare',
  gestione_separata: 'I contributi della gestione separata sono ancora da verificare',
}

export function reasonOf(code: string): string {
  return NOT_ESTIMATED_REASONS[code] ?? code
}

export const MONTHS_PLACEHOLDER = '—'

/** `2026-10` as «ottobre 2026». */
export function monthName(month: string): string {
  return formatIsoMonthYear(`${month}-01`)
}

/** The same without the year, for a sentence («Costo di ottobre»). */
export function monthOnly(month: string): string {
  return monthName(month).replace(/\s\d{4}$/, '')
}

/** The month `delta` months from `month` (`YYYY-MM`). */
export function shiftMonth(month: string, delta: number): string {
  const [year, number] = month.split('-').map(Number) as [number, number]
  return toIsoMonth(new Date(year, number - 1 + delta, 1))
}

export function thisMonth(): string {
  return toIsoMonth(new Date())
}

export function formatDay(value: string): string {
  return formatIsoDateItalian(value)
}

/** «Dipendente, Commercio, livello 4, part-time 80%». */
export function termsLine(member: StaffMember): string {
  const parts: string[] = [KIND_LABELS[member.kind]]
  if (member.collective_agreement) parts.push(member.collective_agreement)
  if (member.contract_level) parts.push(`livello ${member.contract_level}`)
  if (member.contract_type) parts.push(CONTRACT_TYPE_LABELS[member.contract_type].toLowerCase())
  const share = member.part_time_percent === null ? null : formatItalianQuantity(member.part_time_percent)
  if (share !== null && share !== '100') parts.push(`part-time ${share}%`)
  return parts.join(', ')
}

const EURO_INPUT = /^\d{1,10}([.,]\d{1,2})?$/

/** What a person typed in an euro field as the decimal string the API takes, or `null` when it
 *  is not an amount. The comma is accepted because that is how it is written here. */
export function euroInput(typed: string): string | null {
  const value = typed.trim()
  return EURO_INPUT.test(value) ? value.replace(',', '.') : null
}

const NET_KINDS: ReadonlySet<CashItem['kind']> = new Set(['net_pay', 'extra_pay'])

/** The net payments of a person's month. */
export const netItems = (month: MemberMonth | undefined): CashItem[] =>
  month === undefined ? [] : month.cash.filter((item) => NET_KINDS.has(item.kind))

/** The first reason a month has no net pay, preferring the one that unlocks the rest. */
export function missingReason(month: MemberMonth | undefined): string | null {
  const codes = month?.not_estimated ?? []
  const first = codes.includes('accepted_payslip') ? 'accepted_payslip' : codes[0]
  return first === undefined ? null : reasonOf(first)
}


/** The state of a person's month: a closed month is «Chiuso» for everyone in it, whatever they were. */
export const stateOf = (month: MemberMonth | undefined, closed = false): MonthState | null =>
  month === undefined ? null : closed ? 'closed' : month.source === 'payslip' ? 'accepted' : month.received != null ? 'received' : 'estimated'

/** «A», «A e B», «A, B e un altro», «A, B e altri 3»: at most two names, then how many more. */
export function namesList(names: readonly string[]): string {
  const short = names.map((name) => (name.length > 24 ? `${name.slice(0, 23)}…` : name))
  if (short.length <= 2) return short.join(' e ')
  const rest = short.length - 2
  return `${short.slice(0, 2).join(', ')} e ${rest === 1 ? 'un altro' : `altri ${rest}`}`
}

export const NOT_IN_SERVICE = 'Non in servizio in questo mese'
