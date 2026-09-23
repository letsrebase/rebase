import type { components } from '@/lib/api-types'
import { currentMonth } from './periodo'

/**
 * `/app/?tab=&da=&a=&base=` — the dashboard's search params, and the one function that
 * reads them back.
 *
 * The period is in the URL because a screenshot or a shared link of a dashboard with no
 * explicit period is a number with no unit (§4). This is the first route in this codebase
 * with `validateSearch`; the validator lives here rather than inside the route file so it
 * can be tested directly, and so the route file keeps exporting nothing but `Route` (which
 * is what keeps it code-split).
 */

// Two tabs since 2026-09-08: the operational one (week, backlog, signals) is gone from
// the page; its API and MCP tool remain for the agent. Economica comes first since
// 2026-09-09 (Ivan's request): the home opens on the money, the pipeline is one tab away.
export const DASHBOARD_TABS = [
  { id: 'economica', label: 'Economica' },
  { id: 'commerciale', label: 'Commerciale' },
  // Slice 8 part A (REB-329): when the money already invoiced arrives. The one tab with
  // no period, so the picker is hidden on it -- see `DashboardPage`.
  { id: 'scadenziario', label: 'Scadenziario' },
] as const

export type TabId = (typeof DASHBOARD_TABS)[number]['id']

/**
 * Which month a document's money falls in on the economic charts (ORB-133): the accrual
 * period it declares, or the money's own dates. The type is the server's, read off the
 * response it echoes, so the two readings cannot be spelled differently on the two sides.
 * Competenza first because it is the default Ivan asked for («default a competenza»).
 */
export type CashBase = components['schemas']['CashOverview']['base']

/** `label` is the switch's word, `phrase` the one the page's sentence uses («Vista
 *  economica 2026 per competenza»): the wire value is a contract, never copy. */
export const CASH_BASES: readonly { id: CashBase; label: string; phrase: string }[] = [
  { id: 'competenza', label: 'Competenza', phrase: 'per competenza' },
  { id: 'incasso', label: 'Incasso', phrase: 'per incasso' },
]

export type DashboardSearch = { tab: TabId; da: string; a: string; base: CashBase }

/**
 * A `YYYY-MM-DD` that names a day that exists.
 *
 * The shape alone is not enough: `new Date("2026-02-30")` does not fail, it rolls over to
 * 2 March, so a regex-only check would send the server a date the user never wrote — and
 * get a 422 on the home screen for a mistyped bookmark. Round-tripping through `Date` is
 * what catches that, and it costs no coercion of an API value, which is what
 * `src/test/no-browser-arithmetic.test.ts` bans in this folder.
 */
function isCalendarDate(value: unknown): value is string {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  const parsed = new Date(`${value}T00:00:00Z`)
  return !isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value
}

/**
 * Fills in rather than rejects. The dashboard is the landing page, so a bare `/app/` must
 * open on the current month, and a mistyped date must fall back rather than throw — a 404
 * on the home screen because a query parameter is missing would be absurd.
 *
 * What it deliberately does *not* decide is whether the two bounds make sense together.
 * `PeriodoQuery.resolve` answers `da > a` with a named `ValidationFailed` whose message
 * the error banner shows; re-deciding it here is how two interfaces start disagreeing, and
 * silently swapping the bounds would answer a different question from the one the link
 * asked.
 */
/**
 * The search a link into the dashboard sends when it has no period of its own to name:
 * the current month, on the economic tab -- the first one drawn.
 *
 * It exists because `search={{}}` does not typecheck. TanStack derives what `<Link>` and
 * `navigate` must supply from the validator's *return* type, not its parameter type, so
 * every link to `/app` has to name all three keys — declaring the parameter optional does
 * not change that, and was tried.
 *
 * Which turns out to be the right behaviour rather than a concession. §4 puts the period
 * in the URL precisely so that a screenshot or a shared link of a dashboard is not a
 * number without a unit; a link that arrives with an empty search leaves the address bar
 * saying nothing until the validator fills it in. Sending the resolved month makes the
 * URL true from the first paint, and there is still exactly one definition of "the
 * default period" — this function and the validator both call `currentMonth()`.
 */
export function defaultDashboardSearch(): DashboardSearch {
  const { da, a } = currentMonth()
  return { tab: 'economica', da, a, base: 'competenza' }
}

/**
 * The Home's search: the dashboard's, plus the Gmail consent flow's `esito`, which the
 * callback appends when it brings an empty space back to its start page (REB-222). A
 * string or nothing, looked up in a fixed table where it is shown (`messaggioEsito`),
 * exactly as on Impostazioni → Gmail. Optional, so a link into the dashboard never has
 * to name it.
 */
export type HomeSearch = DashboardSearch & { esito?: string }

export function validateHomeSearch(search: Record<string, unknown>): HomeSearch {
  const dashboard = validateDashboardSearch(search)
  return typeof search.esito === 'string' ? { ...dashboard, esito: search.esito } : dashboard
}

export function validateDashboardSearch(search: Record<string, unknown>): DashboardSearch {
  const fallback = currentMonth()
  const tab = DASHBOARD_TABS.find((candidate) => candidate.id === search.tab)?.id ?? 'economica'
  // The same fill-in-rather-than-reject as the tab: a link carrying `base=emissione`
  // (the P&L's reading, not this view's) opens on the default rather than on a 422.
  const base = CASH_BASES.find((candidate) => candidate.id === search.base)?.id ?? 'competenza'
  return {
    tab,
    da: isCalendarDate(search.da) ? search.da : fallback.da,
    a: isCalendarDate(search.a) ? search.a : fallback.a,
    base,
  }
}
