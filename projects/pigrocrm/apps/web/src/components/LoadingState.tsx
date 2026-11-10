import { Loader } from '@rebase/ui/loader'
import { cn } from '@rebase/ui/cn'

/**
 * The one loading state of the app: the brand's `Loader` with a `role="status"` sentence
 * beside it, as `docs/design/DECISIONS.md` fixed on 2026-11-09 (REB-973), instead of grey
 * `Skeleton` bars. `Loader` is decorative and carries no name, so the sentence is what a
 * screen reader hears. It is the wait of a panel, a list or a whole record page; pass a
 * `className` for the spacing the place needs (`p-8` for a page, none for a section).
 */
export function LoadingState({
  label = 'Caricamento…',
  className,
}: {
  label?: string
  className?: string
}) {
  return (
    <div role="status" aria-label={label} className={cn('flex items-center gap-2 py-4 text-sm text-muted-foreground', className)}>
      <Loader className="size-4" />
      {label}
    </div>
  )
}
