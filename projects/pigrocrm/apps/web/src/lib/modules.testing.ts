import type { QueryClient } from '@tanstack/react-query'
import { MODULE_LABELS, type ModuleKey, type SpaceModules, type SpaceProfile } from './modules'
import { queryKeys } from './query'

/** The shared fixture of the tests that need a space with some modules off (REB-953). */

const KEYS = Object.keys(MODULE_LABELS) as ModuleKey[]

/**
 * What `GET /api/settings/modules` answers for a space whose modules in `off` are off and
 * the rest on. `time` needs `deals`, as the server computes it, so turning `deals` off turns
 * `time` off too: a test that wants the other state is not describing a space that exists.
 */
export function spaceModules(
  off: readonly ModuleKey[] = [],
  profile: SpaceProfile | null = null,
): SpaceModules {
  const dealsOff = off.includes('deals')
  return {
    profile,
    modules: KEYS.map((key) => {
      const requires: ModuleKey[] = key === 'time' ? ['deals'] : []
      return {
        key,
        label: MODULE_LABELS[key],
        description: '',
        requires,
        enabled: !off.includes(key) && !(key === 'time' && dealsOff),
        stored: null,
        default: true,
        holds_data: false,
        records: 0,
      }
    }),
  }
}

/** Puts that answer in `client`'s cache, so `useModules` has it on its first render and
 *  asks the API for nothing. */
export function seedModules(
  client: QueryClient,
  off: readonly ModuleKey[] = [],
  profile: SpaceProfile | null = null,
) {
  client.setQueryData(queryKeys.modules, spaceModules(off, profile))
}
