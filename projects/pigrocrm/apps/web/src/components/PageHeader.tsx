import type { LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '@rebase/ui/cn'

/**
 * The header of a page, inside the white panel (spec 2026-09-08 §4).
 *
 * It replaces the shell's top bar, which is gone: the search moved into the sidebar, and
 * what used to be a breadcrumb repeating the sidebar's own highlight is now the page's
 * title, at the size a title deserves. One `<h1>` per screen lives here.
 *
 * Five slots, in the order they are drawn: the title row (icon, title, description,
 * `actions`), then `aboveTabs` for something that belongs to the page's first reading and
 * not to a tab (the Home's «Da fare» band, REB-874), then `tabs` on a row of their own
 * closed by a rule, then `children` for whatever a page needs between its header and its
 * content -- a filter row, most often.
 *
 * What follows a tab rule is never flush against it (REB-1055). The page's body, a sibling of
 * this header inside the `Tabs` root, starts 32px under the rule: the root's `gap-2` plus its own
 * `py-6`. `children` sit *inside* the header, where that gap does not reach them, so with `tabs`
 * they are given the same 32px here, once, and a page no longer adds a top margin of its own
 * (`Anagrafiche` and `In arrivo` had none, `Attività` and `Incarichi` each patched it with a
 * `pt-4` of their own).
 * Without `tabs` the title row's own bottom padding is the gap, as before.
 * `actions` is where a page's primary button goes (§5: one strong thing per screen, top
 * right); everything else on the screen is quieter than it.
 */
export function PageHeader({
  icon: Icon,
  title,
  description,
  titleAddon,
  actions,
  aboveTabs,
  tabs,
  children,
  className,
}: {
  icon: LucideIcon
  title: string
  description?: string
  /** Something that belongs to the title and is not text (the roles of a record, as chips):
   *  a wrapping row right under the `<h1>`, before the description. */
  titleAddon?: ReactNode
  actions?: ReactNode
  aboveTabs?: ReactNode
  tabs?: ReactNode
  children?: ReactNode
  className?: string
}) {
  return (
    <header className={cn('shrink-0', className)}>
      {/* One row from `md` up, three stacked below it. On a phone the icon, a 24px title
          and a primary button cannot share a row: at 390 the title came out as «F…» and
          the description as one word per line, because `actions` kept its width and the
          text beside it gave up all of its own (screenshots of 2026-09-08). */}
      <div className="flex flex-col gap-4 px-8 pt-6 pb-5 md:flex-row md:items-start">
        <div className="flex min-w-0 flex-1 items-start gap-4">
          {/* Paper square, 40px, closed by the 1px ink line: the one piece of structure
              in the header, and a fixed anchor the eye finds at the same spot on every
              page. It used to carry a radius of 10, the soft system's; the derived scale
              is zero since the record of 2026-09-18. Decorative -- it says what the title
              already says, so it is hidden from a screen reader. */}
          <span
            className="flex size-10 shrink-0 items-center justify-center border bg-background text-foreground"
            aria-hidden="true"
          >
            <Icon className="size-5" aria-hidden="true" />
          </span>
          <div className="min-w-0 flex-1">
            {/* Truncated only where it shares the row with the actions; below `md` it has
                a line to itself and wraps instead of losing its own name. */}
            <h1 className="text-2xl font-semibold tracking-tight md:truncate">{title}</h1>
            {titleAddon && <div className="mt-2 flex flex-wrap items-center gap-1.5">{titleAddon}</div>}
            {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
          </div>
        </div>
        {/* Wrapping, because on its own row a page may put two buttons and a period picker
            here; `shrink-0` only from `md`, where there is a row to be pinned in. */}
        {actions && (
          <div className="flex flex-wrap items-center gap-2 md:shrink-0">{actions}</div>
        )}
      </div>

      {aboveTabs}

      {/* Scrolls sideways inside the panel: thirteen settings tabs are wider than a
          phone, and the alternative is a page that scrolls horizontally as a whole. */}
      {tabs && <div className="overflow-x-auto border-b px-8">{tabs}</div>}
      {/* `pt-8` is `gap-2` (the Tabs root's, between this header and the body) plus the body's
          `py-6`: the same 32px under the rule whichever slot the content is in. */}
      {children && <div className={cn(tabs && 'pt-8')}>{children}</div>}
    </header>
  )
}
