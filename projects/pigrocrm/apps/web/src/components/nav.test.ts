import { describe, expect, it } from 'vitest'
import { moduleAccess } from '@/lib/modules'
import { spaceModules } from '@/lib/modules.testing'
import type { ModuleKey } from '@/lib/modules'
import { visibleSections } from './nav'

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
      'Proposte',
      'Clienti',
      'Persone',
      'Deal',
      'Fatture',
      'Ore',
      'Giornate',
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

  it('drops «Giornate» with contracts off and «Ore» with time off, nothing else', () => {
    expect(labels(['contracts'])).not.toContain('Giornate')
    expect(labels(['contracts'])).toContain('Ore')
    expect(labels(['time'])).not.toContain('Ore')
    expect(labels(['time'])).toContain('Deal')
  })

  it('keeps the entries of the core whatever is off', () => {
    const core = ['Attività', 'Calendario', 'Avvisi', 'Proposte', 'Clienti', 'Persone', 'Fatture']
    expect(labels(['deals', 'contracts', 'time', 'costs', 'taxes'])).toEqual(core)
  })

  it('drops a section left with no entry', () => {
    // Vendite keeps its customers and people, so it stays; it is Amministrazione that
    // loses two of its three, and keeps «Fatture».
    const sections = visibleSections(moduleAccess(spaceModules(['deals', 'contracts'])))
    expect(sections.map((section) => section.id)).toEqual(['da-fare', 'vendite', 'amministrazione'])
    expect(sections[2]?.items.map((item) => item.label)).toEqual(['Fatture'])
  })
})
