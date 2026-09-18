import { MoreHorizontal } from 'lucide-react'
import type { KeyboardEvent, MouseEvent } from 'react'
import { Button } from '@rebase/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@rebase/ui/dropdown-menu'

/**
 * One thing a row can do.
 *
 * `disabled` rather than omitting the item: an action a record cannot take *right now*
 * (emitting an already-emitted invoice) is different from one it can never take, and a
 * menu whose items move around between rows is a menu nobody learns. The caller decides
 * which of the two it is by either passing the item disabled or leaving it out.
 */
export interface RowAction {
  label: string
  onSelect: () => void
  /** Renders in the destructive tone. Does not itself confirm anything -- whatever the
   *  caller's `onSelect` did before it moved in here (a `window.confirm`, a dialog) is
   *  still its own responsibility. */
  destructive?: boolean
  disabled?: boolean
}

/**
 * The «⋯» at the end of a table row: the reference's last column, holding the actions
 * that are inline buttons today (design spec §4).
 *
 * Two things it has to get right, both of which cost a live defect if missed:
 *
 * 1. **It must not activate the row.** `DataTable`'s `onRowClick` makes the whole row a
 *    control, so pressing «⋯» would open the record and unmount the menu before it
 *    could be read. Stopping the click on the trigger is only half of it: Radix renders
 *    the menu content through a React *portal*, and a synthetic event still bubbles up
 *    the React tree from a portal, so a click on a menu item reaches the row's handler
 *    too even though the DOM says otherwise. Both ends stop it.
 * 2. **Enter and Space are the row's keys as well.** `DataTable` activates a focused row
 *    on either, mirroring the click; both also open this menu. Only those two are
 *    stopped -- Escape, the arrows and Tab belong to the menu and to the browser, and
 *    `stopPropagation` never prevents default behaviour anyway.
 *
 * An empty `items` renders nothing: the «⋯» is a promise that there is something behind
 * it, and a button that opens an empty menu breaks that promise on every row of the
 * table at once.
 */
export function RowActions({ label = 'Azioni', items }: { label?: string; items: RowAction[] }) {
  if (items.length === 0) return null

  function stopClick(event: MouseEvent) {
    event.stopPropagation()
  }

  function stopRowKeys(event: KeyboardEvent) {
    if (event.key === 'Enter' || event.key === ' ') event.stopPropagation()
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          aria-label={label}
          onClick={stopClick}
          onKeyDown={stopRowKeys}
        >
          <MoreHorizontal aria-hidden="true" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" onClick={stopClick} onKeyDown={stopRowKeys}>
        {items.map((item) => (
          <DropdownMenuItem
            key={item.label}
            variant={item.destructive ? 'destructive' : 'default'}
            disabled={item.disabled}
            onSelect={() => item.onSelect()}
          >
            {item.label}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
