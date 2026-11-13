import type { StatusTone } from '@/components/StatusPill'
import { formatDate, formatMoney, formatQuantity } from '@/features/invoices/format'
import type { components } from '@/lib/api-types'

/**
 * «Incarichi» in words (REB-1035, the approved brief `docs/design/2026-11-10-engagement-pages-brief.md`):
 * the engagement's state, the tabs, the sentence that says what an engagement waits for, the
 * «Rendiconto» and payment states, and the terms of a letter, all as a person reads them. Every
 * `Record` is over the API's own union, so a state added there is a compile error here and not a
 * raw English word on a screen. No figure is computed here: a name, an amount and a date are what
 * the server sent.
 */

export type Engagement = components['schemas']['EngagementRead']
export type EngagementWaiting = components['schemas']['EngagementWaiting']
export type EngagementWarning = components['schemas']['EngagementWarning']
export type EngagementBucket = 'todo' | 'ongoing' | 'closed'
export type EngagementState = 'draft' | 'awaiting_signature' | 'active' | 'concluded' | 'cancelled'
export type DaysReport = components['schemas']['EngagementDaysReport']
export type ReportMonth = components['schemas']['ReportMonth']
export type ReportDay = components['schemas']['ReportDay']
export type MonthlyReport = components['schemas']['MonthlyReportRead']
export type EngagementPayment = components['schemas']['EngagementPaymentRead']
export type EngagementPaymentPage = components['schemas']['EngagementPaymentPage']
export type Availability = components['schemas']['FreelancerAvailability']
export type AvailabilityWeek = components['schemas']['AvailabilityWeek']
export type OwnEngagement = components['schemas']['OwnEngagementRead']
export type OwnEngagementMonth = components['schemas']['OwnEngagementMonth']

/** How many engagements a list page reads, and «Mostra altri» reads again. */
export const PAGE_SIZE = 50

/** The hub's own state names (`projects/hub/apps/web/src/lib/format.ts`), with the brief's tones. */
export const STATE_LABELS: Record<EngagementState, string> = {
  draft: 'Da inviare',
  awaiting_signature: 'In attesa di firma',
  active: 'Attivo',
  concluded: 'Concluso',
  cancelled: 'Annullato',
}

export const STATE_TONES: Record<EngagementState, StatusTone> = {
  draft: 'muted',
  awaiting_signature: 'gold',
  active: 'ink',
  concluded: 'muted',
  cancelled: 'danger',
}

/** An unknown state (an API newer than this file) reads as the stored word, in the muted tone. */
export function stateLabel(state: string): string {
  return STATE_LABELS[state as EngagementState] ?? state
}

export function stateTone(state: string): StatusTone {
  return STATE_TONES[state as EngagementState] ?? 'muted'
}

/** The three tabs of the list, disjoint: what waits for a person, what is under way, what is over. */
export const TABS: ReadonlyArray<{ id: EngagementBucket; label: string }> = [
  { id: 'todo', label: 'Da fare' },
  { id: 'ongoing', label: 'In corso' },
  { id: 'closed', label: 'Chiusi' },
]

export const EMPTY = '—'

const monthName = new Intl.DateTimeFormat('it-IT', { month: 'long' })
const monthAndYear = new Intl.DateTimeFormat('it-IT', { month: 'long', year: 'numeric' })

function localDate(iso: string): Date | null {
  const [year, month, day] = iso.split('-').map(Number)
  if (year === undefined || month === undefined || day === undefined) return null
  return new Date(year, month - 1, day)
}

/** `2026-10-01` as «ottobre 2026». */
export function monthLabel(iso: string): string {
  const date = localDate(iso)
  return date === null ? iso : monthAndYear.format(date)
}

/** `2026-10-01` as «ottobre». */
export function monthOnly(iso: string): string {
  const date = localDate(iso)
  return date === null ? iso : monthName.format(date)
}

export { formatDate, formatMoney, formatQuantity }

/** «al giorno», «all'ora» or «a corpo»: what the fee is per. */
export const FEE_UNIT_WORDS: Record<string, string> = {
  day: 'al giorno',
  hour: "all'ora",
  flat: 'a corpo',
}

/** `4,5` days a week as «4,5 giorni a settimana», one day as «1 giorno a settimana». */
export function daysPerWeekWords(value: string | null): string | null {
  if (value === null) return null
  const text = formatQuantity(value)
  return `${text} ${text === '1' ? 'giorno' : 'giorni'} a settimana`
}

/** The fee over its unit and days a week: «450,00 € al giorno · 3 giorni a settimana». */
export function feeWords(
  fee: string | null,
  unit: string,
  daysPerWeek: string | null,
): string | null {
  if (fee === null) return null
  const parts = [`${formatMoney(fee)} ${FEE_UNIT_WORDS[unit] ?? unit}`]
  const days = unit === 'flat' ? null : daysPerWeekWords(daysPerWeek)
  if (days !== null) parts.push(days)
  return parts.join(' · ')
}

/**
 * The payment rule in the words the letter prints (design § 4): a fixed term from the invoice,
 * optionally from the end of the month, or so many days after the client has paid rebase. The
 * server stores the numbers; this only puts them in a sentence.
 */
