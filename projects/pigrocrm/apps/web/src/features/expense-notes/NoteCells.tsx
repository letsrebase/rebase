import { StatusPill } from '@/components/StatusPill'
import { cn } from '@rebase/ui/cn'
import { formatMoneyValue } from '@/features/time/columns'
import { STATUS_LABELS, STATUS_TONES, formatDay, stateLine } from './labels'
import type { ExpenseNote } from './queries'

/** The pill of a note and the one line under it: what it waits for, how it will be paid, why it
 *  was refused (on one line, whoever reads it, the staff member included). */
export function StateCell({ note, forStaffMember }: { note: ExpenseNote; forStaffMember: boolean }) {
  const line = stateLine(note, forStaffMember)
  return (
    <span className="block max-w-64 min-w-0 space-y-1">
      <StatusPill tone={STATUS_TONES[note.status]}>{STATUS_LABELS[note.status]}</StatusPill>
      {line === null ? null : (
        <span className="block truncate text-xs text-muted-foreground" title={line}>
          {line}
        </span>
      )}
    </span>
  )
}

/** The description over the person and the date. A staff member's own list names no person. */
export function NoteCell({ note, withPerson }: { note: ExpenseNote; withPerson: boolean }) {
  return (
    <div className="min-w-0 space-y-0.5">
      <p className="max-w-80 truncate font-medium" title={note.description}>
        {note.description}
      </p>
      <p className="text-xs text-muted-foreground">
        {withPerson ? `${note.staff_member_name}, ` : ''}
        {formatDay(note.date)}
      </p>
    </div>
  )
}

/** One card of the phone list: the description and the amount on a line, the person, the date and
 *  the category under it (a long category wraps), then the pill. */
export function NoteCard({
  note,
  withPerson,
  forStaffMember,
  selected,
  onOpen,
}: {
  note: ExpenseNote
  withPerson: boolean
  forStaffMember: boolean
  selected: boolean
  onOpen: () => void
}) {
  return (
    <li>
      <button
        type="button"
        onClick={onOpen}
        aria-current={selected ? 'true' : undefined}
        className={cn(
          'block w-full space-y-2 border bg-card p-4 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
          selected && 'border-foreground',
        )}
      >
        <span className="flex items-start justify-between gap-3">
          <span className="min-w-0 flex-1 truncate font-medium" title={note.description}>
            {note.description}
          </span>
          <span className="shrink-0 tabular-nums">{formatMoneyValue(note.amount)}</span>
        </span>
        <span className="block whitespace-normal break-words text-sm text-muted-foreground">
          {withPerson ? `${note.staff_member_name}, ` : ''}
          {formatDay(note.date)}, {note.category_name}
        </span>
        <StateCell note={note} forStaffMember={forStaffMember} />
      </button>
    </li>
  )
}
