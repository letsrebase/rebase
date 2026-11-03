// Not imported by anything: it exists so `no-float-money.test.ts` can prove its guard
// fires on a real construct instead of asserting so in prose. Excluded from the sweep
// by living under `__fixtures__`, which the glob filters out explicitly -- this file is
// handed to `violationsIn` directly instead.
export function wrong(rows: { amount: string }[]): number {
  return rows.reduce((sum, row) => sum + Number(row.amount), 0)
}
