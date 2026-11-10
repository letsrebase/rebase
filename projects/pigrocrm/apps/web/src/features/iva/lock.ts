import type { IvaPeriod } from './queries'

/**
 * What the person looking at a period may do about its lock (REB-1036): close it once it has ended,
 * reopen it once it is closed. Both are an administrator's with the whole space in view (the service
 * asks for an unscoped one); a scoped administrator reads the registers and is told why there is no
 * button. A period that has not ended offers nothing: the server settles a period when it is closed
 * and refuses one that is still running.
 */
export type LockMode = 'close' | 'reopen' | 'scoped' | 'none'

export function lockMode(period: IvaPeriod, canLock: boolean, scoped: boolean): LockMode {
  if (!canLock) return 'none'
  if (period.closed) return scoped ? 'none' : 'reopen'
  if (period.state !== 'ended') return 'none'
  return scoped ? 'scoped' : 'close'
}
