import { NOT_COMPUTABLE } from '@/features/analytics/format'
import { money } from '@/features/dashboard/format'
import {
  NOT_ONE_YET,
  STAGES,
  STAGE_LABELS,
  type Stage,
  type TaxReadingLine,
  type TaxReadings,
} from './labels'

/** What a reading cell says: the figure the server sent, «non calcolabile» for an estimate the
 *  code could not give (never `0,00 €`), and a dash for a reading that is not there yet. */
export function readingCell(line: TaxReadingLine, stage: Stage): string {
  const figure = line[stage]
  if (figure !== null && figure !== undefined) return money(figure)
  return stage === 'estimate' ? NOT_COMPUTABLE : NOT_ONE_YET
}

/** A total cell: the server's sum of one kind of reading, or the same words as a line. */
export function totalCell(readings: TaxReadings, stage: Stage): string {
  const figure = readings.totals[stage]
  if (figure !== null && figure !== undefined) return money(figure)
  return stage === 'estimate' ? NOT_COMPUTABLE : NOT_ONE_YET
}

const LEFT_OUT: Record<Stage, 'estimate_left_out' | 'declared_left_out' | 'paid_left_out'> = {
  estimate: 'estimate_left_out',
  declared: 'declared_left_out',
  paid: 'paid_left_out',
}

/** «La stima non comprende 1 riga senza cifra.»: said under a column whose total leaves lines
 *  out, so a total is never read as the sum of the whole year. Only for a column that has one. */
export function leftOutNotes(readings: TaxReadings): string[] {
  return STAGES.flatMap((stage) => {
    const left = readings.totals[LEFT_OUT[stage]]
    if (readings.totals[stage] == null || left === 0) return []
    return [
      `Il totale di «${STAGE_LABELS[stage]}» non comprende ${left === 1 ? '1 riga' : `${left} righe`} senza cifra.`,
    ]
  })
}
