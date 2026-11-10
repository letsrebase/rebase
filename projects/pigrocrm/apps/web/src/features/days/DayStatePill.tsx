import { StatusPill } from '@/components/StatusPill'
import { statusLabel, statusTone } from './labels'

/** A day's state, in the ledger's own words and the tone that state reads as. */
export function DayStatePill({ status }: { status: string }) {
  return <StatusPill tone={statusTone(status)}>{statusLabel(status)}</StatusPill>
}
