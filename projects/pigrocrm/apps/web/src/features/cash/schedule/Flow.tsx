import { cn } from '@rebase/ui/cn'
import { money } from '@/features/dashboard/format'
import { isZero } from './labels'

/** The swatch of a forecast: dashed and empty where an expected amount is solid ink. The
 *  meaning is also in the word «previsti» next to it, never the swatch alone. */
export function ForecastSwatch() {
  return <span aria-hidden="true" className="inline-block size-2.5 shrink-0 border border-dashed border-muted-foreground bg-muted" />
}

/** The swatch of an expected amount. */
export function ExpectedSwatch() {
  return <span aria-hidden="true" className="inline-block size-2.5 shrink-0 bg-foreground" />
}

/**
 * Forecast against expected inside one figure (the brief's signature): the expected amount in
 * ink, the forecast under it in Charcoal italics with its swatch and the word «previsti». The
 * two numbers are the server's strings.
 */
export function Flow({
  expected,
  forecast,
  className,
}: {
  expected: string
  forecast: string
  className?: string
}) {
  return (
    <span className={cn('inline-flex flex-col items-end tabular-nums', className)}>
      <span>{money(expected)}</span>
      {!isZero(forecast) && (
        <span className="inline-flex items-center gap-1.5 text-xs italic text-muted-foreground">
          <ForecastSwatch />
          {money(forecast)} previsti
        </span>
      )}
    </span>
  )
}

/** The balance at the end of a week: `danger` with its sign and the words «sotto zero» when the
 *  server says it is below zero, so colour is never the only carrier. */
export function Balance({ value, belowZero, className }: { value: string; belowZero: boolean; className?: string }) {
  return (
    <span className={cn('inline-flex flex-col items-end tabular-nums', className)}>
      <span className={cn('font-medium', belowZero && 'text-destructive')}>{money(value)}</span>
      {belowZero && <span className="text-xs font-medium text-destructive">sotto zero</span>}
    </span>
  )
}
