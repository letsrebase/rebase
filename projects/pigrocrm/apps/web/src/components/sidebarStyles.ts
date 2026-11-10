/** The class strings every control of the sidebar shares. */

/**
 * The focus ring of every control in here, on every one of them.
 *
 * `--sidebar-ring` is Paper (see `tokens.css`), not the app-wide `--ring`: Watermelon at
 * 50% over Prussian Blue is 1.73:1, which is a ring nobody can see -- and shadcn's
 * `Button` sets `outline-none`, so an invisible ring is *no* visible focus at all. The
 * `cn()` here is `twMerge`, so this overrides the ring colour the button variant sets.
 */
export const FOCUS =
  'outline-none focus-visible:ring-[3px] focus-visible:ring-sidebar-ring focus-visible:ring-offset-0'

export const ITEM =
  'relative flex items-center gap-3 px-3 py-2 text-sm transition-colors before:absolute before:left-0 before:top-1/2 before:size-1.5 before:-translate-y-1/2 before:bg-transparent before:content-[""]'
// The active mark of the record of 2026-09-18: the lighter translucent fill on the dark
// sidebar (the group and the item inside it are marked at once, and two solid fills
// would fight), plus the Watermelon Strong tile beside the item -- a small square at the
// row's left edge, drawn by `ITEM`'s `before:` and painted only when active, so the
// label never shifts when a route changes which row carries it.
export const ACTIVE =
  'data-[status=active]:before:bg-sidebar-primary data-[status=active]:bg-sidebar-accent data-[status=active]:text-sidebar-accent-foreground data-[status=active]:font-medium'
export const QUIET = 'text-sidebar-foreground/70 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground'

/** `ACTIVE` for an entry whose caller decides it is current and says so with `aria-current`,
 *  because the router's own match cannot tell it from the entry that opens the same page. */
export const ACTIVE_BY_ARIA =
  'aria-[current=page]:before:bg-sidebar-primary aria-[current=page]:bg-sidebar-accent aria-[current=page]:text-sidebar-accent-foreground aria-[current=page]:font-medium'
