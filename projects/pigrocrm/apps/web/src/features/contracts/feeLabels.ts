import type { StatusTone } from '@/components/StatusPill'
import type { components } from '@/lib/api-types'
import { formatDate } from '@/features/invoices/format'
import { RatePeriods } from './labels'

/** Wire shapes straight from the generated schema. `Decimal`s arrive as strings. */
export type FeeOccurrences = components['schemas']['FeeOccurrences']
export type FeeOccurrence = components['schemas']['FeeOccurrence']
export type FeeBilled = components['schemas']['FeeBilled']
export type FeeState = FeeOccurrence['state']

/**
 * Where an occurrence of a recurring fee stands, in the words the page uses and the tone it
 * reads as (REB-1032). A `Record` over the server's union, so a state added there is a compile
 * error here and not a blank pill. «In bozza» covers a draft invoice and a proforma alike: both
 * can still be deleted and billed again, which is what tells them from «Fatturato».
 */
export const FEE_STATE_LABELS: Record<FeeState, string> = {
  to_bill: 'Da fatturare',
  drafted: 'In bozza',
  issued: 'Fatturato',
}

export const FEE_STATE_TONE: Record<FeeState, StatusTone> = {
  to_bill: 'gold',
  drafted: 'muted',
  issued: 'ink',
}

/**
 * What the row says about the days a fee covers: «Mensile · dal 10/11/2026 al 09/12/2026», or
 * the cadence alone for a one-off fee, which covers no period. The server sends both ends, cut
 * where the card or the contract ends first, so nothing is computed here.
 */
export function feePeriodLabel(occurrence: FeeOccurrence): string {
  const cadence =
    occurrence.delivery_period in RatePeriods
      ? RatePeriods[occurrence.delivery_period as keyof typeof RatePeriods]
      : occurrence.delivery_period
  if (occurrence.period_end === null) return cadence
  return `${cadence} · dal ${formatDate(occurrence.period_start)} al ${formatDate(occurrence.period_end)}`
}
