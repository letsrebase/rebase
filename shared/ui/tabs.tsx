"use client"

import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { Tabs as TabsPrimitive } from "radix-ui"

import { cn } from "./cn"

function Tabs({
  className,
  orientation = "horizontal",
  ...props
}: React.ComponentProps<typeof TabsPrimitive.Root>) {
  return (
    <TabsPrimitive.Root
      data-slot="tabs"
      data-orientation={orientation}
      className={cn(
        "group/tabs flex gap-2 data-horizontal:flex-col",
        className
      )}
      {...props}
    />
  )
}

/* Tabs are a thin rule under the active label, not a boxed list of pills (design
   spec §4). The list contributes only the hairline the active trigger's 2px underline
   sits on; `line` is the same thing without even that, for a row of tabs inside a
   surface that already has its own separator. */
const tabsListVariants = cva(
  "group/tabs-list inline-flex w-fit items-center gap-6 bg-transparent text-muted-foreground group-data-vertical/tabs:h-fit group-data-vertical/tabs:flex-col group-data-vertical/tabs:items-start group-data-vertical/tabs:gap-2",
  {
    variants: {
      variant: {
        default: "border-b border-border group-data-vertical/tabs:border-b-0 group-data-vertical/tabs:border-l",
        line: "",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
)

function TabsList({
  className,
  variant = "default",
  ...props
}: React.ComponentProps<typeof TabsPrimitive.List> &
  VariantProps<typeof tabsListVariants>) {
  return (
    <TabsPrimitive.List
      data-slot="tabs-list"
      data-variant={variant}
      className={cn(tabsListVariants({ variant }), className)}
      {...props}
    />
  )
}

function TabsTrigger({
  className,
  ...props
}: React.ComponentProps<typeof TabsPrimitive.Trigger>) {
  return (
    <TabsPrimitive.Trigger
      data-slot="tabs-trigger"
      className={cn(
        /* `-mb-px` pulls the trigger's own bottom border onto the list's hairline, so
           the active 2px rule replaces that line instead of stacking under it. The
           inactive border is transparent and the same width, so activating a tab moves
           nothing. Radix writes `data-state="active"` here, which is what the active
           rules match. */
        "relative -mb-px inline-flex items-center justify-center gap-1.5 border-b-2 border-transparent bg-transparent px-0.5 pb-2 text-[15px] whitespace-nowrap text-muted-foreground transition-colors group-data-vertical/tabs:w-full group-data-vertical/tabs:justify-start group-data-vertical/tabs:-ml-px group-data-vertical/tabs:mb-0 group-data-vertical/tabs:border-b-0 group-data-vertical/tabs:border-l-2 group-data-vertical/tabs:pb-0 group-data-vertical/tabs:pl-3 hover:text-foreground focus-visible:rounded-sm focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none disabled:pointer-events-none disabled:opacity-50 has-data-[icon=inline-end]:pr-1 has-data-[icon=inline-start]:pl-1 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
        "data-[state=active]:border-b-2 data-[state=active]:border-foreground data-[state=active]:font-medium data-[state=active]:text-foreground group-data-vertical/tabs:data-[state=active]:border-b-0 group-data-vertical/tabs:data-[state=active]:border-l-2",
        className
      )}
      {...props}
    />
  )
}

function TabsContent({
  className,
  ...props
}: React.ComponentProps<typeof TabsPrimitive.Content>) {
  return (
    <TabsPrimitive.Content
      data-slot="tabs-content"
      className={cn("flex-1 text-sm outline-none", className)}
      {...props}
    />
  )
}

export { Tabs, TabsList, TabsTrigger, TabsContent, tabsListVariants }
