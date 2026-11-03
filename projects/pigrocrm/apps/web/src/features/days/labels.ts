import type { StatusTone } from '@/components/StatusPill'
import { formatItalianQuantity, scaledFromDecimalString } from '@/lib/decimal'
import type { components } from '@/lib/api-types'

/**
 * Wire shapes, from the generated schema and never hand-declared: `work_units/schemas.py`
 * is the source and `pnpm generate:api` tracks it. `quantity` and the rate's `amount`
 * arrive as decimal strings, and nothing here parses them into a float.
 */
export type WorkUnitRow = components['schemas']['WorkUnitRow']
export type WorkUnitDetail = components['schemas']['WorkUnitDetail']
export type WorkUnitCreateBody = components['schemas']['WorkUnitRecord']
export type WorkUnitTransition = components['schemas']['WorkUnitTransitionRead']
export type RateCard = components['schemas']['RateCardRead']
export type WorkUnitStatus = WorkUnitDetail['actions'][number]
/** A contract day as a calendar cell carries it (`DayWorkUnit`). */
export type CalendarWorkUnit = components['schemas']['DayWorkUnit']

/** The ledger's own words, one for one (`WorkUnitStatus`), in the order a day lives them. */
export const StatusLabels: Record<WorkUnitStatus, string> = {
  proposed: 'Proposta',
  approved: 'Approvata',
  worked: 'Lavorata',
  worked_without_approval: 'Lavorata senza approvazione',
  disputed: 'Contestata',
  invoiced: 'Fatturata',
  paid: 'Pagata',
  revoked: 'Revocata',
  rejected: 'Rifiutata',
  non_billable: 'Non fatturabile',
}

/**
 * The tone each state reads as, a `Record` over the enum so a state added to the API
 * breaks the compile here instead of rendering as the default. Gold is «waiting on
 * somebody» (a proposal, a day worked without the approval its contract requires: both
 * are a fact still to be settled), danger the ones that went the other way, ink the
 * settled ones.
 */
export const STATUS_TONE: Record<WorkUnitStatus, StatusTone> = {
  proposed: 'gold',
  approved: 'ink',
  worked: 'ink',
  worked_without_approval: 'gold',
  disputed: 'danger',
  invoiced: 'ink',
  paid: 'ink',
  revoked: 'danger',
  rejected: 'danger',
  non_billable: 'muted',
}

export function isStatus(value: string): value is WorkUnitStatus {
  return value in StatusLabels
}

export function statusLabel(value: string): string {
  return isStatus(value) ? StatusLabels[value] : value
}

export function statusTone(value: string): StatusTone {
  return isStatus(value) ? STATUS_TONE[value] : 'muted'
}

/**
 * What a move is called on its button and what it asks for. `reason` is what the API
 * requires (`reason`, never empty); `defaultReason` is written for the moves whose reason
 * is the act itself, so marking a day worked is one click, and a move that is a judgement
 * (contesting, withdrawing, writing off) asks the person for theirs.
 */
export interface ActionSpec {
  label: string
  /** The title of the confirmation, and the sentence under it. */
  title: string
  description: string
  /** `null` asks for a reason; a string is sent as it is when the person adds none. */
  defaultReason: string | null
  tone: 'default' | 'outline' | 'destructive'
}

