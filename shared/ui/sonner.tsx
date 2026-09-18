import { Toaster as Sonner, toast, type ToasterProps } from "sonner"
import { CircleCheckIcon, InfoIcon, TriangleAlertIcon, OctagonXIcon, Loader2Icon } from "lucide-react"

// `light`, not next-themes' `system`: the application has one colour scheme since
// 2026-09-18 (REB-298), and `system` handed sonner the visitor's OS preference, which
// was the one remaining path to a dark surface in a product with no dark tokens left.
const Toaster = ({ ...props }: ToasterProps) => {
  return (
    <Sonner
      theme="light"
      className="toaster group"
      icons={{
        success: (
          <CircleCheckIcon className="size-4" />
        ),
        info: (
          <InfoIcon className="size-4" />
        ),
        warning: (
          <TriangleAlertIcon className="size-4" />
        ),
        error: (
          <OctagonXIcon className="size-4" />
        ),
        loading: (
          <Loader2Icon className="size-4 animate-spin" />
        ),
      }}
      style={
        {
          "--normal-bg": "var(--popover)",
          "--normal-text": "var(--popover-foreground)",
          "--normal-border": "var(--border)",
          "--border-radius": "var(--radius)",
        } as React.CSSProperties
      }
      toastOptions={{
        classNames: {
          // The step shadow, like every other surface that floats: sonner's own
          // stylesheet hardcodes `box-shadow: 0 4px 12px rgba(0,0,0,.1)` on
          // `[data-sonner-toast]` and exposes no variable for it, so the only way to
          // reach it is a class with more specificity than a single attribute
          // selector, which `shadow-md!` is. Without this the one floating surface in
          // the product with a blur is the toast.
          toast: "cn-toast shadow-md! ring-1 ring-foreground/10",
        },
      }}
      {...props}
    />
  )
}

/**
 * `toast` is re-exported rather than imported from `sonner` directly by each caller,
 * and that is not a convenience. The toast queue is module state inside sonner: two
 * resolutions of the package mean the `<Toaster>` rendered here listens to one queue
 * while `toast()` pushes onto the other, so every toast in the application silently
 * stops appearing, with a green build and a green suite. One import path is what makes
 * that impossible: the application declares no `sonner` of its own.
 */
export { Toaster, toast }