export function paymentRuleWords(args: {
  payment_rule: string
  payment_days: number | null
  payment_end_of_month: boolean
  collection_days: number | null
}): string {
  if (args.payment_rule === 'on_collection') {
    return args.collection_days === null
      ? 'Dopo che il cliente ha pagato rebase'
      : `${args.collection_days} giorni dopo che il cliente ha pagato rebase`
  }
  if (args.payment_days === null) return EMPTY
  return args.payment_end_of_month
    ? `${args.payment_days} giorni dalla fattura, a fine mese`
    : `${args.payment_days} giorni dalla fattura`
}

/** One line for a closed range of dates: «dal 02/11/2026 al 29/11/2026», or «dal … » when it has no end. */
export function datesWords(startsOn: string, endsOn: string | null): string {
  return endsOn === null
    ? `dal ${formatDate(startsOn)}, senza data di fine`
    : `dal ${formatDate(startsOn)} al ${formatDate(endsOn)}`
}

/**
 * What one `waiting` entry says in a sentence (the brief's signature of a row): «Lettera pronta, non
 * inviata», «Rendiconto di ottobre ricevuto: nessuna obiezione entro il 12/11/2026». The server
 * names the month and its last day for an objection; no date is worked out here.
 */
export function waitingSentence(waiting: EngagementWaiting): string {
  switch (waiting.code) {
    case 'letter_not_created':
      return 'Lettera da scrivere'
    case 'letter_not_sent':
      return 'Lettera pronta, non inviata'
    case 'missing_days_per_week':
      return 'Mancano i giorni a settimana'
    case 'missing_client':
      return 'Manca il cliente'
    case 'report_undecided': {
      const month = waiting.month === null || waiting.month === undefined ? '' : ` di ${monthOnly(waiting.month)}`
      const until =
        waiting.tacit_on === null || waiting.tacit_on === undefined
          ? ''
          : `: nessuna obiezione entro il ${formatDate(waiting.tacit_on)}`
      return `Rendiconto${month} ricevuto${until}`
    }
  }
}

/** What a row says under its pill when nothing waits: how the state stands or how it ended. */
export function standingSentence(engagement: Pick<Engagement, 'state' | 'ends_on'>): string | null {
  switch (engagement.state) {
    case 'awaiting_signature':
      return 'Aspetta la firma del freelance'
    case 'active':
      return 'In corso'
    case 'concluded':
      return engagement.ends_on === null ? 'Concluso' : `Concluso il ${formatDate(engagement.ends_on)}`
    case 'cancelled':
      return 'Annullato'
    default:
      return null
  }
}

// ---- the «Rendiconto» of a month (the brief: «Non ancora», «Non ricevuto», «Ricevuto», «Approvato»,
// «Approvazione tacita») ---------------------------------------------------------------------

export type ReportState = 'not_yet' | 'not_received' | 'received' | 'approved' | 'tacit'

export const REPORT_LABELS: Record<ReportState, string> = {
  not_yet: 'Non ancora',
  not_received: 'Non ricevuto',
  received: 'Ricevuto',
  approved: 'Approvato',
  tacit: 'Approvazione tacita',
}

export const REPORT_TONES: Record<ReportState, StatusTone> = {
  not_yet: 'muted',
  not_received: 'muted',
  received: 'gold',
  approved: 'ink',
  tacit: 'ink',
}

/**
 * The state a month's «Rendiconto» reads as. The report says `requested`, `approved` or `tacit` once
 * rebase has it; with none, the month is «Non ancora» while it has not ended and «Non ricevuto» after.
 * `ended` is the month's own fact from the server's report (`month` before the month of `date_to`
 * is not worked out here: the caller passes whether the month is over).
 */
export function reportState(report: MonthlyReport | null, ended: boolean): ReportState {
  if (report === null) return ended ? 'not_received' : 'not_yet'
  if (report.effective_state === 'tacit') return 'tacit'
  if (report.effective_state === 'approved') return 'approved'
  return 'received'
}

// ---- what the engagement pays the freelancer -------------------------------------------------

export type PaymentView = 'forecast' | 'to_pay' | 'late' | 'paid'

export const PAYMENT_LABELS: Record<PaymentView, string> = {
  forecast: 'Previsto',
  to_pay: 'Da pagare',
  late: 'Scaduto',
  paid: 'Pagato',
}

export const PAYMENT_TONES: Record<PaymentView, StatusTone> = {
  forecast: 'muted',
  to_pay: 'gold',
  late: 'danger',
  paid: 'ink',
}

// ---- the availability of a freelancer, one cell a week ----------------------------------------

export const AVAILABILITY_LABELS: Record<AvailabilityWeek['status'], string> = {
  free: 'libera',
  committed: 'impegnata',
  unknown: 'sconosciuta',
}

export const AVAILABILITY_TONES: Record<AvailabilityWeek['status'], StatusTone> = {
  free: 'muted',
  committed: 'ink',
  unknown: 'gold',
}

// ---- the freelancer's own card -----------------------------------------------------------------

export const OWN_MONTH_LABELS: Record<OwnEngagementMonth['state'], string> = {
  in_progress: 'In corso',
  received: 'Ricevuto da rebase',
  approved: 'Approvato',
}

export const OWN_MONTH_TONES: Record<OwnEngagementMonth['state'], StatusTone> = {
  in_progress: 'muted',
  received: 'gold',
  approved: 'ink',
}
