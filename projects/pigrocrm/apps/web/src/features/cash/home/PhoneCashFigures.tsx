import { Link } from '@tanstack/react-router'
import type { ReactNode } from 'react'
import { useMediaQuery } from '@rebase/ui/use-media-query'
import { LoadingState } from '@/components/LoadingState'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { money } from '@/features/dashboard/format'
import { formatIsoDateItalian } from '@/lib/dates'
import { CashBand } from './CashBand'
import { useCashSummary, type CashSummary } from './queries'

const CARD = 'block min-w-0 border border-border bg-card px-3 py-3'
const LABEL = 'text-xs text-muted-foreground'
const FIGURE = 'break-words text-xl font-semibold tabular-nums'

function Late({ count, total, solo }: { count: number; total: string; solo: boolean }): ReactNode {
  if (count === 0) return <p className="mt-1 text-xs text-muted-foreground">Niente di scaduto</p>
  return (
    <Link
      to="/app/invoices"
      search={{ overdue: true }}
      className="mt-1 block text-xs font-medium tabular-nums text-destructive"
    >
      {solo ? 'Di cui scaduto' : 'di cui scaduto'} {money(total)}
    </Link>
  )
}

/** The figures of a settled summary; separate from the hook so each state is testable. */
export function CashFigures({ summary }: { summary: CashSummary }) {
  const solo = !summary.show_outgoing || summary.payable === null
  const payable = summary.payable
  const balance = summary.cash_enabled ? summary.balance_today : null
  return (
    <div className="space-y-3">
      {summary.cash_enabled && summary.alert && <CashBand alert={summary.alert} />}
      {balance !== null && (
        <Link to="/app/cash" className={CARD}>
          <p className={LABEL}>Saldo oggi</p>
          <p
            className={`break-words text-2xl font-semibold tabular-nums${balance.startsWith('-') ? ' text-destructive' : ''}`}
          >
            {money(balance)}
          </p>
          {summary.balance_as_of && (
            <p className="text-xs text-muted-foreground">al {formatIsoDateItalian(summary.balance_as_of)}</p>
          )}
        </Link>
      )}
      <div className={solo ? 'grid grid-cols-1 gap-3' : 'grid grid-cols-2 gap-3'}>
        <div className={CARD}>
          <Link to="/app/cash" className="block">
            <p className={LABEL}>Da incassare</p>
            <p className={FIGURE}>{money(summary.receivable.total)}</p>
          </Link>
          <Late count={summary.receivable.late_count} total={summary.receivable.late_total} solo={solo} />
        </div>
        {payable && (
          <div className={CARD}>
            <Link to="/app/cash" className="block">
              <p className={LABEL}>Da pagare</p>
              <p className={FIGURE}>{money(payable.total)}</p>
            </Link>
            {payable.late_count === 0 ? (
              <p className="mt-1 text-xs text-muted-foreground">Niente di scaduto</p>
            ) : (
              <Link to="/app/cash" className="mt-1 block text-xs font-medium tabular-nums text-destructive">
                di cui scaduto {money(payable.late_total)}
              </Link>
            )}
          </div>
        )}
      </div>
      <Link to="/app/cash" className={CARD}>
        <p className={LABEL}>Prossimi 7 giorni</p>
        <p className="flex items-baseline justify-between gap-2 text-sm">
          <span>entrano</span>
          <span className="break-words font-semibold tabular-nums">{money(summary.next_7_days.in_total)}</span>
        </p>
        {summary.next_7_days.out_total !== null && !solo && (
          <p className="flex items-baseline justify-between gap-2 text-sm">
            <span>escono</span>
            <span className="break-words font-semibold tabular-nums">{money(summary.next_7_days.out_total)}</span>
          </p>
        )}
      </Link>
    </div>
  )
}

function Figures() {
  const summary = useCashSummary(true)
  if (summary.isError) return <QueryErrorBanner error={summary.error} onRetry={() => void summary.refetch()} />
  if (summary.isPending || !summary.data) return <LoadingState label="Leggo le cifre della cassa…" />
  return <CashFigures summary={summary.data} />
}

/**
 * The money figures at the top of the phone Home (brief decision 6). Phone only: from 640 px
 * the Home keeps its shape, its figures are the tab «Scadenziario». Mounted before the rest
 * of the Home, which a slow or failed read never holds back.
 */
export function PhoneCashFigures() {
  const phone = !useMediaQuery('(min-width: 640px)')
  if (!phone) return null
  return (
    <section aria-label="Cassa oggi" className="mb-6">
      <Figures />
    </section>
  )
}
