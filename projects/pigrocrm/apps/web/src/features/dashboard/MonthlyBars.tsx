import { ScrollRegion } from '@rebase/ui/table'
import { money } from './format'
import { SERIES, type SeriesKey } from './monthlySeries'
import type { CashMonth } from './queries'

/**
 * Twelve stacked columns, one per month, in the product's own visual system: square
 * tiles on the grid, a 2px surface gap between segments, a legend always present
 * (there are two or four series) and the table view underneath for a screen reader.
 *
 * Heights come from the server (`quote_*`, a share of the tallest month in [0, 1]):
 * the browser never turns an amount string into a number
 * (src/test/no-browser-arithmetic.test.ts); it multiplies a share by a pixel height,
 * which is geometry, not a figure.
 *
 * Series colours are the tile palette, not a validated categorical set: Prussian Blue
 * and Royal Gold sit outside the lightness band a chart palette would want, and the
 * dataviz validator says so. The brand wins here, and the secondary encodings carry
 * identity where colour alone would not -- legend, direct labels, the 2px gaps and the
 * table view.
 *
 * The figures on the columns (ORB-139). Every segment tall enough to hold the text says
 * its own value inside; a column too short for a label says its value above itself
 * rather than nowhere; a stacked column says its height as money above, so the figure on
 * top of a month is the bar a reader measures and not one segment mistaken for it. That
 * height comes from the server (`pila_andamento`, `pila_proiezione`), costs included
 * since the bar includes them: a sum is a figure, and no figure is born in the browser.
 * A payload without it, an API a deploy behind the web, prints nothing above rather than
 * `NaN €`. Below 72px of column the labels hide instead of clipping (a container query):
 * a cut-off amount is a wrong amount, and the tooltip and the table keep the full one.
 */

const MONTHS = ['gen', 'feb', 'mar', 'apr', 'mag', 'giu', 'lug', 'ago', 'set', 'ott', 'nov', 'dic']
const PLOT_HEIGHT = 180
/** The room a 10px label with its padding needs: below this a segment stays mute, and a
 *  column made only of mute segments lifts its value above itself instead. */
const LABEL_HEIGHT = 16
/** Reserved above the plot for the lifted value: its fixed 16px slot, the 2px flex gap
 *  under it, and the 2px gaps between up to four stacked segments, so a full-height
 *  column carries its figure without growing past the plot and dropping its baseline. */
const HEADROOM = 24

function isZero(value: string): boolean {
  return /^-?0(?:\.0+)?$/.test(value)
}

export function MonthlyBars({
  title,
  months,
  series,
  shares,
}: {
  title: string
  months: CashMonth[]
  series: SeriesKey[]
  shares: 'quote_andamento' | 'quote_proiezione'
}) {
  const empty = months.every((month) => series.every((key) => isZero(month[key])))
  const heights = shares === 'quote_andamento' ? 'pila_andamento' : 'pila_proiezione'

  return (
    <figure className="border bg-card p-4" aria-labelledby={`${shares}-title`}>
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <figcaption id={`${shares}-title`} className="text-sm font-medium">
          {title}
        </figcaption>
        <ul className="flex flex-wrap gap-4 text-xs text-muted-foreground" aria-label="Legenda">
          {series.map((key) => (
            <li key={key} className="flex items-center gap-1.5">
              <span
                aria-hidden="true"
                className="inline-block size-2.5"
                style={{ backgroundColor: SERIES[key].color }}
              />
              {SERIES[key].label}
            </li>
          ))}
        </ul>
      </div>

      {empty ? (
        <p className="mt-4 text-sm text-muted-foreground">Nessun movimento nell&apos;anno.</p>
      ) : (
        <div
          aria-hidden="true"
          className="mt-4 grid grid-cols-12 items-end gap-2"
          style={{ height: PLOT_HEIGHT + HEADROOM }}
        >
          {months.map((month) => {
            // Stacked from the baseline up, in series order; a 2px surface gap between
            // segments, none below the first. Heights are geometry from the server's
            // shares; whether a segment can carry its label is a question about pixels.
            const segments = series
              .map((key) => ({ key, share: month[shares][key] ?? 0, value: month[key] }))
              .filter((segment) => segment.share > 0)
              .map((segment) => ({
                ...segment,
                height: Math.max(2, segment.share * PLOT_HEIGHT),
              }))
            const labelled = segments.filter((segment) => segment.height >= LABEL_HEIGHT)
            // Above the column: the stack's height as money when the month stacks more
            // than one segment, the lone value when its only segment is too short to carry
            // it. A month with one segment tall enough says its figure once, inside. An
            // older payload carries no `pila_*`: then nothing, never `NaN €`.
            const pila: string | undefined = month[heights]
            const above =
              segments.length > 1
                ? pila === undefined
                  ? null
                  : money(pila)
                : segments.length === 1 && labelled.length === 0
                  ? money(segments[0]!.value)
                  : null
            return (
              <div
                key={month.mese}
                data-testid={`column-${month.mese}`}
                className="@container flex h-full min-h-0 flex-col justify-end gap-0.5"
              >
                {above !== null && (
                  <span
                    data-testid="lifted-value"
                    className="hidden h-4 text-center text-[10px] leading-4 font-medium text-muted-foreground @min-[72px]:block"
                  >
                    {above}
                  </span>
                )}
                {[...segments].reverse().map((segment) => (
                  <div
                    key={segment.key}
                    data-series={segment.key}
                    data-testid="segment"
                    title={`${SERIES[segment.key].label}: ${money(segment.value)}`}
                    className="relative w-full"
                    style={{
                      height: `${segment.height}px`,
                      backgroundColor: SERIES[segment.key].color,
                    }}
                  >
                    {segment.height >= LABEL_HEIGHT && (
                      <span
                        className="absolute inset-x-0 top-1 hidden px-1 text-center text-[10px] font-medium @min-[72px]:block"
                        style={{
                          color:
                            segment.key === 'da_incassare'
                              ? 'var(--color-prussian-blue)'
                              : '#ffffff',
                        }}
                      >
                        {money(segment.value)}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            )
          })}
        </div>
      )}
      <div aria-hidden="true" className="mt-1 grid grid-cols-12 gap-2 text-center text-xs text-muted-foreground">
        {months.map((month) => (
          <span key={month.mese}>{MONTHS[month.mese - 1]}</span>
        ))}
      </div>

      {/* The table view: the same figures, for a screen reader and for anyone who wants
          the number rather than the bar. Visually collapsed, never absent. */}
      <details className="mt-3 text-sm">
        <summary className="cursor-pointer text-xs text-muted-foreground">Tabella dei valori</summary>
        <ScrollRegion label={`${title}, valori`} className="mt-2">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-muted-foreground">
                <th scope="col" className="py-1 pr-2 font-normal">
                  Mese
                </th>
                {series.map((key) => (
                  <th key={key} scope="col" className="py-1 pr-2 font-normal">
                    {SERIES[key].label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {months.map((month) => (
                <tr key={month.mese}>
                  <th scope="row" className="py-1 pr-2 text-left font-normal">
                    {MONTHS[month.mese - 1]} {String(month.anno).slice(2)}
                  </th>
                  {series.map((key) => (
                    <td key={key} className="py-1 pr-2 tabular-nums">
                      {money(month[key])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </ScrollRegion>
      </details>
    </figure>
  )
}
