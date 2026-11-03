/**
 * Criterion 4's second half: "no economic total is born in the browser".
 *
 * The previous system's whole P&L was computed in `App.jsx` -- three fiscal constants, float hour
 * sums, and a margin that changed depending on which of three fallback buckets happened
 * to be non-empty. This slice moves that arithmetic into `packages/core` and this test
 * is what keeps it there. Parsed with the TypeScript compiler rather than grepped,
 * because `Number(` in a comment or in a string is not a defect and a regex cannot tell
 * the difference.
 */
import { globSync, readFileSync } from 'node:fs'
import { join, relative } from 'node:path'
import ts from 'typescript'
import { describe, expect, it } from 'vitest'

const SRC = join(import.meta.dirname, '..')

/**
 * The API fields that are money, hours or rates. Every one of them arrives as a
 * decimal string and must reach the screen either as that string or through
 * `lib/decimal.ts` -- never through `Number()`, `parseFloat`, or `+`.
 *
 * The kind of thing this catches is rarely a total anybody set out to compute. The
 * Economia tab wanted to know whether a deal has hours left to invoice, and the obvious
 * spelling is `Number(pnl.ore_fatturabili_non_fatturate) > 0` -- a float parse of a
 * decimal column, written as a *question* rather than as a sum, and one this list flags
 * on sight. `EconomicsTab.tsx` asks `!== '0.00'` instead, comparing the string the API
 * sent, which is also the only spelling `Numeric(12,2)` serialises for zero.
 */
const ECONOMIC_FIELDS = new Set([
  'amount',
  'hours',
  'total_hours',
  'budgeted_hours',
  'billable_uninvoiced_hours',
  'line_value',
  'line_cost',
  'expected_value',
  'budgeted_value',
  'unbilled_hours_value',
  'accrued_value',
  'rate',
  'applied_rate',
  'hourly_rate',
  'default_hourly_rate',
  'cost',
  'applied_cost',
  'default_hourly_cost',
  'work_cost',
  'direct_costs',
  'revenue',
  'gross_margin',
  'percentage_margin',
  'budget_pro_rata',
  'value_variance',
  'hours_progress',
  'taxable_amount',
  'substitute_tax',
  'contributions',
  'estimated_net_income',
])

/**
 * The one module allowed to touch these values numerically, and the one place the
 * exemption is written down. `lib/decimal.ts` is the exemption; its own test exercises
 * it. `features/deals/columns.tsx` is NOT exempt -- it goes through the helper.
 */
const ALLOWED = new Set(['lib/decimal.ts', 'lib/decimal.test.ts', 'lib/no-float-money.test.ts'])

/**
 * The binary operators that would turn a decimal string into a float. `+=` is in the
 * list for the accumulator shape (`total += row.ore`) that a `reduce` rewritten as a
 * loop reaches for first.
 */
const ARITHMETIC: ts.SyntaxKind[] = [
  ts.SyntaxKind.PlusToken,
  ts.SyntaxKind.MinusToken,
  ts.SyntaxKind.AsteriskToken,
  ts.SyntaxKind.SlashToken,
  ts.SyntaxKind.PlusEqualsToken,
]

function economicFieldName(node: ts.Node): string | null {
  if (ts.isPropertyAccessExpression(node)) return node.name.text
  if (ts.isElementAccessExpression(node) && ts.isStringLiteralLike(node.argumentExpression)) {
    return node.argumentExpression.text
  }
  return null
}

function violationsIn(file: string): string[] {
  const source = ts.createSourceFile(
    file,
    readFileSync(file, 'utf8'),
    ts.ScriptTarget.ESNext,
    true,
    ts.ScriptKind.TSX,
  )
  const found: string[] = []

  const isEconomic = (node: ts.Node): boolean => {
    const name = economicFieldName(node)
    return name !== null && ECONOMIC_FIELDS.has(name)
  }

  const report = (node: ts.Node, what: string) => {
    const { line } = source.getLineAndCharacterOfPosition(node.getStart(source))
    found.push(`${relative(SRC, file)}:${line + 1} ${what}`)
  }

  const visit = (node: ts.Node): void => {
    if (ts.isCallExpression(node) && ts.isIdentifier(node.expression)) {
      const callee = node.expression.text
      if (
        (callee === 'Number' || callee === 'parseFloat' || callee === 'parseInt') &&
        node.arguments.some(isEconomic)
      ) {
        report(node, `${callee}() applied to an economic field`)
      }
    }
    if (
      ts.isBinaryExpression(node) &&
      ARITHMETIC.includes(node.operatorToken.kind) &&
      (isEconomic(node.left) || isEconomic(node.right))
    ) {
      report(node, 'arithmetic on an economic field')
    }
    if (
      ts.isPrefixUnaryExpression(node) &&
      node.operator === ts.SyntaxKind.PlusToken &&
      isEconomic(node.operand)
    ) {
      report(node, 'unary + on an economic field')
    }
    ts.forEachChild(node, visit)
  }

  ts.forEachChild(source, visit)
  return found
}

describe('no economic total is born in the browser', () => {
  it('finds no float arithmetic on an API money, hours or rate field', () => {
    const files = globSync('**/*.{ts,tsx}', { cwd: SRC })
      .filter((file) => !ALLOWED.has(file) && !file.includes('__fixtures__'))
      .map((file) => join(SRC, file))
    const violations = files.flatMap(violationsIn)
    expect(violations).toEqual([])
  })

  it('actually catches a violation, rather than only claiming to', () => {
    // The guard proven to work, on a real construct, the same discipline
    // `test_architecture.py` follows for its own import checks.
    const fixture = join(import.meta.dirname, '__fixtures__', 'bad-money.ts')
    expect(violationsIn(fixture).length).toBeGreaterThan(0)
  })
})
