import { describe, expect, it } from 'vitest'
import { moduleAccess } from '@/lib/modules'
import { spaceModules } from '@/lib/modules.testing'
import type { ModuleKey } from '@/lib/modules'
import { DASHBOARD_TABS, availableTabs, resolveTab } from './search'

const access = (off: readonly ModuleKey[]) => moduleAccess(spaceModules(off))
const ids = (off: readonly ModuleKey[]) => availableTabs(access(off)).map((tab) => tab.id)

describe('availableTabs (REB-953)', () => {
  it('has all five tabs while every module is on or the answer is not known', () => {
    expect(ids([])).toEqual(DASHBOARD_TABS.map((tab) => tab.id))
    expect(availableTabs(moduleAccess(undefined))).toHaveLength(5)
  })

  it('takes «Anno» from taxes, «Giornate» from contracts and «Commerciale» from deals', () => {
    expect(ids(['taxes'])).toEqual(['economic', 'commercial', 'receivables', 'work_units'])
    expect(ids(['contracts'])).toEqual(['economic', 'commercial', 'receivables', 'year'])
    expect(ids(['deals'])).toEqual(['economic', 'receivables', 'year', 'work_units'])
  })

  it('keeps «Economica» and «Scadenziario», the core’s, with every module off', () => {
    expect(ids(['deals', 'contracts', 'time', 'purchases', 'taxes'])).toEqual(['economic', 'receivables'])
  })
})

describe('resolveTab', () => {
  it('keeps the tab the URL names when the space shows it', () => {
    expect(resolveTab('year', access([]))).toBe('year')
    expect(resolveTab('commercial', access(['taxes']))).toBe('commercial')
  })

  it('opens the first tab that is shown when the URL names one that is not', () => {
    expect(resolveTab('year', access(['taxes']))).toBe('economic')
    expect(resolveTab('work_units', access(['contracts']))).toBe('economic')
    expect(resolveTab('commercial', access(['deals']))).toBe('economic')
  })
})
