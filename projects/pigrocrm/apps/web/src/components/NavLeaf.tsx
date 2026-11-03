import { Link } from '@tanstack/react-router'
import type { LucideIcon } from 'lucide-react'
import { cn } from '@rebase/ui/cn'
import type { LinkTo } from './nav'
import { ACTIVE, FOCUS, ITEM, QUIET } from './sidebarStyles'

/**
 * A leaf entry: an icon and a label expanded, an icon alone in the rail. A `count` above
 * zero is drawn as a badge after the label, and on the icon's corner in the rail, where it
 * is also part of the link's accessible name. `overdue` is «Attività»'s own late count (the
 * one number of the product that asks for an action, slice 7 §5), drawn beside it and only
 * when it is known.
 */
export function NavLeaf({
  to,
  label,
  icon: Icon,
  rail,
  exact = false,
  count = 0,
  overdue,
}: {
  to: LinkTo
  label: string
  icon: LucideIcon
  rail: boolean
  exact?: boolean
  count?: number
  overdue?: number
}) {
  return (
    <Link
      to={to}
      // `includeSearch: false`, because TanStack defaults it to true: none of these links
      // carries a search of its own, so with the default an entry stopped being active
      // the moment its page put anything in the URL -- and Home, whose dashboard writes
      // its tab and period there on load, was never highlighted at all.
      activeOptions={{ exact, includeSearch: false }}
      activeProps={{ 'aria-current': 'page' }}
      className={cn(ITEM, QUIET, ACTIVE, FOCUS, rail && 'justify-center px-0')}
    >
      <Icon className="size-4 shrink-0" aria-hidden="true" />
      <span className={cn('truncate', rail && 'sr-only')}>{label}</span>
      {to === '/app/activities' && overdue !== undefined && (
        <span
          className={cn(
            'bg-sidebar-primary px-1.5 text-xs font-medium text-sidebar-primary-foreground',
            rail ? 'absolute top-1 right-1' : 'ml-auto',
          )}
        >
          <span aria-hidden="true">{overdue}</span>
          <span className="sr-only">{overdue} in ritardo</span>
        </span>
      )}
      {count > 0 && (
        <>
          {/* Sidebar Watermelon Strong under white, the pair the active tile already uses
              and `tokens.test.ts` measures at AA. In the rail it sits on the icon's corner. */}
          <span
            aria-hidden="true"
            className={cn(
              'bg-sidebar-primary px-1.5 text-xs font-medium tabular-nums text-sidebar-primary-foreground',
              rail ? 'absolute right-1.5 top-0.5 min-w-4 px-1 text-center' : 'ml-auto',
            )}
          >
            {count > 99 ? '99+' : count}
          </span>
          <span className="sr-only">{`, ${count} da leggere`}</span>
        </>
      )}
    </Link>
  )
}
