import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { Button } from './button'
import { Card } from './card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from './table'

/**
 * The shapes the 2026-09-08 revision gives the primitives that read `--radius`,
 * `--border` and the shadow tokens (design spec §3, §4). Class lists, not computed
 * styles: jsdom loads no Tailwind stylesheet, and these classes are what the feature
 * code and the Playwright checks build on.
 */
describe('Table', () => {
  function renderTable() {
    return render(
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Cliente</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <TableRow>
            <TableCell>Acme</TableCell>
          </TableRow>
        </TableBody>
      </Table>,
    )
  }

  it('closes the header on the record\'s 2px rule, 12px muted and lightly tracked', () => {
    const { container } = renderTable()
    const head = container.querySelector('[data-slot="table-head"]')!
    expect(head.className).toContain('text-xs')
    expect(head.className).toContain('text-muted-foreground')
    expect(head.className).toContain('tracking-wide')
    expect(head.className).not.toContain('text-foreground')
    // "a 2px rule under the header" (the application-variant record of 2026-09-18),
    // read from the width token rather than typed as a number.
    const header = container.querySelector('[data-slot="table-header"]')!
    expect(header.className).toContain('[&_tr]:border-b-(length:--line-strong)')
  })

  it('gives every row 48px, the ink separator, a column rule and a Paper hover', () => {
    const { container } = renderTable()
    for (const row of container.querySelectorAll('[data-slot="table-row"]')) {
      // 48px, the record's middle density; the 56px this pinned was the soft system's.
      expect(row.className).toContain('h-12')
      expect(row.className).toContain('border-b')
      expect(row.className).toContain('border-border')
      // Ruled: the same ink line between the cells, never on the first column's outer edge.
      expect(row.className).toContain('[&>*+*]:border-l')
      expect(row.className).toContain('[&>*+*]:border-border')
      // Paper at full strength, not `hover:bg-muted/40`: since `--muted` became Paper
      // itself (tokens.css, this pass) a 40% mix of it over the white panel is #f9fafa --
      // a hover a pointer cannot see. The spec's «hover Paper» is the whole tint.
      expect(row.className).toContain('hover:bg-muted')
      expect(row.className).not.toContain('hover:bg-muted/')
    }
  })

  it('breathes in the cells', () => {
    const { container } = renderTable()
    expect(container.querySelector('[data-slot="table-cell"]')!.className).toContain('py-3')
  })
})

describe('Button', () => {
  it('stands 36px tall at the default size and 32px at sm', () => {
    const { container } = render(
      <>
        <Button>Crea</Button>
        <Button size="sm">Filtra</Button>
      </>,
    )
    const [regular, small] = [...container.querySelectorAll('[data-slot="button"]')]
    expect(regular!.className).toContain('h-9')
    expect(small!.className).toContain('h-8')
  })

  it('reads the radius token rather than a literal corner', () => {
    const { container } = render(<Button>Crea</Button>)
    // rounded-lg resolves to --radius-lg, which is --radius: zero since the
    // application-variant record of 2026-09-18, and whatever it becomes after.
    expect(container.querySelector('[data-slot="button"]')!.className).toContain('rounded-lg')
  })

  it('keeps the primary fill on Watermelon-strong', () => {
    const { container } = render(<Button>Crea</Button>)
    expect(container.querySelector('[data-slot="button"]')!.className).toContain('bg-primary')
  })

  it('draws the secondary button as the ink line on the card', () => {
    const { container } = render(<Button variant="outline">Esporta</Button>)
    const button = container.querySelector('[data-slot="button"]')!
    expect(button.className).toContain('border-border')
    expect(button.className).toContain('bg-card')
    expect(button.className).toContain('hover:bg-muted')
  })
})

describe('Card', () => {
  it('sits on the page with a line and no shadow at all', () => {
    const { container } = render(<Card>Totale</Card>)
    const card = container.querySelector('[data-slot="card"]')!
    expect(card.className).toContain('rounded-2xl')
    expect(card.className).toContain('border')
    expect(card.className).toContain('border-border')
    expect(card.className).toContain('bg-card')
    // The step shadow of the pixel system, and any replacement for it, are gone:
    // a CRM page shows many cards at once and the page would be drawn in shadows.
    expect(card.className).not.toMatch(/shadow-/)
    expect(card.className).not.toContain('ring-1')
  })
})

describe('the floating surfaces', () => {
  // Read as source, not rendered: each of these lives behind an open state and a
  // portal, and what is being pinned is the class the *component* declares.
  const source = (name: string) => readFileSync(join(__dirname, `${name}.tsx`), 'utf-8')

  it.each(['dialog', 'dropdown-menu', 'popover', 'select'])(
    '%s floats on shadow-lg, the one step shadow, and reads the radius scale',
    (name) => {
      const content = source(name)
        .split('\n')
        .filter((line) => /rounded-|shadow-/.test(line) && /bg-popover/.test(line))
      expect(content.length, `${name}: no popover surface found`).toBeGreaterThan(0)
      for (const line of content) {
        expect(line, name).toContain('rounded-xl')
        expect(line, name).toContain('shadow-lg')
      }
    },
  )

  it('leaves no step shadow anywhere in the primitives', () => {
    // `shadow-[4px_4px_0_0_...]` was the pixel system's signature, and it arrives
    // through a token now, never typed into a component. This scans the directory it
    // sits in, which since REB-300 is the package: the five generated components that
    // stayed in the CRM, the sidebar rail's `0 0 0 1px` ring among them, are scanned by
    // that application's own `components/ui/shadows.test.tsx`.
    for (const name of readdirSync(__dirname).filter((f) => f.endsWith('.tsx') && !f.includes('.test.'))) {
      for (const [, value] of readFileSync(join(__dirname, name), 'utf-8').matchAll(/shadow-\[([^\]]+)\]/g)) {
        expect(value, name).toMatch(/^0_0_0_1px_/)
      }
    }
  })
})
