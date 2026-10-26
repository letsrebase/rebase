import { StatusPill } from '@/components/StatusPill'
import { statoLabel, statoTone } from './labels'

/** A day's state, in the ledger's own words and the tone that state reads as. */
export function DayStatePill({ stato }: { stato: string }) {
  return <StatusPill tone={statoTone(stato)}>{statoLabel(stato)}</StatusPill>
}
