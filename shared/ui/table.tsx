import * as React from "react"

import { cn } from "./cn"

/**
 * A box that scrolls sideways when its content is wider than it is, and a region that
 * scrolls must be reachable by keyboard (axe `scrollable-region-focusable`, serious): a
 * person who cannot use a pointer otherwise cannot see what fell off the edge (REB-775).
 * So while, and only while, the content overflows, the box takes a tab stop and the arrow
 * keys scroll it, which the browser does for any focused scrollable box. It is measured,
 * not assumed: content that fits stays out of the tab order, since a tab stop on every
 * table in the product would be forty stops that do nothing.
 *
 * `label` is the region's accessible name, the one thing a screen reader says when the
 * focus lands on it («Trattamento fiscale, regione»); a caller names what the box holds.
 * The prop is required, so a caller states a name or states that it has none (`Table`
 * does, when it is given neither `scrollLabel` nor `aria-label`); without one the box is
 * still focusable and scrolls, and it is not announced as a region, since a region with
 * no name is noise.
 *
 * `Table` is this around a `<table>`. It is exported on its own for the few tables that
 * are hand-written markup (a chart's table of values, a grid of inputs) and so cannot be
 * `Table`, yet scroll just the same (REB-782).
 */
function ScrollRegion({
  label,
  className,
  ...props
}: Omit<React.ComponentProps<"div">, "aria-label"> & { label: string | undefined }) {
  const container = React.useRef<HTMLDivElement>(null)
  const [scrolls, setScrolls] = React.useState(false)

  React.useEffect(() => {
    const node = container.current
    if (!node || typeof ResizeObserver === "undefined") return
    // The observer reports once when it starts observing, so no measurement is made
    // here: the first answer arrives through the callback like every later one.
    const measure = () => setScrolls(node.scrollWidth > node.clientWidth)
    const observer = new ResizeObserver(measure)
    observer.observe(node)
    // The content's own width moves with what it holds, the box's with the card.
    if (node.firstElementChild) observer.observe(node.firstElementChild)
    return () => observer.disconnect()
  }, [])

  return (
    <div
      ref={container}
      data-slot="scroll-region"
      tabIndex={scrolls ? 0 : undefined}
      role={scrolls && label ? "region" : undefined}
      aria-label={scrolls ? label : undefined}
      /* The focus indicator is an outline drawn inside the box: the CRM's and the hub's list
         wrappers clip overflow (`overflow-x-auto overflow-y-hidden`), and a ring is a shadow
         outside the border box, which they cut off on every side. */
      className={cn(
        "relative w-full overflow-x-auto focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring",
        className
      )}
      {...props}
    />
  )
}

/**
 * The container is a `ScrollRegion`, named by `scrollLabel`. A table that already carries
 * an `aria-label` of its own lends it when no `scrollLabel` is given.
 */
function Table({
  className,
  scrollLabel,
  ...props
}: React.ComponentProps<"table"> & { scrollLabel?: string }) {
  return (
    <ScrollRegion data-slot="table-container" label={scrollLabel ?? props["aria-label"]}>
      <table
        data-slot="table"
        className={cn("w-full caption-bottom text-sm", className)}
        {...props}
      />
    </ScrollRegion>
  )
}

function TableHeader({ className, ...props }: React.ComponentProps<"thead">) {
  return (
    <thead
      data-slot="table-header"
      /* The header row is shorter than the data rows it labels (40px against 48px),
         and closes on the record's 2px rule ("a 2px rule under the header", the
         application-variant record of 2026-09-18) -- the width token, not a typed
         number. A descendant selector, so it beats TableRow's own `h-12` on
         specificity without TableRow having to know it is in a header. */
      className={cn('[&_tr]:h-10 [&_tr]:border-b-(length:--line-strong)', className)}
      {...props}
    />
  )
}

function TableBody({ className, ...props }: React.ComponentProps<"tbody">) {
  return (
    <tbody
      data-slot="table-body"
      className={cn("[&_tr:last-child]:border-0", className)}
      {...props}
    />
  )
}

function TableFooter({ className, ...props }: React.ComponentProps<"tfoot">) {
  return (
    <tfoot
      data-slot="table-footer"
      className={cn(
        "border-t bg-muted/50 font-medium [&>tr]:last:border-b-0",
        className
      )}
      {...props}
    />
  )
}

function TableRow({ className, ...props }: React.ComponentProps<"tr">) {
  return (
    <tr
      data-slot="table-row"
      className={cn(
        /* Ruled, per the application-variant record of 2026-09-18: 48px rows, a
           1px column separator between cells (`[&>*+*]:border-l`, so the first
           column keeps its edge against the container's own line), the ink at
           full strength, and a Paper hover. The 56px and the unruled body were
           the soft system's, kept through the token move; the record's Tables
           paragraph is the contract, and the canvas is not the source. */
        // Paper at full strength: `--muted` is Paper itself, and 40% of it over the white
        // panel (#f9fafa) is a hover nobody sees. `has-aria-expanded` keeps the row lit
        // while its «⋯» menu is open, so the open menu still says which row it belongs to.
        "h-12 border-b border-border transition-colors [&>*+*]:border-l [&>*+*]:border-border hover:bg-muted has-aria-expanded:bg-muted data-[state=selected]:bg-secondary",
        className
      )}
      {...props}
    />
  )
}

function TableHead({ className, ...props }: React.ComponentProps<"th">) {
  return (
    <th
      data-slot="table-head"
      className={cn(
        /* The one place in the app where a heading is smaller and quieter than the
           content under it: 12px Charcoal Blue with a little tracking, per the
           reference (design spec §4, and §5's exception for table headers). */
        "px-2 text-left align-middle text-xs font-medium tracking-wide whitespace-nowrap text-muted-foreground [&:has([role=checkbox])]:pr-0",
        className
      )}
      {...props}
    />
  )
}

function TableCell({ className, ...props }: React.ComponentProps<"td">) {
  return (
    <td
      data-slot="table-cell"
      className={cn(
        "px-2 py-3 align-middle whitespace-nowrap [&:has([role=checkbox])]:pr-0",
        className
      )}
      {...props}
    />
  )
}

function TableCaption({
  className,
  ...props
}: React.ComponentProps<"caption">) {
  return (
    <caption
      data-slot="table-caption"
      className={cn("mt-4 text-sm text-muted-foreground", className)}
      {...props}
    />
  )
}

export {
  ScrollRegion,
  Table,
  TableHeader,
  TableBody,
  TableFooter,
  TableHead,
  TableRow,
  TableCell,
  TableCaption,
}
