import { cn } from '@rebase/ui/cn'

/**
 * The four tiles that stand before the name, everywhere the product signs itself:
 * the app, the landing (`.glyph` in landing.css and community.css) and the community
 * page's favicon (`landing/rebase-logo.svg`). Ink, Royal Gold, Watermelon, ink -- the
 * field at glyph scale. `bg-foreground` rather than the literal Prussian Blue so
 * the two ink tiles stay visible in dark mode, where the ground is Prussian Blue.
 *
 * Decorative: it never carries the name, so it is hidden from assistive technology.
 */
export function BrandMark({ className }: { className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={cn('inline-grid size-3 shrink-0 grid-cols-2 grid-rows-2', className)}
    >
      <span className="bg-foreground" />
      <span className="bg-[var(--color-royal-gold)]" />
      <span className="bg-[var(--color-watermelon)]" />
      <span className="bg-foreground" />
    </span>
  )
}
