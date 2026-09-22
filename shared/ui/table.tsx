import * as React from "react"

import { cn } from "./cn"

function Table({ className, ...props }: React.ComponentProps<"table">) {
  return (
    <div
      data-slot="table-container"
      className="relative w-full overflow-x-auto"
    >
      <table
        data-slot="table"
        className={cn("w-full caption-bottom text-sm", className)}
        {...props}
      />
    </div>
  )
}

function TableHeader({ className, ...props }: React.ComponentProps<"thead">) {
  return (
    <thead
      data-slot="table-header"
      /* The header row is shorter than the data rows it labels (40px against 48px),
         and closes on the record's 2px rule ("a 2px rule under the header", the
         application-variant record of 2026-09-18). A descendant selector, so it beats
         TableRow's own `h-12` on specificity without TableRow having to know it is in
         a header. */
      className={cn("[&_tr]:h-10 [&_tr]:border-b-2", className)}
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
  Table,
  TableHeader,
  TableBody,
  TableFooter,
  TableHead,
  TableRow,
  TableCell,
  TableCaption,
}
