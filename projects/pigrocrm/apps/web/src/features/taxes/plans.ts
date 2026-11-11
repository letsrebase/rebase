import type { StatusTone } from '@/components/StatusPill'
import { TAX_LABELS, type Tax } from './labels'
import { TAX_ITEM_STATE_LABEL, TAX_ITEM_STATE_TONE, taxItemState, type TaxItemState } from './items'
import type { TaxDueItem, TaxInstalmentPlan, TaxNoticePlan } from './queries'

/**
 * The rows of «Rate»: a plan the administrator chose for the June payments, or the plan of a
 * notice from the tax office typed by hand, each with the due items its instalments wrote. The
 * items are matched by what the server writes into them (the tax and the year of an instalment
 * plan, the label of a notice plan); nothing is added or compared as an amount.
 */

export type PlanRow =
  | { kind: 'instalment'; key: string; title: string; plan: TaxInstalmentPlan; items: TaxDueItem[] }
  | { kind: 'notice'; key: string; title: string; plan: TaxNoticePlan; items: TaxDueItem[] }

const byDate = (a: TaxDueItem, b: TaxDueItem) =>
  (a.due_date ?? '') < (b.due_date ?? '') ? -1 : (a.due_date ?? '') > (b.due_date ?? '') ? 1 : 0

/** An instalment plan's items: the instalments of its tax paid in the year after the tax year
 *  (the June balance and the first advance, which fall in it; the November advance is never split). */
function itemsOfInstalmentPlan(plan: TaxInstalmentPlan, items: readonly TaxDueItem[]): TaxDueItem[] {
  return items
    .filter(
      (item) =>
        item.source_kind === 'tax_instalment' &&
        item.tax === plan.tax &&
        item.due_date?.startsWith(`${plan.tax_year + 1}-`) === true,
    )
    .sort(byDate)
}

/** A notice plan's items: the instalments whose description starts with its label, which is how
 *  the server writes them («Avviso bonario 2023, rata 2 di 6»). */
function itemsOfNoticePlan(plan: TaxNoticePlan, items: readonly TaxDueItem[]): TaxDueItem[] {
  return items
    .filter(
      (item) =>
        item.source_kind === 'notice_plan' &&
        item.tax === plan.tax &&
        (item.description ?? '').startsWith(`${plan.label}, rata `),
    )
    .sort(byDate)
}

export function planRows(
  instalmentPlans: readonly TaxInstalmentPlan[],
  noticePlans: readonly TaxNoticePlan[],
  items: readonly TaxDueItem[],
): PlanRow[] {
  const rows: PlanRow[] = []
  for (const plan of instalmentPlans) {
    rows.push({
      kind: 'instalment',
      key: `instalment-${plan.tax}-${plan.tax_year}`,
      title: `Rateazione ${TAX_LABELS[plan.tax as Tax] ?? plan.tax} ${plan.tax_year}`,
      plan,
      items: itemsOfInstalmentPlan(plan, items),
    })
  }
  for (const plan of noticePlans) {
    rows.push({
      kind: 'notice',
      key: `notice-${plan.id}`,
      title: plan.label,
      plan,
      items: itemsOfNoticePlan(plan, items),
    })
  }
  return rows
}

/** The first instalment still to pay, by date. */
export function nextInstalment(row: PlanRow, today: string): TaxDueItem | null {
  const open = row.items.filter((item) => item.state === 'forecast' || item.state === 'expected')
  return open.find((item) => (item.due_date ?? '') >= today) ?? open[0] ?? null
}

/** Where the plan stands: the worst state of its instalments (late, then waiting, then
 *  forecast), «Pagato» when every one is settled, «Previsto» when the items are not written yet. */
export function planState(row: PlanRow, today: string): { tone: StatusTone; label: string } {
  const states = row.items.map((item) => taxItemState(item, today))
  const order: TaxItemState[] = ['overdue', 'expected', 'forecast']
  const worst = order.find((state) => states.includes(state))
  if (worst) return { tone: TAX_ITEM_STATE_TONE[worst], label: TAX_ITEM_STATE_LABEL[worst] }
  if (states.length > 0 && states.every((state) => state === 'settled' || state === 'dropped'))
    return { tone: TAX_ITEM_STATE_TONE.settled, label: TAX_ITEM_STATE_LABEL.settled }
  return { tone: TAX_ITEM_STATE_TONE.forecast, label: TAX_ITEM_STATE_LABEL.forecast }
}
