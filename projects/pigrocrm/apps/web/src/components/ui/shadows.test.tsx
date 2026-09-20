import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

/**
 * The generated components that stayed in this application when the other eighteen
 * moved into `@rebase/ui` (REB-300). Five stayed then; two of them, `calendar` and
 * `sidebar`, had no importer in this application and never had one, so REB-304 deleted
 * them along with `react-day-picker` and `hooks/use-mobile.ts`, which existed only for
 * those two files. What is left is what the CRM actually renders: `avatar` in the
 * shell, and `command`, which composes `input-group`, in the command palette.
 *
 * The package's own `shapes.test.tsx` scans the directory it lives in, so without this
 * file nothing looks at these three, and the shadow rule is exactly the one a
 * `shadcn add --overwrite command` can undo without anybody noticing. The application
 * no longer carries a `components.json` (REB-304), so that command has to be run in
 * `shared/ui`, which is where a new primitive belongs.
 */
describe('the components that stayed here', () => {
  const files = readdirSync(__dirname).filter((f) => f.endsWith('.tsx') && !f.includes('.test.'))

  it('is the three the package README names, and no more', () => {
    expect(files.sort()).toEqual(['avatar.tsx', 'command.tsx', 'input-group.tsx'])
  })

  it('types no shadow into any of them', () => {
    // `shadow-[4px_4px_0_0_...]` is the application's signature on what floats, and it
    // arrives through a token (`--shadow-md`), never typed into a component. Nothing
    // here floats, so no arbitrary shadow is allowed at all: the `0 0 0 1px` ring that
    // used to be the one exception left with the sidebar.
    const found = files.flatMap((name) =>
      [...readFileSync(join(__dirname, name), 'utf-8').matchAll(/shadow-\[([^\]]+)\]/g)].map(
        ([, value]) => `${name}: ${value}`,
      ),
    )
    expect(found, 'an arbitrary shadow typed into a component').toEqual([])
  })

  it('reads the moved primitives from the package rather than keeping a second copy', () => {
    // A sibling import among these three is fine (`command` composes `input-group`);
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
