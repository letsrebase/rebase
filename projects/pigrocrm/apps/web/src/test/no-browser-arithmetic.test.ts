/**
 * **Criterion 14.** No number is born in the browser.
 *
 * Slice 4 §14.4 describes this test as already written; it is not -- slice 4 is not
 * implemented and, when this file was written, `apps/web` had no source-reading test
 * other than a regex over its own `tokens.css`. So this slice **creates** it, scoped to
 * the dashboard modules it introduces. (That CSS test now lives in `@rebase/ui`, with
 * the tokens; what reads source here is this file and
 * `components/ui/shadows.test.tsx`.)
 *
 * Deliberately out of scope, and named so nobody widens the scope without deciding to: the
 * five existing `Number()` call sites in `features/settings/FieldsPanel.tsx`,
 * `features/settings/PipelinePanel.tsx`, `features/deals/columns.tsx`,
 * `components/DynamicFieldRenderer.tsx` and `routes/app/customers/$customerId.tsx`. Each
 * coerces a position, a probability or a form input -- none is an economic field from a
 * dashboard response. If slice 4 lands a wider guard later, this file's scope is subsumed
 * and it can be deleted.
 *
 * That last exclusion is why the scope below is `features/dashboard/` plus the dashboard
 * *route file*, and not the whole of `routes/app/`: `routes/app/customers/$customerId.tsx`
 * already carries `currencyFormatter.format(Number(value))`, so a `routes/app` root would
 * fail on the day it was written and would have to be bought back with an allowlist --
 * which is a weaker guard than a scope that means what criterion 14 says, "a dashboard
 * module". Every dashboard module lives under `features/dashboard/`; `routes/app/index.tsx`
 * is the page that composes them, and Task B14 rewrites it.
 *
 * What this bans is the *coercion* of an API value into a JS number, not arithmetic as
 * such. `features/dashboard/Freshness.tsx` subtracts two instants and divides by 60 000 to
 * say how old the figures are, and `CommercialTab.tsx` divides one server-sent integer by
 * another to scale a bar: neither invents a figure, and neither could be written without
 * an operator. What no dashboard module may do is turn `"1234.56"` into `1234.56`, because
 * the number that comes back out is not the number that went in.
 *
 * The TypeScript compiler API is used rather than a regex: `Number(` inside a string
 * literal or a comment is not a call, and a regex cannot tell the difference.
 */
import { mkdtempSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import ts from 'typescript'
import { describe, expect, it } from 'vitest'

const SCOPED_ROOTS = [join(__dirname, '..', 'features', 'dashboard')]
const SCOPED_FILES = [join(__dirname, '..', 'routes', 'app', 'index.tsx')]
const FORBIDDEN_CALLS = new Set(['Number', 'parseFloat', 'parseInt'])

function sourceFiles(root: string): string[] {
  if (!statSync(root, { throwIfNoEntry: false })) return []
  const found: string[] = []
  for (const entry of readdirSync(root, { withFileTypes: true })) {
    const path = join(root, entry.name)
    if (entry.isDirectory()) found.push(...sourceFiles(path))
    else if (/\.tsx?$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name)) {
      found.push(path)
    }
  }
  return found
}

function scopedFiles(): string[] {
  return [
    ...SCOPED_ROOTS.flatMap(sourceFiles),
    ...SCOPED_FILES.filter((path) => statSync(path, { throwIfNoEntry: false })),
  ]
}

function offendingCalls(path: string): string[] {
  const text = readFileSync(path, 'utf-8')
  const source = ts.createSourceFile(path, text, ts.ScriptTarget.ESNext, true)
  const offenders: string[] = []

  const visit = (node: ts.Node): void => {
    if (ts.isCallExpression(node) && ts.isIdentifier(node.expression)) {
      if (FORBIDDEN_CALLS.has(node.expression.text)) {
        const { line } = source.getLineAndCharacterOfPosition(node.getStart())
        offenders.push(`${node.expression.text}() at line ${line + 1}`)
      }
    }
    // The unary `+x` coercion, which is the same defect written shorter.
    if (ts.isPrefixUnaryExpression(node) && node.operator === ts.SyntaxKind.PlusToken) {
      const { line } = source.getLineAndCharacterOfPosition(node.getStart())
      offenders.push(`unary + at line ${line + 1}`)
    }
    ts.forEachChild(node, visit)
  }
  visit(source)
  return offenders
}

describe('no number is born in the browser', () => {
  it('finds files to check, so the guard is not vacuous', () => {
    expect(scopedFiles().length).toBeGreaterThan(0)
  })

  it('scans the dashboard feature modules and the dashboard route, by name', () => {
    // A scope narrowed by a typo reports zero offenders forever. Naming the two modules
    // that must always be in it is what makes the count above mean something.
    const files = scopedFiles()
    expect(files.some((path) => path.endsWith(join('features', 'dashboard', 'charts.tsx')))).toBe(true)
    expect(files.some((path) => path.endsWith(join('routes', 'app', 'index.tsx')))).toBe(true)
  })

  it('never coerces an API value to a JS number in a dashboard module', () => {
    const offenders: string[] = []
    for (const file of scopedFiles()) {
      const found = offendingCalls(file)
      if (found.length > 0) offenders.push(`${file}: ${found.join(', ')}`)
    }
    expect(
      offenders,
      'the dashboards do not add anything -- every total arrives already summed ' +
        '(§13). Format the string the API sent; never parse it.',
    ).toEqual([])
  })

  it('catches each forbidden form when it is present', () => {
    // The guard proven to catch what it claims to, by running `offendingCalls` itself over
    // a synthetic module. Re-walking a copy of the visitor here instead would assert that
    // the copy works and leave the real function untested.
    const directory = mkdtempSync(join(tmpdir(), 'no-browser-arithmetic-'))
    try {
      const cases = [
        'const total = Number(row.valore_totale)',
        'const total = parseFloat(row.valore_totale)',
        'const total = parseInt(row.valore_totale, 10)',
        'const total = +row.valore_totale',
      ]
      for (const code of cases) {
        const probe = join(directory, 'probe.ts')
        writeFileSync(probe, code, 'utf-8')
        expect(offendingCalls(probe), code).toHaveLength(1)
      }
      // ...and proven not to fire on what it must allow, so it never becomes the reason a
      // component stops clamping a server-computed ratio.
      const allowed = join(directory, 'allowed.ts')
      writeFileSync(
        allowed,
        [
          'const clamped = Math.max(0, Math.min(1, ratio))',
          'const width = `${(clamped * 100).toFixed(2)}%`',
          'const joined = "Number(" + label',
          '// Number(row.totale) in a comment is not a call',
        ].join('\n'),
        'utf-8',
      )
      expect(offendingCalls(allowed)).toEqual([])
    } finally {
      rmSync(directory, { recursive: true, force: true })
    }
  })
})
