import { cn } from '@rebase/ui/cn'
import { daysCount } from '@/features/cash/days'
import { money } from '@/features/dashboard/format'
import { LATE_NOTE, itemsCount } from './labels'
import type { LateFigure } from './queries'

/** Where the late lists sit on the page, for the note under the weeks. */
export const LATE_LISTS_ID = 'scaduti'

function LateCard({ label, figure }: { label: string; figure: LateFigure }) {
  const late = figure.count > 0
  return (
    <div role="group" aria-label={label} className="min-w-0 border border-border bg-card p-4">
      <p className="text-sm text-muted-foreground">{label}</p>
      <p className={cn('mt-1.5 break-words text-xl font-semibold tabular-nums', late && 'text-destructive')}>
        {money(figure.total)}
      </p>
      <p className={cn('mt-1 text-xs', late ? 'text-destructive' : 'text-muted-foreground')}>
        {late
          ? `${itemsCount(figure.count)}${figure.oldest_days === null ? '' : `, la più vecchia da ${daysCount(figure.oldest_days)}`}`
          : 'Niente in ritardo'}
      </p>
    </div>
  )
}

/** The two late figures above the weeks (decision 3): what the projected balance leaves out. */
export function LateFigures({
  receivable,
  payable,
}: {
  receivable: LateFigure
  payable: LateFigure | null
}) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <LateCard label="In ritardo da incassare" figure={receivable} />
      {payable !== null && <LateCard label="In ritardo da pagare" figure={payable} />}
    </div>
  )
}

function jumpToLateLists() {
  document.getElementById(LATE_LISTS_ID)?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
}

/** «Il saldo non conta ciò che è già scaduto», with the two totals and a link to the lists. */
export function LateNote({
  receivable,
  payable,
}: {
  receivable: LateFigure
  payable: LateFigure | null
}) {
  return (
    <p className="text-sm text-muted-foreground">
      {LATE_NOTE}: {money(receivable.total)} da incassare
      {payable !== null && <> e {money(payable.total)} da pagare</>}.{' '}
      <button type="button" onClick={jumpToLateLists} className="underline underline-offset-4">
        Vai alle voci in ritardo
      </button>
    </p>
  )
}