export const ACTIONS: Partial<Record<WorkUnitStatus, ActionSpec>> = {
  worked: {
    label: 'Segna come lavorata',
    title: 'Segnare la giornata come lavorata?',
    description: 'La giornata risulta svolta. Se il contratto richiede l’approvazione preventiva e nessuna è collegata, resta segnata come lavorata senza approvazione.',
    defaultReason: 'Giornata svolta',
    tone: 'default',
  },
  disputed: {
    label: 'Contesta',
    title: 'Contestare la giornata?',
    description: 'Il cliente o tu la contestate. Il motivo resta nella cronologia.',
    defaultReason: null,
    tone: 'outline',
  },
  paid: {
    label: 'Segna come pagata',
    title: 'Segnare la giornata come pagata?',
    description: 'Il pagamento è arrivato. Da qui la giornata non si muove più.',
    defaultReason: 'Pagamento ricevuto',
    tone: 'default',
  },
  non_billable: {
    label: 'Non fatturabile',
    title: 'Dichiarare la giornata non fatturabile?',
    description: 'Resta registrata ma non entra in nessuna fattura. Da qui la giornata non si muove più.',
    defaultReason: null,
    tone: 'outline',
  },
  revoked: {
    label: 'Revoca',
    title: 'Revocare la giornata?',
    description: 'La giornata non conta più e la sua data torna libera. Da qui non si muove più.',
    defaultReason: null,
    tone: 'destructive',
  },
  rejected: {
    label: 'Rifiuta',
    title: 'Rifiutare la giornata?',
    description: 'Il cliente non l’ha confermata. La data torna libera. Da qui non si muove più.',
    defaultReason: null,
    tone: 'destructive',
  },
}

/**
 * The reason a transition carries, as the log shows it. A person writes the other ones
 * (a refusal, a dispute) and they are shown as written; `creazione` is the one the
 * service writes itself when a day is recorded (`WorkUnitService.record`), a key and not
 * a sentence, so it has its words here.
 */
const REASON_LABELS: Record<string, string> = {
  'creazione': 'Giornata registrata',
}

export function reasonLabel(reason: string): string {
  return (Object.hasOwn(REASON_LABELS, reason) ? REASON_LABELS[reason] : undefined) ?? reason
}

/** Who made a move, as the log's `actor` says it. */
export function actorLabel(actor: WorkUnitTransition['actor']): string {
  switch (actor.kind) {
    case 'human':
      return 'Una persona'
    case 'agent':
      return 'Un assistente'
    case 'rebase':
      return 'rebase'
    default:
      return 'Il sistema'
  }
}

export const FULL_DAY = '1.00'
export const HALF_DAY = '0.50'

/** `1.00` is a full day, `0.50` half; anything else prints as the number of days. */
export function quantityLabel(quantity: string): string {
  const scaled = scaledFromDecimalString(quantity, 2)
  if (scaled === 100) return 'Giornata intera'
  if (scaled === 50) return 'Mezza giornata'
  return `${formatItalianQuantity(quantity)} giornate`
}

/** The rate card whose validity range contains `data` (ISO dates compare as strings). */
export function activeRateCard(cards: readonly RateCard[], data: string): RateCard | null {
  return (
    cards.find((card) => card.valid_from <= data && (card.valid_to === null || data <= card.valid_to)) ??
    null
  )
}

/** Whether `card` prices a fraction of this size: `allowed_fractions` lists what it allows. */
export function allowsFraction(card: RateCard | null, quantity: string): boolean {
  if (card === null) return true
  const wanted = scaledFromDecimalString(quantity, 2)
  return card.allowed_fractions.some((allowed) => scaledFromDecimalString(allowed, 2) === wanted)
}

const UnitPhrases: Record<string, string> = {
  hour: 'all’ora',
  day: 'al giorno',
  month: 'al mese',
  year: 'all’anno',
  flat: 'a forfait',
}

/** The unit a rate card prices by, as the words after the figure: «al giorno», «all’ora». */
export function unitLabel(unit: string): string {
  return (Object.hasOwn(UnitPhrases, unit) ? UnitPhrases[unit] : undefined) ?? `per ${unit}`
}

/** The cell's contribution to its accessible name: how many days, and any flagged one. */
export function cellDaysLabel(days: readonly CalendarWorkUnit[]): string {
  if (days.length === 0) return ''
  const flagged = days.filter((day) => day.status === 'worked_without_approval').length
  const base = days.length === 1 ? '1 giornata' : `${days.length} giornate`
  return flagged > 0 ? `, ${base}, ${flagged} senza approvazione` : `, ${base}`
}
