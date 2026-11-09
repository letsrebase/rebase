import { useQuery } from '@tanstack/react-query'
import { useMemo } from 'react'
import { api, unwrap } from './api'
import type { components } from './api-types'
import { queryKeys } from './query'

/**
 * The space's modules (spec 2026-11-09, A2 and B2, REB-953): which groups of features the
 * space has on, and the one table that says which page belongs to which group.
 *
 * Visibility is decided here and nowhere else. The sidebar, the settings sections, the
 * route guard (`ModuleGate`) and the pages that point into a module all ask this file:
 * `ROUTE_MODULES` for "which module owns this path" and `useModules().on(...)` for "is it
 * on". A component never spells `deals` in an `if` of its own beyond that one question.
 *
 * Its own module and not part of `lib/auth.tsx`, for the reason `lib/permissions.ts`
 * documents: component tests mock `@/lib/auth` wholesale.
 */

export type ModuleKey = components['schemas']['ModuleRead']['key']
export type ModuleRead = components['schemas']['ModuleRead']
export type SpaceModules = components['schemas']['SpaceModulesRead']
export type SpaceProfile = NonNullable<SpaceModules['profile']>

/**
 * The module names as a person reads them, for the sentence a guard shows before the
 * server's own answer is in. The server's `label` wins once it has answered.
 */
export const MODULE_LABELS: Record<ModuleKey, string> = {
  deals: 'Deal',
  contracts: 'Contratti',
  time: 'Ore',
  costs: 'Costi',
  taxes: 'Tasse',
}

/**
 * Which module(s) own which page: the one table. A path belongs to the entry whose
 * `path` is the path itself or a prefix of it, so `/app/deal` covers the board, the list
 * and a deal's own page. An entry needing several modules exists only when all are on
 * (a surface spanning modules, spec A2).
 *
 * Only what a module owns is listed: the core (customers, people, activities, calendar,
 * reminders, proposals, invoices, documents, agents, the settings that are not a module's)
 * has no row, and `modulesOfPath` answers `[]` for it.
 */
export const ROUTE_MODULES: ReadonlyArray<{ path: string; modules: readonly ModuleKey[] }> = [
  // deals: pipeline, deals, offers.
  { path: '/app/deal', modules: ['deals'] },
  { path: '/app/settings/pipeline', modules: ['deals'] },
  // contracts: contracts, rate cards, worked days and their approvals.
  { path: '/app/contracts', modules: ['contracts'] },
  { path: '/app/days', modules: ['contracts'] },
  { path: '/app/approvals', modules: ['contracts'] },
  // time: the hours register, the timer and the rates the hours are priced with.
  { path: '/app/hours', modules: ['time'] },
  { path: '/app/settings/rates', modules: ['time'] },
  // costs: the cost categories (the costs themselves live on a deal's Ore tab, `time` and
  // `costs` together).
  { path: '/app/settings/cost-categories', modules: ['costs'] },
]

/** The modules a path needs; `[]` for a page of the core. */
export function modulesOfPath(path: string): readonly ModuleKey[] {
  const entry = ROUTE_MODULES.find(
    (candidate) => path === candidate.path || path.startsWith(`${candidate.path}/`),
  )
  return entry?.modules ?? []
}

/**
 * Which dashboard tab belongs to which module (B5): «Anno» to `taxes`, «Giornate» to
 * `contracts`, «Commerciale» to `deals`. The tabs that are the core's (Economica,
 * Scadenziario) have no row. Keyed by the tab id so `features/dashboard/search.ts` keeps
 * the list of tabs and this file the ownership.
 */
export const TAB_MODULES: Readonly<Record<string, readonly ModuleKey[]>> = {
  commercial: ['deals'],
  year: ['taxes'],
  work_units: ['contracts'],
}

/** What a page asks about the space's modules. */
export interface ModuleAccess {
  /** Whether the modules for `keys` are all on. Everything is on until the space's
   *  answer is in (or when it could not be read), so nothing flashes away or breaks. */
  on: (...keys: readonly ModuleKey[]) => boolean
  /** The first of `keys` that is off, or `undefined`. */
  firstOff: (...keys: readonly ModuleKey[]) => ModuleKey | undefined
  /** The module's name as a person reads it. */
  label: (key: ModuleKey) => string
  /** The space's profile (spec 2026-11-09, B1): `null` until chosen, and also while the
   *  first read is on its way or when it failed, which the Home reads as «company». */
  profile: SpaceProfile | null
  /** The first read is still on its way: a guard that would turn a page away waits for
   *  it, and a read that failed is not "loading" but "not known" (everything on). */
  loading: boolean
}

/** The access a space with this answer has. `undefined` is "not known": all on. */
export function moduleAccess(data: SpaceModules | undefined, loading = false): ModuleAccess {
  const enabled =
    data === undefined
      ? null
      : new Set(data.modules.filter((module) => module.enabled).map((module) => module.key))
  const firstOff = (...keys: readonly ModuleKey[]) =>
    enabled === null ? undefined : keys.find((key) => !enabled.has(key))
  return {
    on: (...keys) => firstOff(...keys) === undefined,
    firstOff,
    label: (key) => data?.modules.find((module) => module.key === key)?.label ?? MODULE_LABELS[key],
    profile: data?.profile ?? null,
    loading,
  }
}

/**
 * `GET /api/settings/modules`, once for the whole shell (every member may read it).
 *
 * Fresh for ten seconds, which is what the server's settings cache lives (`OVERRIDES_TTL_SECONDS`),
 * and refetched on window focus: a switch made in another tab or by an administrator
 * reaches this one the next time it is looked at. A failed read, or an answer that is not
 * the shape the API documents, reads as "not known" and every module as on: a page that
 * wrongly shows a part of an off module answers 404 politely, a page that wrongly hides a
 * part of an on module loses work.
 */
export function useModules(): ModuleAccess {
  const { data, isPending, errorUpdateCount } = useQuery({
    queryKey: queryKeys.modules,
    queryFn: async () => {
      const body = await unwrap(api.GET('/api/settings/modules'))
      if (!Array.isArray(body?.modules)) throw new Error('settings/modules: no list')
      return body
    },
    staleTime: 10_000,
    retry: false,
    // A read that failed is not asked again by every component that mounts afterwards (a
    // page that mounts ten of them would ask ten times, and each retry would put the query
    // back to «pending» under the others); the next window focus asks once.
    retryOnMount: false,
  })
  // «Loading» is the first read only: a refetch after a failure puts the query back to
  // pending, and a guard that waited on that would flash its pending page on every focus.
  const loading = isPending && errorUpdateCount === 0
  return useMemo(() => moduleAccess(data, loading), [data, loading])
}
