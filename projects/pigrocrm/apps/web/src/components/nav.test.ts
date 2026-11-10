import { describe, expect, it } from 'vitest'
import { moduleAccess } from '@/lib/modules'
import { spaceModules } from '@/lib/modules.testing'
import type { ModuleKey } from '@/lib/modules'
import { isNavItemActive, phoneMoreItems, phoneThirdPlace, visibleSections } from './nav'

/** The labels the sidebar draws for a space whose modules in `off` are switched off. */
function labels(off: readonly ModuleKey[]) {
  return visibleSections(moduleAccess(spaceModules(off))).flatMap((section) =>
    section.items.map((item) => item.label),
  )
}

describe('visibleSections (REB-953)', () => {
  it('draws every entry while every module is on, and while the answer is not known', () => {
    const everything = [
      'Attività',
      'Calendario',
      'Avvisi',
      'In arrivo',
      'Proposte',
      'Clienti',
      'Persone',
      'Deal',
      'Fatture',
      'Fornitori',
      'Acquisti',
      'Ore',
      'Giornate',
      'Tasse',
    ]
    expect(labels([])).toEqual(everything)
    expect(visibleSections(moduleAccess(undefined)).flatMap((s) => s.items.map((i) => i.label))).toEqual(
      everything,
    )
  })

  it('drops «Deal» with deals off, and «Ore» with it, which needs the deals', () => {
    const shown = labels(['deals'])
    expect(shown).not.toContain('Deal')
    expect(shown).not.toContain('Ore')
    expect(shown).toContain('Giornate')
    expect(shown).toContain('Clienti')
  })

  it('drops «Fornitori» and «Acquisti» with purchases off, and keeps «Clienti» and «Fatture»', () => {
    const shown = labels(['purchases'])
    expect(shown).not.toContain('Fornitori')
    expect(shown).not.toContain('Acquisti')
    expect(shown).toContain('Clienti')
    expect(shown).toContain('Fatture')
  })

  it('sets «Fornitori» in «Amministrazione» before «Acquisti», and «Acquisti» after «Fatture»', () => {
    const sections = visibleSections(moduleAccess(undefined))
    expect(sections.find((section) => section.id === 'amministrazione')?.items.map((i) => i.label)).toEqual([
      'Fatture',
      'Fornitori',
      'Acquisti',
      'Ore',
      'Giornate',
      'Tasse',
    ])
    expect(sections.find((section) => section.id === 'vendite')?.items[0]?.label).toBe('Clienti')
  })

  it('opens the registry on the supplier view for «Fornitori» and on the default one for «Clienti»', () => {
    const items = visibleSections(moduleAccess(undefined)).flatMap((section) => section.items)
    expect(items.find((item) => item.label === 'Fornitori')).toMatchObject({
      to: '/app/organizations',
      search: { role: 'supplier' },
    })
    expect(items.find((item) => item.label === 'Clienti')?.search).toBeUndefined()
  })

  it('drops «Giornate» with contracts off and «Ore» with time off, nothing else', () => {
    expect(labels(['contracts'])).not.toContain('Giornate')
    expect(labels(['contracts'])).toContain('Ore')
    expect(labels(['time'])).not.toContain('Ore')
    expect(labels(['time'])).toContain('Deal')
  })

  it('drops «Tasse» with taxes off and nothing else (REB-1026)', () => {
    expect(labels([])).toContain('Tasse')
    const shown = labels(['taxes'])
    expect(shown).not.toContain('Tasse')
    expect(shown).toContain('Giornate')
    expect(shown).toContain('Fatture')
  })

  it('keeps the entries of the core whatever is off', () => {
    const core = [
      'Attività',
      'Calendario',
      'Avvisi',
      'In arrivo',
      'Proposte',
      'Clienti',
      'Persone',
      'Fatture',
    ]
    expect(labels(['deals', 'contracts', 'time', 'purchases', 'taxes'])).toEqual(core)
  })

  it('drops a section left with no entry', () => {
    // Vendite keeps its organizations and people, so it stays; it is Amministrazione that
    // loses two of its four, and keeps «Fatture» and «Tasse».
    const sections = visibleSections(moduleAccess(spaceModules(['deals', 'contracts'])))
    expect(sections.map((section) => section.id)).toEqual(['da-fare', 'vendite', 'amministrazione'])
    expect(sections[2]?.items.map((item) => item.label)).toEqual(['Fatture', 'Fornitori', 'Acquisti', 'Tasse'])
  })
})

describe('the phone bar (REB-959)', () => {
  const third = (off: readonly ModuleKey[], profile: 'solo' | 'company' | null) =>
    phoneThirdPlace(moduleAccess(spaceModules(off, profile)))?.label ?? null

  it('puts «Giornate» third for a solo space and «Deal» for a company', () => {
    expect(third([], 'solo')).toBe('Giornate')
    expect(third([], 'company')).toBe('Deal')
    expect(third([], null)).toBe('Deal')
  })

  it('falls to the other place when the first is off, and to none when both are', () => {
    expect(third(['contracts'], 'solo')).toBe('Deal')
    expect(third(['deals'], 'company')).toBe('Giornate')
    expect(third(['deals', 'contracts'], 'solo')).toBeNull()
  })

  it('lists in «Altro» what the bar does not draw, and nothing of an off module', () => {
    const more = (off: readonly ModuleKey[], profile: 'solo' | 'company' | null) =>
      phoneMoreItems(moduleAccess(spaceModules(off, profile))).map((item) => item.label)
    expect(more([], 'solo')).toEqual([
      'In arrivo',
      'Calendario',
      'Avvisi',
      'Proposte',
      'Clienti',
      'Persone',
      'Deal',
      'Fatture',
      'Fornitori',
      'Acquisti',
      'Ore',
      'Tasse',
    ])
    expect(more(['deals', 'time'], 'company')).toEqual([
      'In arrivo',
      'Calendario',
      'Avvisi',
      'Proposte',
      'Clienti',
      'Persone',
      'Fatture',
      'Fornitori',
      'Acquisti',
      'Tasse',
    ])
  })
})

describe('isNavItemActive: one registry page, two entries', () => {
  const items = visibleSections(moduleAccess(undefined)).flatMap((section) => section.items)
  const customers = items.find((item) => item.label === 'Clienti')!
  const suppliers = items.find((item) => item.label === 'Fornitori')!

  it('marks «Fornitori» on the supplier view and «Clienti» on every other view of the page', () => {
    expect(isNavItemActive(suppliers, '/app/organizations', 'supplier')).toBe(true)
    expect(isNavItemActive(customers, '/app/organizations', 'supplier')).toBe(false)
    for (const role of [undefined, 'customer', 'any']) {
      expect(isNavItemActive(customers, '/app/organizations', role)).toBe(true)
      expect(isNavItemActive(suppliers, '/app/organizations', role)).toBe(false)
    }
  })

  it('keeps «Clienti» on an organization\'s own page and never marks «Fornitori» there', () => {
    expect(isNavItemActive(customers, '/app/organizations/abc', undefined)).toBe(true)
    expect(isNavItemActive(suppliers, '/app/organizations/abc', 'supplier')).toBe(false)
  })

  it('marks nothing on another page', () => {
    expect(isNavItemActive(customers, '/app/invoices', undefined)).toBe(false)
  })
})
