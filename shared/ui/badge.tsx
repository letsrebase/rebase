import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { Slot } from "radix-ui"

import { cn } from "./cn"

const badgeVariants = cva(
  "group/badge inline-flex h-5 w-fit shrink-0 items-center justify-center gap-1 overflow-hidden rounded-4xl border border-transparent px-2 py-0.5 text-xs font-medium whitespace-nowrap transition-all focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 has-data-[icon=inline-end]:pr-1.5 has-data-[icon=inline-start]:pl-1.5 aria-invalid:border-destructive aria-invalid:ring-destructive/20 [&>svg]:pointer-events-none [&>svg]:size-3!",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground [a]:hover:bg-primary/80",
        secondary:
          "bg-secondary text-secondary-foreground [a]:hover:bg-secondary/80",
        destructive:
          "bg-destructive/10 text-destructive focus-visible:ring-destructive/20 [a]:underline-offset-4 [a]:hover:underline",
        outline:
          "border-border text-foreground [a]:hover:bg-muted [a]:hover:text-muted-foreground",
        ghost:
          "hover:bg-muted hover:text-muted-foreground",
        link: "text-primary underline-offset-4 hover:underline",
        /* The status pill of the reference screenshots (design spec §4): fully round,
           12px/500, 2px 10px of padding, on Paper rather than on a colour of its own.
           The state it reports is carried by the label and, optionally, by `dot`; the
           chip itself stays quiet because a table shows dozens of them at once. */
        pill: "rounded-full bg-paper px-2.5 py-0.5 text-xs font-medium text-foreground",
        /* A badge that reads as a progress bar (REB-1130): the background fills from the
           left up to `fill`, and nothing is filled without one. A 2px ink rail runs along
           the bottom edge for exactly as far as the fill does, so the level is carried by a
           line that is 16.8:1 on the ground and not by a hue that is 1.36:1 against it, and
           no line crosses the label. The label is ink over both halves at AA. The tokens it
           reads are in tokens.css, with the contrast they were chosen for. */
        meter:
          "border-border bg-(--meter-ground) text-foreground bg-no-repeat bg-position-[left_bottom,left_top] bg-size-[100%_var(--line-strong),100%_100%] bg-[linear-gradient(to_right,var(--foreground)_var(--meter-level,0%),transparent_var(--meter-level,0%)),linear-gradient(to_right,var(--meter-fill)_var(--meter-level,0%),var(--meter-ground)_var(--meter-level,0%))]",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
)

/**
 * The tint of the 6px dot a badge can carry before its label. `ink` and `muted` read
 * the semantic slots (the ink, and the ink faded to Charcoal Blue); `accent` reads the
 * --accent slot, which is Royal Gold today and follows it if it is ever re-pointed;
 * `gold` and `danger` name the tint itself, for a dot that must stay Royal Gold or
 * Watermelon whatever the slots do. No sixth colour: these are the five tints.
 */
const BADGE_DOTS = {
  ink: "bg-foreground",
  muted: "bg-muted-foreground",
  accent: "bg-accent",
  danger: "bg-watermelon",
  gold: "bg-royal-gold",
} as const

type BadgeDot = keyof typeof BADGE_DOTS

/**
 * How much of a `meter` badge is filled: a third, two thirds or all of it. Three steps by
 * design: the badge says a rough level of something that is not a number, and a fourth
 * step would be a precision nobody has. Without one the badge is unfilled.
 */
const BADGE_FILLS = {
  third: "[--meter-level:var(--meter-third)]",
  "two-thirds": "[--meter-level:var(--meter-two-thirds)]",
  full: "[--meter-level:var(--meter-full)]",
} as const

type BadgeFill = keyof typeof BADGE_FILLS

/**
 * `dot` and `asChild` are mutually exclusive by type, not by runtime check: with
 * `asChild` the badge renders a Slot, which takes exactly one child, and a dot would
 * make two. A caller that needs both can put the dot in its own child element.
 */
type BadgeProps = React.ComponentProps<"span"> &
  VariantProps<typeof badgeVariants> &
  ({ asChild?: false; dot?: BadgeDot } | { asChild: true; dot?: never }) & {
    /** Only the `meter` variant reads it. */
    fill?: BadgeFill
  }

function Badge({
  className,
  variant = "default",
  asChild = false,
  dot,
  fill,
  children,
  ...props
}: BadgeProps) {
  const Comp = asChild ? Slot.Root : "span"

  return (
    <Comp
      data-slot="badge"
      data-variant={variant}
      data-fill={fill}
      className={cn(badgeVariants({ variant }), fill && BADGE_FILLS[fill], className)}
      {...props}
    >
      {dot ? (
        <span
          data-slot="badge-dot"
          /* Decoration, not information: the label beside it says the same thing in
             words, so a reader who cannot separate two hues has lost nothing. */
          aria-hidden="true"
          className={cn("size-1.5 shrink-0 rounded-full", BADGE_DOTS[dot])}
        />
      ) : null}
      {children}
    </Comp>
  )
}

export { Badge, badgeVariants }
export type { BadgeDot, BadgeFill, BadgeProps }
