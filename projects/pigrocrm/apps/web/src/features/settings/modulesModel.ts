import type { ModuleKey, ModuleRead } from '@/lib/modules'

/**
 * What switching one module changes beyond itself (spec 2026-11-09, A2 "Dependencies"): a
 * module cannot be on while one it needs is off, so turning one off takes with it every
 * module that needs it and is on, and turning one on takes with it the modules it needs.
 * The confirmation lists exactly these, and the call sends exactly these, so the server's
 * own dependency check never has to refuse what the page offered.
 */

/** The modules that are on and need `key`, directly or through another. */
export function dependantsOf(modules: readonly ModuleRead[], key: ModuleKey): ModuleRead[] {
  const out = new Set<ModuleKey>()
  const visit = (needed: ModuleKey) => {
    for (const module of modules) {
      if (module.enabled && module.requires.includes(needed) && !out.has(module.key)) {
        out.add(module.key)
        visit(module.key)
      }
    }
  }
  visit(key)
  return modules.filter((module) => out.has(module.key))
}

/** The modules that are off and that `key` needs, directly or through another. */
export function requirementsOf(modules: readonly ModuleRead[], key: ModuleKey): ModuleRead[] {
  const out = new Set<ModuleKey>()
  const visit = (wanted: ModuleKey) => {
    const module = modules.find((candidate) => candidate.key === wanted)
    for (const need of module?.requires ?? []) {
      const target = modules.find((candidate) => candidate.key === need)
      if (target && !target.enabled && !out.has(need)) {
        out.add(need)
        visit(need)
      }
    }
  }
  visit(key)
  return modules.filter((module) => out.has(module.key))
}

/**
 * The first dependency a set of switches breaks, as "needs": a module that would be on
 * while one it needs would be off. The profile confirmation lets the admin keep single
 * modules as they are, and the server refuses a set like this, so the page says so first.
 */
export function brokenDependency(
  modules: readonly ModuleRead[],
  state: ReadonlyMap<ModuleKey, boolean>,
): { module: ModuleRead; needs: ModuleRead } | null {
  for (const module of modules) {
    if (state.get(module.key) !== true) continue
    for (const need of module.requires) {
      const target = modules.find((candidate) => candidate.key === need)
      if (target && state.get(need) !== true) return { module, needs: target }
    }
  }
  return null
}
