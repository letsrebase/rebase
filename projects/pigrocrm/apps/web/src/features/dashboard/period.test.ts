/**
 * The period, and the period as it survives a round trip through the URL.
 *
 * §4 puts the period in the URL because a screenshot or a shared link of a dashboard with
 * no explicit period is a number with no unit. That only means something if the page reads
 * it back, so the reading is what these tests are about.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { currentMonth, presetQuarter, presetYear } from './period'
import { DASHBOARD_TABS, validateDashboardSearch } from './search'

describe('period presets', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('gives the current month from the first to the last day', () => {
    vi.setSystemTime(new Date(2026, 1, 14, 12, 0, 0))
    expect(currentMonth()).toEqual({ from: '2026-02-01', to: '2026-02-28' })
  })

  it('finds February in a leap year without a leap-year table', () => {
    vi.setSystemTime(new Date(2024, 1, 3, 12, 0, 0))
    expect(currentMonth()).toEqual({ from: '2024-02-01', to: '2024-02-29' })
  })

  it('builds the period from the local calendar, not from UTC', () => {
    // 1 March, 00:30 local. `new Date().toISOString()` would say 28 February anywhere
    // east of Greenwich and the dashboard would open on the wrong month -- the same
    // defect the plan names for `new Date("YYYY-MM-DD")`, in the other direction.
    vi.setSystemTime(new Date(2026, 2, 1, 0, 30, 0))
    expect(currentMonth()).toEqual({ from: '2026-03-01', to: '2026-03-31' })
  })

  it.each([
    [new Date(2026, 0, 15), { from: '2026-01-01', to: '2026-03-31' }],
    [new Date(2026, 4, 15), { from: '2026-04-01', to: '2026-06-30' }],
    [new Date(2026, 8, 15), { from: '2026-07-01', to: '2026-09-30' }],
    [new Date(2026, 11, 31), { from: '2026-10-01', to: '2026-12-31' }],
  ])('gives the calendar quarter containing %s', (now, expected) => {
    vi.setSystemTime(now)
    expect(presetQuarter()).toEqual(expected)
  })

  it('gives the calendar year', () => {
    vi.setSystemTime(new Date(2026, 6, 4))
    expect(presetYear()).toEqual({ from: '2026-01-01', to: '2026-12-31' })
  })
})

describe('validateDashboardSearch', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date(2026, 2, 15, 9, 0, 0))
  })
  afterEach(() => vi.useRealTimers())

  it('reads back exactly what a shared link carried', () => {
    // The round trip the URL exists for. Without this the period is decoration.
    expect(
      validateDashboardSearch({ tab: 'economic', from: '2025-11-03', to: '2025-11-09', base: 'collection' }),
    ).toEqual({ tab: 'economic', from: '2025-11-03', to: '2025-11-09', base: 'collection' })
  })

  it('reads the economic charts by accrual period unless the link says otherwise', () => {
    // ORB-133: Ivan's default is «competenza»; «incasso» is the other reading, and any
    // other word falls back rather than reaching the server as a 422 on the home screen.
    expect(validateDashboardSearch({}).base).toBe('accrual')
    expect(validateDashboardSearch({ base: 'collection' }).base).toBe('collection')
    expect(validateDashboardSearch({ base: 'issue' }).base).toBe('accrual')
  })

  it('fills in the current month for a bare /app/', () => {
    // The dashboard is the landing page; a 404 on the home screen because a query
    // parameter is missing would be absurd.
    expect(validateDashboardSearch({})).toEqual({
      tab: 'economic',
      from: '2026-03-01',
      to: '2026-03-31',
      base: 'accrual',
    })
  })

  it('falls back on an unknown tab rather than rendering nothing', () => {
    expect(validateDashboardSearch({ tab: 'tax' }).tab).toBe('economic')
  })

  it.each([
    ['not-a-date'],
    ['2026-3-1'],
    ['2026-13-01'],
    // Shape-valid and calendar-invalid. `new Date("2026-02-30")` rolls over to 2 March
    // rather than failing, so a regex on the shape alone would send the server a date
    // the user never wrote and get a 422 on the home screen.
    ['2026-02-30'],
    ['2026-03-01T00:00:00Z'],
  ])('falls back on %s rather than putting it in a request', (bad) => {
    expect(validateDashboardSearch({ from: bad, to: '2026-04-30' }).from).toBe('2026-03-01')
  })

  it('keeps a valid bound when only the other one is unusable', () => {
    const search = validateDashboardSearch({ from: '2025-01-01', to: 'boh' })
    expect(search.from).toBe('2025-01-01')
    expect(search.to).toBe('2026-03-31')
  })

  it('reads the Italian words of links sent before the English rename as what they became', () => {
    expect(
      validateDashboardSearch({ tab: 'anno', da: '2025-11-03', a: '2025-11-09', base: 'incasso' }),
    ).toEqual({ tab: 'year', from: '2025-11-03', to: '2025-11-09', base: 'collection' })
  })

  it('accepts 29 February in a leap year', () => {
    expect(validateDashboardSearch({ from: '2024-02-29', to: '2024-03-01' }).from).toBe('2024-02-29')
  })

  it('leaves the ordering of the two bounds to the server', () => {
    // Validation lives in the backend (plan 1B): `PeriodQuery.resolve` answers `from > to`
    // with a named `ValidationFailed` whose message the error banner shows. Deciding it
    // here as well is how two interfaces start disagreeing -- and silently swapping the
    // bounds would answer a different question from the one the link asked.
    expect(validateDashboardSearch({ from: '2026-05-01', to: '2026-04-01' })).toEqual({
      tab: 'economic',
      base: 'accrual',
      from: '2026-05-01',
      to: '2026-04-01',
    })
  })

  it('offers the five tabs the dashboard has since REB-756', () => {
    expect(DASHBOARD_TABS.map((tab) => tab.id)).toEqual([
      'economic',
      'commercial',
      'receivables',
      'year',
      'work_units',
    ])
  })

  it('falls back to the economic tab for a link that still names the retired operational one', () => {
    expect(validateDashboardSearch({ tab: 'operational' }).tab).toBe('economic')
  })
})
