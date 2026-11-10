import { describe, expect, it } from 'vitest'
import { moduleAccess } from '@/lib/modules'
import { spaceModules } from '@/lib/modules.testing'
import type { ModuleKey } from '@/lib/modules'
import { isNavItemActive, phoneMoreItems, phoneThirdPlace, visibleSections, type NavGate } from './nav'

/** The labels the sidebar draws for a space whose modules in `off` are switched off. */
function labels(off: readonly ModuleKey[]) {
  return visibleSections(moduleAccess(spaceModules(off)), 'admin').flatMap((section) =>
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
      "Persone dell'azienda",
      'Esporta per il commercialista',
    ]
    expect(labels([])).toEqual(everything)
    expect(visibleSections(moduleAccess(undefined), 'admin').flatMap((s) => s.items.map((i) => i.label))).toEqual(
      everything,
    )
  })

  it('draws «IVA» after «Acquisti» only when its gate holds, and drops it with purchases off (REB-1024)', () => {
    const gates = new Set<NavGate>(['iva'])
    const shown = (open: ReadonlySet<NavGate>, off: readonly ModuleKey[] = []) =>
      visibleSections(moduleAccess(spaceModules(off)), 'admin', open).flatMap((section) =>
        section.items.map((item) => item.label),
      )
    expect(shown(new Set())).not.toContain('IVA')
    const open = shown(gates)
    expect(open.slice(open.indexOf('Acquisti'), open.indexOf('Acquisti') + 3)).toEqual([
      'Acquisti',
      'IVA',
      'Ore',
    ])
    expect(shown(gates, ['purchases'])).not.toContain('IVA')
    expect(
      phoneMoreItems(moduleAccess(spaceModules([])), 'admin', gates).map((item) => item.label),
    ).toContain('IVA')
    expect(phoneMoreItems(moduleAccess(spaceModules([])), 'admin').map((item) => item.label)).not.toContain(
      'IVA',
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
    const sections = visibleSections(moduleAccess(undefined), 'admin')
    expect(sections.find((section) => section.id === 'amministrazione')?.items.map((i) => i.label)).toEqual([
      'Fatture',
      'Fornitori',
      'Acquisti',
      'Ore',
      'Giornate',
      'Tasse',
      "Persone dell'azienda",
      'Esporta per il commercialista',
    ])
    expect(sections.find((section) => section.id === 'vendite')?.items[0]?.label).toBe('Clienti')
  })

  it('opens the registry on the supplier view for «Fornitori» and on the default one for «Clienti»', () => {
    const items = visibleSections(moduleAccess(undefined), 'admin').flatMap((section) => section.items)
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

  it("draws «Persone dell'azienda» to an administrator only, and not with staff off (REB-1027)", () => {
    const access = moduleAccess(undefined)
    const names = (role: 'admin' | 'collaborator' | 'readonly' | undefined) =>
      visibleSections(access, role).flatMap((section) => section.items.map((item) => item.label))
    expect(names('admin')).toContain("Persone dell'azienda")
    for (const role of ['collaborator', 'readonly', undefined] as const) {
      expect(names(role)).not.toContain("Persone dell'azienda")
    }
    expect(labels(['staff'])).not.toContain("Persone dell'azienda")
    expect(phoneMoreItems(access, 'collaborator').map((item) => item.label)).not.toContain("Persone dell'azienda")
    expect(phoneMoreItems(access, 'admin').map((item) => item.label)).toContain("Persone dell'azienda")
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
      'Esporta per il commercialista',
    ]
    expect(labels(['deals', 'contracts', 'time', 'purchases', 'taxes', 'staff'])).toEqual(core)
  })

  it('drops a section left with no entry', () => {
    // Vendite keeps its organizations and people, so it stays; it is Amministrazione that
    // loses two of its four, and keeps «Fatture», «Tasse» and the export.
    const sections = visibleSections(moduleAccess(spaceModules(['deals', 'contracts'])), 'admin')
    expect(sections.map((section) => section.id)).toEqual(['da-fare', 'vendite', 'amministrazione'])
    expect(sections[2]?.items.map((item) => item.label)).toEqual([
      'Fatture',
      'Fornitori',
      'Acquisti',
      'Tasse',
      "Persone dell'azienda",
      'Esporta per il commercialista',
    ])
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
      phoneMoreItems(moduleAccess(spaceModules(off, profile)), 'admin').map((item) => item.label)
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
      "Persone dell'azienda",
      'Esporta per il commercialista',
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
      "Persone dell'azienda",
      'Esporta per il commercialista',
    ])
  })
})

describe('«Esporta per il commercialista» is an administrator\'s entry (REB-1028)', () => {
  const access = moduleAccess(undefined)
  const labelsFor = (role: 'admin' | 'collaborator' | 'readonly' | undefined) =>
    visibleSections(access, role).flatMap((section) => section.items.map((item) => item.label))

  it('is drawn for an administrator, last in «Amministrazione», and for nobody else', () => {
    const admin = visibleSections(access, 'admin').find((section) => section.id === 'amministrazione')
    expect(admin?.items.at(-1)?.label).toBe('Esporta per il commercialista')
    for (const role of ['collaborator', 'readonly', undefined] as const) {
      expect(labelsFor(role)).not.toContain('Esporta per il commercialista')
      expect(labelsFor(role)).toContain('Tasse')
    }
  })

  it('is in «Altro» for an administrator and gone for the others, whatever the modules', () => {
    const more = (role: 'admin' | 'collaborator' | 'readonly') =>
      phoneMoreItems(moduleAccess(spaceModules(['deals', 'contracts', 'time', 'purchases', 'taxes'])), role).map(
        (item) => item.label,
      )
    expect(more('admin')).toContain('Esporta per il commercialista')
    expect(more('collaborator')).not.toContain('Esporta per il commercialista')
    expect(more('readonly')).not.toContain('Esporta per il commercialista')
  })
})

describe('isNavItemActive: one registry page, two entries', () => {
  const items = visibleSections(moduleAccess(undefined), 'admin').flatMap((section) => section.items)
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

  it('follows the view on an organization\'s own page: «Clienti» by default, «Fornitori» for a supplier (REB-1038)', () => {
    expect(isNavItemActive(customers, '/app/organizations/abc', undefined)).toBe(true)
    expect(isNavItemActive(suppliers, '/app/organizations/abc', undefined)).toBe(false)
    expect(isNavItemActive(suppliers, '/app/organizations/abc', 'supplier')).toBe(true)
    expect(isNavItemActive(customers, '/app/organizations/abc', 'supplier')).toBe(false)
  })

  it('marks nothing on another page', () => {
    expect(isNavItemActive(customers, '/app/invoices', undefined)).toBe(false)
  })
})
