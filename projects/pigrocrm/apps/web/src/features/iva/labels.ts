import type { StatusTone } from '@/components/StatusPill'
import type { IvaLadder, IvaPeriod } from './queries'

/** What a pack's result of a period comes to, in words. A `Record`, so a new outcome in the API
 *  fails the build here. */
export const OUTCOME_LABELS: Record<IvaLadder['outcome'], string> = {
  due: 'Da versare',
  carried: 'Riportato al periodo successivo',
  credit: 'A credito',
  nil: 'Nulla da versare',
}

/** The pill of a period that has not ended: an ended one wears what became of its result. */
type RunningState = Exclude<IvaPeriod['state'], 'ended'>

export const STATE_LABELS: Record<RunningState, string> = {
  future: 'Non ancora iniziato',
  open: 'Aperto',
}

export const STATE_TONES: Record<RunningState, StatusTone> = {
  future: 'muted',
  open: 'gold',
}

export const OUTCOME_TONES: Record<IvaLadder['outcome'], StatusTone> = {
  due: 'gold',
  carried: 'muted',
  credit: 'muted',
  nil: 'muted',
}

export const PAYMENT_LABELS: Record<NonNullable<IvaPeriod['payment_state']>, string> = {
  expected: 'Da versare',
  settled: 'Versato',
}

export const PAYMENT_TONES: Record<NonNullable<IvaPeriod['payment_state']>, StatusTone> = {
  expected: 'gold',
  settled: 'ink',
}

/** The pill a period wears: «Chiuso» once a person closed it (REB-1036), «Aperto» while it runs,
 *  then what became of its result (the payment state of its due item when it owes, the outcome
 *  otherwise). A period nothing settled yet says so. */
export function periodPill(period: IvaPeriod): { tone: StatusTone; label: string } {
  if (period.closed) return { tone: 'ink', label: 'Chiuso' }
  if (period.state === 'open' || period.state === 'future') {
    return { tone: STATE_TONES[period.state], label: STATE_LABELS[period.state] }
  }
  if (period.ladder === null || !period.settled) {
    return { tone: 'muted', label: 'Non ancora liquidato' }
  }
  if (period.ladder.outcome === 'due' && period.payment_state !== null) {
    return { tone: PAYMENT_TONES[period.payment_state], label: PAYMENT_LABELS[period.payment_state] }
  }
  return { tone: OUTCOME_TONES[period.ladder.outcome], label: OUTCOME_LABELS[period.ladder.outcome] }
}

export const SALES_EXIGIBILITY_LABELS = {
  immediate: 'Immediata',
  split_payment: 'Scissione',
} as const

export const NO_REGISTRATIONS = 'Nessuna registrazione in questo periodo.'
export const LOADING_SENTENCE = 'Leggo i registri…'
export const NOT_ALLOWED_SENTENCE = 'L’IVA è visibile agli amministratori.'
export const NO_ENTITY_SENTENCE =
  'Nessuna azienda di questo spazio liquida l’IVA per periodo: non ha registri IVA.'
export const NO_PERIODS_SENTENCE = 'Questa azienda non liquida l’IVA per periodo in quest’anno.'
export const NOT_THIS_ENTITY_SENTENCE =
  'L’azienda scelta nella barra laterale non liquida l’IVA per periodo.'
export const EVERY_ENTITY_SENTENCE = 'L’IVA non si somma tra aziende.'
export const PACK_SOURCE = 'dal pacchetto fiscale'
export const SCOPED_CANNOT_CLOSE_SENTENCE =
  'Solo un amministratore senza limiti all’azienda può chiudere il periodo.'
