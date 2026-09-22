/**
 * REB-294: the table against its source.
 *
 * `lib/permissions.ts` claims every key is a core service's own
 * `require_write`/`require_admin` string, verbatim. That claim is what makes the SPA's
 * hiding trustworthy: if a key were invented, the control would vanish for a role the
 * server would have accepted (the card's forbidden half, "optimistic hiding of data"
 * in the wrong direction), and if a key drifted from the service's string, the same
 * control would stay visible past a tightening the server already enforces.
 *
 * So this reads `packages/core/src/pigrocrm/core` and checks both halves:
 *
 *  1. every key names an action the core really guards;
 *  2. the kind of guard agrees with the table's minimum: a `require_admin` string
 *     cannot be listed as `collaboratore`, and a `require_write` one not as `admin`.
 *
 * Actions the core guards but the table does not list are *not* failures: the table
 * lists what a named control on a screen consults, and the core guards more than the
 * web offers (restore endpoints, email drafts, the timer rename). What the table may
 * never hold is an action the core does not name.
 *
 * The core's two indirect strings (`_SETTINGS_ACTION`, `_ROOTS_ACTION`) resolve here
 * the way ruff resolves nothing: by reading the constant's own assignment in the file
 * that guards with it. Their values are Italian on purpose (they surface inside a
 * sentence), and they are keys here too, so the two sides stay one fact.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { MIN_ROLE, SETTINGS_TAB_MIN_ROLE, can, canSeeSettingsTab } from '@/lib/permissions'
import { SETTINGS_TABS } from '@/features/settings/tabs'
// From `apps/web/src/test` up to the project root (`projects/pigrocrm`), whose
// `packages/core` is the one every action string comes from. Derived, never absolute.
const CORE_ROOT = join(
  __dirname,
  '..',
  '..',
  '..',
  '..',
  'packages',
  'core',
  'src',
  'pigrocrm',
  'core',
)

function pyFiles(root: string): string[] {
  const out: string[] = []
  for (const entry of readdirSync(root)) {
    const path = join(root, entry)
    if (statSync(path).isDirectory()) out.push(...pyFiles(path))
    else if (entry.endsWith('.py')) out.push(path)
  }
  return out
}

/** The actions each file guards, split by kind, constants resolved to their literal. */
function guardedActions(source: string): { write: Set<string>; admin: Set<string> } {
  const write = new Set<string>()
  const admin = new Set<string>()
  // `require_write("x")` / `require_admin("x")`, and the named-argument form the routers
  // do not use but the services do: `actor.require_write(_CONST)`.
  const call = /require_(write|admin)\(\s*(?:"([^"]+)"|([A-Za-z_][A-Za-z0-9_]*))\s*\)/g
  const assignment = /([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"([^"]+)"/g
  const constants = new Map<string, string>()
  for (const [, name, value] of source.matchAll(assignment)) {
    if (name && value) constants.set(name, value)
  }
  for (const match of source.matchAll(call)) {
    const [, kind, literal, constName] = match
    const action = literal ?? (constName === undefined ? undefined : constants.get(constName))
    if (!action) continue
    ;(kind === 'write' ? write : admin).add(action)
  }
  return { write, admin }
}

const ALL = new Map<string, 'write' | 'admin'>()
for (const file of pyFiles(CORE_ROOT)) {
  const { write, admin } = guardedActions(readFileSync(file, 'utf8'))
  for (const action of write) if (!ALL.has(action)) ALL.set(action, 'write')
  for (const action of admin) ALL.set(action, 'admin')
}

describe('the permission table against the core source', () => {
  it('finds guards at all (a broken scan would pass every key below vacuously)', () => {
    expect(ALL.size).toBeGreaterThan(40)
  })

  it.each(Object.entries(MIN_ROLE))('`%s` is guarded by the core', (action) => {
    expect(ALL.has(action)).toBe(true)
  })

  it.each(Object.entries(MIN_ROLE))(
    '`%s` is guarded by the kind the table names (%s)',
    (action, minimum) => {
      // `admin` implies the action is *also* write-ish; the two calls are disjoint in
      // the core (a service picks one), so equality is the honest assertion.
      expect(ALL.get(action)).toBe(minimum === 'admin' ? 'admin' : 'write')
    },
  )

  it('never lists `readonly` as a minimum', () => {
    for (const minimum of Object.values(MIN_ROLE)) {
      expect(minimum).not.toBe('readonly')
    }
  })
})

describe('can()', () => {
  const ROLES = ['admin', 'collaboratore', 'readonly'] as const

  it.each(Object.entries(MIN_ROLE))(
    'the whole action x role matrix: `%s`',
    (action, minimum) => {
      for (const role of ROLES) {
        // The lattice: a role at or above the minimum may, below it may not.
        expect(can(role, action)).toBe(
          minimum === 'admin' ? role === 'admin' : role === 'admin' || role === 'collaboratore',
        )
      }
    },
  )

  it('answers true for an action the table does not name (absence means not role-gated)', () => {
    // One's own profile, one's own token: no row, no gate, every role.
    for (const role of ROLES) {
      expect(can(role, 'update_own_profile')).toBe(true)
    }
  })

  it('gives a readonly actor nothing in the table', () => {
    for (const action of Object.keys(MIN_ROLE)) {
      expect(can('readonly', action)).toBe(false)
    }
  })
})

describe('the settings tab visibility', () => {
  it('decides every tab in SETTINGS_TABS (the record is exhaustive, this is the drift pin)', () => {
    for (const tab of SETTINGS_TABS) {
      expect(SETTINGS_TAB_MIN_ROLE).toHaveProperty(tab.value)
    }
  })

  it('shows a non-admin exactly the profile tab', () => {
    for (const tab of SETTINGS_TABS) {
      const visible = canSeeSettingsTab('collaboratore', tab.value)
      expect(visible).toBe(tab.value === 'profile')
      // The card's rule, both roles: no Impostazioni beyond one's own profile.
      expect(canSeeSettingsTab('readonly', tab.value)).toBe(tab.value === 'profile')
    }
  })

  it('shows an admin every tab', () => {
    for (const tab of SETTINGS_TABS) {
      expect(canSeeSettingsTab('admin', tab.value)).toBe(true)
    }
  })
})
