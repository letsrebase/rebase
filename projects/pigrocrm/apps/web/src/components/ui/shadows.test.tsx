import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

/**
 * The five generated components that stayed in this application when the other
 * eighteen moved into `@rebase/ui` (REB-300): avatar, calendar, command, input-group
 * and sidebar. The package's own `shapes.test.tsx` scans the directory it lives in, so
 * without this file nothing looks at these five, and the shadow rule is exactly the one
 * a `shadcn add --overwrite calendar` can undo without anybody noticing.
 */
describe('the components that stayed here', () => {
  const files = readdirSync(__dirname).filter((f) => f.endsWith('.tsx') && !f.includes('.test.'))

  it('is the five the package README names, and no more', () => {
    expect(files.sort()).toEqual([
      'avatar.tsx',
      'calendar.tsx',
      'command.tsx',
      'input-group.tsx',
      'sidebar.tsx',
    ])
  })

  it('leaves no step shadow in any of them', () => {
    // `shadow-[4px_4px_0_0_...]` is the application's signature on what floats, and it
    // arrives through a token (`--shadow-md`), never typed into a component. The one
    // arbitrary shadow allowed here is the sidebar rail's `0 0 0 1px` ring, which is a
    // border drawn as a shadow rather than a step.
    for (const name of files) {
      for (const [, value] of readFileSync(join(__dirname, name), 'utf-8').matchAll(/shadow-\[([^\]]+)\]/g)) {
        expect(value, name).toMatch(/^0_0_0_1px_/)
      }
    }
  })

  it('reads the moved primitives from the package rather than keeping a second copy', () => {
    // A sibling import among these five is fine (`command` composes `input-group`);
    // what may not come back is a local copy of one of the eighteen, or of `cn`.
    const moved =
      /from ["']@\/components\/ui\/(button|input|textarea|select|checkbox|label|badge|card|dialog|sheet|dropdown-menu|popover|tooltip|tabs|table|separator|skeleton|sonner)["']/
    for (const name of files) {
      const source = readFileSync(join(__dirname, name), 'utf-8')
      expect(source, name).not.toMatch(moved)
      expect(source, name).not.toMatch(/from ["']@\/lib\/utils["']/)
    }
  })
})
