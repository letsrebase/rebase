import type { ReactNode } from 'react'
import { Button } from '@rebase/ui/button'
import { cn } from '@rebase/ui/cn'

/**
 * The «riga filtri» of the design spec (§4): the chips on the left, the search box and
 * the selects on the right, between a page's header and its table.
 *
 * It goes in `PageHeader`'s `children` slot -- the slot's own docstring says «a filter
 * row, most often» -- and not as a sibling after it: the row belongs *inside* the
 * `<header>` it filters for, which is also what stops the next list page from having to
 * guess where it went.
 *
 * `role="search"` rather than a bare `div`: this row *is* the filtering of the list
 * below it, which is what the ARIA search landmark is for, and it gives a page test one
 * place to scope to (`within(screen.getByRole('search'))`) instead of matching a class
 * the next design pass renames. It carries an `aria-label` because it is the *second*
 * search landmark on screen -- `AppShell`'s sidebar has the global one -- and two
 * unnamed landmarks of the same role read identically in a screen reader's list.
 *
 * It renders nothing of its own beyond the row. Every list page has a different set of
 * filters -- and the rule for this revision is that no page gains a filter it did not
 * already have -- so the row is a container and the page keeps deciding what goes in it.
 */
export function FilterRow({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      role="search"
      aria-label="Filtra l’elenco"
      className={cn('flex flex-wrap items-center gap-3 px-8 pb-4', className)}
    >
      {children}
    </div>
  )
}

/**
 * One filter chip: a `rounded-full` toggle button (§4).
 *
 * `aria-pressed` and not a class is what says whether it is on -- a screen reader has to
 * hear a toggle's state, and it is also what the page tests assert, so a restyle cannot
 * break them. The pressed look is driven from the same attribute (`aria-pressed:`) for
 * the same reason: one source of truth for "this filter is applied".
 *
 * Ink-filled when on, the outline button's own 1px ink line when off. No new colour:
 * `--foreground` is Prussian Blue and `--background` is Paper, both from
 * `@rebase/ui/tokens.css`. The 12% tint this comment described was deleted with the old
 * token layer (REB-299): a line is the ink itself now.
 */
export function FilterChip({
  pressed,
  onPress,
  children,
}: {
  pressed: boolean
  onPress: () => void
  children: ReactNode
}) {
  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      aria-pressed={pressed}
      onClick={onPress}
      className="rounded-full aria-pressed:border-foreground aria-pressed:bg-foreground aria-pressed:text-background"
    >
      {children}
    </Button>
  )
}

export interface FilterOption<T extends string> {
  value: T
  label: string
}

/**
 * A single-choice row of chips with an «all» chip in front of it, which is the shape
 * every state filter in this product has: one value or none.
 *
 * Chips and not a `Select`, per §4 -- and a toggle rather than a radio group, because
 * pressing the chip that is already on clears the filter. That matters more than it
 * looks: with a radio group the only way back to "everything" is to find the «Tutte»
 * option, and a user who pressed «Bozze» to look at drafts expects pressing it again to
 * undo exactly that. «Tutte» stays, on when nothing is selected, so the row also reads
 * as a statement of what is on screen and not only as a set of controls.
 *
 * `value === null` is "no filter", never a magic `'tutti'` string: the API filter is
 * simply absent, and a sentinel value in the state is how a page ends up sending
 * `?stato=tutti` to a server that has no such state.
 */
export function FilterChips<T extends string>({
  label,
  allLabel,
  options,
  value,
  onChange,
}: {
  /** Names the group for a screen reader: several rows carry two of these. */
  label: string
  allLabel: string
  options: readonly FilterOption<T>[]
  value: T | null
  onChange: (next: T | null) => void
}) {
  return (
    <div role="group" aria-label={label} className="flex flex-wrap items-center gap-2">
      <FilterChip pressed={value === null} onPress={() => onChange(null)}>
        {allLabel}
      </FilterChip>
      {options.map((option) => (
        <FilterChip
          key={option.value}
          pressed={value === option.value}
          onPress={() => onChange(value === option.value ? null : option.value)}
        >
          {option.label}
        </FilterChip>
      ))}
    </div>
  )
}
