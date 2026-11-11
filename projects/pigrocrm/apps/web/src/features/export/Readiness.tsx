import { Link } from '@tanstack/react-router'
import type { ReactNode } from 'react'
import { StatusPill } from '@/components/StatusPill'
import { CHECK_LABELS, CHECK_TONES, moreWord, type ChecklistCheck } from './labels'

/**
 * Where an open point is fixed: the sentence of the link and the route behind it. The server says
 * which place (`target`) and the page only maps it to its own route, so a place it does not know
 * is drawn with no link rather than a wrong one.
 */
function FixLink({ target }: { target: string }): ReactNode {
  const className = 'text-sm underline underline-offset-4'
  switch (target) {
    case 'inbox':
      return (
        <Link to="/app/inbox" search={{ tab: 'pending' }} className={className}>
          Apri In arrivo
        </Link>
      )
    case 'purchases':
      return (
        <Link to="/app/purchases" className={className}>
          Apri Acquisti
        </Link>
      )
    case 'invoices':
      return (
        <Link to="/app/invoices" search={{}} className={className}>
          Apri Fatture
        </Link>
      )
    case 'cash':
      return (
        <Link to="/app/cash" className={className}>
          Apri Cassa
        </Link>
      )
    case 'iva':
      return (
        <Link to="/app/vat" className={className}>
          Apri IVA
        </Link>
      )
    case 'staff':
      return (
        <Link to="/app/staff" className={className}>
          Apri Persone dell’azienda
        </Link>
      )
    default:
      return null
  }
}

/**
 * Every check of the period as a row, the fine ones included (brief, answer 2): a state, a count
 * and the first open points by name with «e altre N» after them. With `fix` each open check
 * carries the link to where it is fixed; a snapshot kept with an export has none, since it says
 * how the period stood and not what to do now. Open points are gold and never stop the export.
 */
export function Readiness({
  checks,
  fix,
  label,
}: {
  checks: readonly ChecklistCheck[]
  fix: boolean
  label: string
}) {
  return (
    <ul aria-label={label} className="divide-y border bg-background">
      {checks.map((check) => (
        <li key={check.key} className="space-y-1 px-3 py-2" data-check={check.key}>
          <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
            <p className="min-w-0 break-words text-sm font-medium">{check.label}</p>
            <StatusPill tone={CHECK_TONES[check.state]}>
              {CHECK_LABELS[check.state]}
              {check.state === 'open' ? ` · ${check.count}` : ''}
            </StatusPill>
          </div>
          {check.items.length > 0 ? (
            <ul className="space-y-0.5 text-xs text-muted-foreground">
              {check.items.map((item, position) => (
                <li key={`${position}-${item}`} className="break-words">
                  {item}
                </li>
              ))}
              {check.more > 0 ? <li>{moreWord(check.more)}</li> : null}
            </ul>
          ) : null}
          {fix && check.state === 'open' ? <FixLink target={check.target} /> : null}
        </li>
      ))}
    </ul>
  )
}
