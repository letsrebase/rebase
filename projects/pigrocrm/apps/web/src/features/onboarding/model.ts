import type { components } from '@/lib/api-types'
import type { ModuleKey } from '@/lib/modules'

/**
 * The onboarding's pure model (spec 2026-11-09, B3, REB-957): what each answer means for
 * the fiscal pack and for the modules, with no rendering. `Onboarding` draws it and calls
 * `set_space_profile` / `set_space_modules`' REST routes; the rules of which module may be
 * on without which live here so the screen cannot offer a state the server would refuse.
 */

export type Profile = components['schemas']['SpaceProfileSet']['profile']
export type Proposal = components['schemas']['SpaceProfileProposal']
export type FiscalPack = 'it-flat-rate' | 'it-standard' | 'it-company'

/** The regimes a profile offers on step 2. A solo space runs the flat-rate or the ordinary
 *  regime; a company is an srl on its own pack, `it-company` (REB-1005), which invoices like the
 *  ordinary regime (RF01) and owes its own taxes. Other forms are shown as «presto». */
export const REGIMES: Record<Profile, readonly { pack: FiscalPack; label: string; hint: string }[]> = {
  solo: [
    {
      pack: 'it-flat-rate',
      label: 'Forfettario',
      hint: 'Fatture senza IVA, con la tassa sostitutiva sul fatturato.',
    },
    {
      pack: 'it-standard',
      label: 'Ordinario',
      hint: 'Fatture con IVA al 22%.',
    },
  ],
  company: [
    {
      pack: 'it-company',
      label: 'Srl',
      hint: 'Fatture con IVA al 22%.',
    },
  ],
}

/** Company forms that are not built yet: listed so nobody wonders, and not selectable. */
export const COMING_FORMS = ['SpA', 'Snc', 'Sas'] as const

/** Whether `module` can be on given the others: `time` needs `deals`. The server refuses the
 *  state otherwise (`_check_dependencies`), so the screen never builds it. */
export function withDependencies(
  state: Record<string, boolean>,
  requires: Record<string, readonly string[]>,
): Record<string, boolean> {
  const next = { ...state }
  for (const [key, needs] of Object.entries(requires)) {
    if (next[key] && needs.some((need) => !next[need])) next[key] = false
  }
  return next
}

/** The modules the proposal ends with when nothing is edited: today's state with the
 *  proposal's changes applied. */
export function proposedState(proposal: Proposal): Record<ModuleKey, boolean> {
  const state = {} as Record<ModuleKey, boolean>
  for (const module of proposal.state.modules) state[module.key] = module.enabled
  for (const change of proposal.changes) state[change.module] = change.becomes
  return state
}

/** The `modules` edit of a confirmed call: only what differs from the profile's own
 *  defaults, which is how the server reads it (a module not named takes the default). */
export function editsAgainst(
  proposal: Proposal,
  wanted: Record<ModuleKey, boolean>,
): Partial<Record<ModuleKey, boolean>> {
  const proposed = proposedState(proposal)
  const edits: Partial<Record<ModuleKey, boolean>> = {}
  for (const key of Object.keys(wanted) as ModuleKey[]) {
    if (wanted[key] !== proposed[key]) edits[key] = wanted[key]
  }
  return edits
}

/** The modules that hold records and are about to go off: what the confirmation names. */
export function hiddenWithData(
  proposal: Proposal,
  wanted: Record<ModuleKey, boolean>,
): { module: ModuleKey; label: string; records: number }[] {
  return proposal.state.modules
    .filter((module) => module.records > 0 && module.enabled && !wanted[module.key])
    .map((module) => ({ module: module.key, label: module.label, records: module.records }))
}
