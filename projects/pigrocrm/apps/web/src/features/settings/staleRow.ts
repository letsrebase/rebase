import type { ProblemDetail } from '@/lib/api'

/**
 * The refusal every whole-row settings save can meet since REB-622 (spec 2026-10-03
 * §11): the panel sent the `updated_at` its form was seeded from, the server's row has
 * another, and `PUT` answered 409 `stale_row` rather than overwriting what the other
 * admin saved. Shared by the three panels that save a whole row (`LegalEntityPanel`,
 * `FiscalPanel`, `SpacePanel`); `StaleRowBanner` is what each of them shows for it.
 */
export const STALE_ROW = 'stale_row'

export function isStaleRow(problem: ProblemDetail | null): boolean {
  return problem?.code === STALE_ROW
}

/**
 * `refused`: the save was turned down and the inputs still hold the draft. `reloaded`:
 * the person asked for the row again; the fields they had not touched now show what the
 * other admin saved, the ones they had typed in keep their draft, and the banner stays
 * until a save goes through, so the page never looks settled while it is not.
 */
export type StalePhase = 'none' | 'refused' | 'reloaded'
