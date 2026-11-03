/**
 * The figures on the columns (ORB-139). Until now one segment per column carried a
 * value, the tallest, and only when it filled more than a third of the plot: most months
 * said nothing and the one figure shown read as the column's total when it was one
 * segment's. Now every segment tall enough to hold the text says its value, a column too
 * short for a label says it above, and a stacked column says its height as money above.
 */
import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { MonthlyBars } from './MonthlyBars'
import type { CashMonth } from './queries'

function month(
  mese: number,
  amounts: Partial<Record<'paid' | 'unpaid' | 'drafts' | 'costs', string>>,
  shares: Record<string, number>,
  stack: { trend?: string; projection?: string } = {},
): CashMonth {
  return {
    year: 2026,
    month: mese,
    paid: amounts.paid ?? '0.00',
    unpaid: amounts.unpaid ?? '0.00',
    drafts: amounts.drafts ?? '0.00',
    costs: amounts.costs ?? '0.00',
    trend_stack: stack.trend ?? amounts.paid ?? '0.00',
    projection_stack: stack.projection ?? amounts.paid ?? '0.00',
    trend_shares: shares,
    projection_shares: shares,
  }
}

const ZERO = { paid: 0, unpaid: 0, drafts: 0, costs: 0 }

/** `count` empty months starting at `from`, to fill the row beside the one under test. */
function empties(from: number, count: number): CashMonth[] {
  return Array.from({ length: count }, (_, i) => month(from + i, {}, ZERO))
}

function column(month: number) {
  return screen.getByTestId(`column-${month}`)
}

describe('MonthlyBars', () => {
  it('labels every segment tall enough to hold its value, not only the tallest', () => {
    render(
      <MonthlyBars
        title="Proiezione"
        months={[
          month(
            1,
            { paid: '4000.00', unpaid: '3000.00', drafts: '2000.00' },
            { ...ZERO, paid: 0.4, unpaid: 0.3, drafts: 0.2 },
            { projection: '9000.00' },
          ),
          month(2, {}, ZERO),
          month(3, {}, ZERO),
        ]}
        series={['paid', 'unpaid', 'drafts', 'costs']}
        shares="projection_shares"
      />,
    )
    const january = column(1)
    expect(within(january).getByText('4.000,00 €')).toBeInTheDocument()
    expect(within(january).getByText('3.000,00 €')).toBeInTheDocument()
    expect(within(january).getByText('2.000,00 €')).toBeInTheDocument()
  })

  it("says the stack's height as money above a stacked column, so the figure on top is the whole bar", () => {
    render(
      <MonthlyBars
        title="Proiezione"
        months={[
          month(
            8,
            { paid: '7813.91', unpaid: '9970.12', drafts: '5000.00' },
            { ...ZERO, paid: 0.3, unpaid: 0.4, drafts: 0.2 },
            { projection: '22784.03' },
          ),
          ...empties(10, 2),
        ]}
        series={['paid', 'unpaid', 'drafts', 'costs']}
        shares="projection_shares"
      />,
    )
    const august = column(8)
    expect(within(august).getByTestId('lifted-value')).toHaveTextContent('22.784,03 €')
  })

  /** August in the screenshot that opened ORB-139: a tall revenue segment and a sliver
   *  of cost too short for its own label. The lifted figure still says the whole bar. */
  it('says the height above a stack whose second segment is a sliver', () => {
    render(
      <MonthlyBars
        title="Andamento"
        months={[
          month(
            8,
            { paid: '7813.91', costs: '320.00' },
            { ...ZERO, paid: 0.96, costs: 0.04 },
            { trend: '8133.91' },
          ),
          ...empties(10, 2),
        ]}
        series={['paid', 'costs']}
        shares="trend_shares"
      />,
    )
    const august = column(8)
    expect(within(august).getByTestId('lifted-value')).toHaveTextContent('8.133,91 €')
    expect(within(august).getByText('7.813,91 €')).toBeInTheDocument()
    expect(within(august).getAllByTestId('segment')).toHaveLength(2)
  })

  /** An API one deploy behind the web sends no `pila_*`: the chart says nothing above
   *  the stack rather than `NaN €`, and the segments keep their own figures. */
  it('prints nothing above a stacked column when the payload has no height for it', () => {
    const without = month(
      8,
      { paid: '7813.91', unpaid: '9970.12' },
      { ...ZERO, paid: 0.4, unpaid: 0.5 },
    ) as Partial<CashMonth>
    delete without.projection_stack
    render(
      <MonthlyBars
        title="Proiezione"
        months={[without as CashMonth, ...empties(10, 2)]}
        series={['paid', 'unpaid', 'drafts', 'costs']}
        shares="projection_shares"
      />,
    )
    const august = column(8)
    expect(within(august).queryByTestId('lifted-value')).toBeNull()
    expect(within(august).queryByText(/NaN/)).toBeNull()
    expect(within(august).getByText('9.970,12 €')).toBeInTheDocument()
  })

  it('prints no figure above a column with one segment: its value already says it', () => {
    render(
      <MonthlyBars
        title="Andamento"
        months={[month(6, { paid: '7740.72' }, { ...ZERO, paid: 1 }), ...empties(10, 2)]}
        series={['paid', 'costs']}
        shares="trend_shares"
      />,
    )
    const june = column(6)
    expect(within(june).getByText('7.740,72 €')).toBeInTheDocument()
    expect(within(june).queryByTestId('lifted-value')).toBeNull()
  })

  /** feb and apr in the screenshot that opened ORB-139: a column of a few pixels cannot
   *  hold a label, so its value goes above it rather than nowhere. */
  it('lifts the value above a column too short to hold it', () => {
    render(
      <MonthlyBars
        title="Andamento"
        months={[
          month(2, { paid: '1000.00' }, { ...ZERO, paid: 0.05 }),
          month(6, { paid: '20000.00' }, { ...ZERO, paid: 1 }),
          ...empties(10, 1),
        ]}
        series={['paid', 'costs']}
        shares="trend_shares"
      />,
    )
    const february = column(2)
    expect(within(february).getByTestId('lifted-value')).toHaveTextContent('1.000,00 €')
    // Inside the segment there is no room, so no second copy of the figure sits there.
    expect(within(february).getByTestId('segment')).not.toHaveTextContent('1.000,00 €')
  })

  it('says nothing above an empty month', () => {
    render(
      <MonthlyBars
        title="Andamento"
        months={[month(6, { paid: '20000.00' }, { ...ZERO, paid: 1 }), ...empties(10, 2)]}
        series={['paid', 'costs']}
        shares="trend_shares"
      />,
    )
    expect(within(column(10)).queryByTestId('lifted-value')).toBeNull()
    expect(within(column(10)).queryByTestId('segment')).toBeNull()
  })
})
