/**
 * Four shapes, no library, and every one of them with an equivalent table.
 *
 * A chart without a table is a figure a screen reader does not read (§13), so the table is
 * not a fallback -- it is the accessible rendering, and the visual mark is decoration on
 * top of it. That is why every assertion below is on table semantics rather than on SVG.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { BarRows, BigNumber } from './charts'

describe('BigNumber', () => {
  it('renders the value exactly as given, never reformatted', () => {
    // The API sends "1234.56" already formatted for it-IT upstream; the component must not
    // parse it. Parsing is what criterion 14 forbids.
    render(<BigNumber label="Fatturato" value="1.234,56 €" hint="imponibile, emesso" />)
    expect(screen.getByText('1.234,56 €')).toBeInTheDocument()
    expect(screen.getByText('Fatturato')).toBeInTheDocument()
    expect(screen.getByText('imponibile, emesso')).toBeInTheDocument()
  })

  it('associates the label with the value for a screen reader', () => {
    render(<BigNumber label="Deal aperti" value="12" />)
    expect(screen.getByRole('group', { name: /deal aperti/i })).toBeInTheDocument()
  })

  /**
   * The KPI card of the reference screenshots (design spec §4): a 15px Charcoal label
   * over a 30px semibold value. Asserted as the utilities that set those sizes, not as
   * a computed `font-size`: jsdom loads no stylesheet, so a Tailwind arbitrary value
   * computes to nothing at all and an assertion on the rendered size would pass on a
   * card with no styling whatever. What it protects is the one thing this shape must
   * never do -- a value that reads at the same size and weight as its label, when the
   * whole point is that the figure is what the eye lands on.
   */
  it('sets the label and the value at the two sizes the KPI card is specified at', () => {
    render(<BigNumber label="Deal aperti" value="12" />)
    expect(screen.getByText('Deal aperti')).toHaveClass('text-[15px]')
    const value = screen.getByText('12')
    expect(value).toHaveClass('text-[30px]')
    expect(value).toHaveClass('font-semibold')
  })
})

describe('BarRows', () => {
  const rows = [
    { label: 'Lead', value: '3.000,00 €', ratio: 1, tone: 1 },
    { label: 'Offerta', value: '500,00 €', ratio: 0.166, tone: 2 },
  ]

  it('renders a real table with a caption', () => {
    render(<BarRows caption="Pipeline per stato" rows={rows} />)
    expect(screen.getByRole('table', { name: 'Pipeline per stato' })).toBeInTheDocument()
    expect(screen.getAllByRole('row')).toHaveLength(3) // header + two
  })

  it('shows every label and value as text', () => {
    render(<BarRows caption="Pipeline per stato" rows={rows} />)
    expect(screen.getByText('Lead')).toBeInTheDocument()
    expect(screen.getByText('3.000,00 €')).toBeInTheDocument()
  })

  it('draws the bar with a CSS width and marks it decorative', () => {
    render(<BarRows caption="Pipeline per stato" rows={rows} />)
    const bars = screen.getAllByTestId('bar-fill')
    expect(bars[0]).toHaveStyle({ width: '100%' })
    // The bar duplicates the number next to it, so it is hidden from the accessibility
    // tree rather than announced twice.
    expect(bars[0]).toHaveAttribute('aria-hidden', 'true')
  })

  it('gives a smaller ratio a proportionally smaller bar', () => {
    // Without this the clamp tests below pass on a component that ignores `ratio` and
    // hard-codes 100%, which is the shape a "clamped to 100%" assertion cannot see.
    render(<BarRows caption="Pipeline per stato" rows={rows} />)
    const bars = screen.getAllByTestId('bar-fill')
    expect(bars[1]).toHaveStyle({ width: '16.60%' })
  })

  it('clamps a ratio outside 0..1 instead of overflowing the row', () => {
    render(<BarRows caption="c" rows={[{ label: 'x', value: '1', ratio: 4, tone: 1 }]} />)
    expect(screen.getByTestId('bar-fill')).toHaveStyle({ width: '100%' })
  })

  it('clamps a negative ratio to nothing rather than to a backwards bar', () => {
    render(<BarRows caption="c" rows={[{ label: 'x', value: '1', ratio: -2, tone: 1 }]} />)
    expect(screen.getByTestId('bar-fill')).toHaveStyle({ width: '0.00%' })
  })

  it('paints each row from its own chart token, wrapping past the fifth', () => {
    // The tone is what makes five bars five colours; a component that ignored it would
    // pass every other assertion in this file.
    render(
      <BarRows
        caption="c"
        rows={[
          { label: 'a', value: '1', ratio: 1, tone: 1 },
          { label: 'b', value: '1', ratio: 1, tone: 3 },
          { label: 'f', value: '1', ratio: 1, tone: 6 },
        ]}
      />,
    )
    const bars = screen.getAllByTestId('bar-fill')
    expect(bars[0]).toHaveStyle({ backgroundColor: 'var(--chart-1)' })
    expect(bars[1]).toHaveStyle({ backgroundColor: 'var(--chart-3)' })
    // Six rows in a five-colour palette is a wrap, not a sixth colour and not a crash.
    expect(bars[2]).toHaveStyle({ backgroundColor: 'var(--chart-1)' })
  })

  it('renders an empty state rather than an empty table', () => {
    render(<BarRows caption="Pipeline per stato" rows={[]} />)
    expect(screen.getByText(/nessun dato/i)).toBeInTheDocument()
  })
})

