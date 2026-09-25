import { cn } from './cn'

/**
 * The loading state, drawn from the brand's own four-tile mark instead of a fifth
 * shape: "Replay" (Claude Design canvas c0ee076a-22c6-40f8-8707-4a71b1485340,
 * 2026-09-25, Lorenzo: "replay"). The tiles light one at a time in the mark's own
 * reading order -- ink, royal gold, watermelon, ink -- 200ms each, an 800ms loop, so
 * the mark is only ever partly dimmed and reads as itself for the whole cycle. The
 * alternative drawn alongside it, a synchronised pulse, was rejected because every
 * tile dims together and the mark briefly stops reading as the mark right when it
 * has to say "still working".
 *
 * `motion-reduce:` strips every tile back to the static, fully-lit mark
 * `BrandMark.tsx` draws, so a visitor who has asked for less motion sees the plain
 * glyph rather than a loader that never moves and never rests.
 *
 * The four classes are written out, the same way `BrandMark.tsx` writes them, rather
 * than built from `BRAND_TILE_CLASSES` at render time: Tailwind's scanner extracts
 * candidates from source text, never by executing JS, and `@rebase/brand/mark.ts`
 * sits outside every `@source` this package declares, so a class assembled from its
 * exported map resolves to nothing here (measured 2026-09-25: `bg-[var(--color-royal-
 * gold)]` computed to `rgba(0,0,0,0)`, watermelon rendered invisible with it). The
 * order and the colours are still the mark's own -- `loader.test.tsx` and the
 * gallery's e2e suite both assert against `BRAND_TILES`/`BRAND_TILE_CLASSES` so the
 * two cannot drift.
 *
 * The per-tile delay is a negative offset into `rebase-loader-replay`'s 800ms cycle
 * (`tokens.css`): a negative `animation-delay` of `-Xms` starts the tile as though it
 * had already been playing for `Xms`, so its own peak (the keyframe's `0%`/`100%`)
 * lands at `t = (800 - X) mod 800` on the shared clock, not at `t = X` -- reviewed
 * 2026-09-25 after the first pass swapped the second and fourth tile's peaks (gold at
 * 600ms instead of 200ms) by assigning delays in reading order rather than by this
 * math. Reading order stays 0ms, 200ms, 400ms, 600ms; the delays that produce it are
 * 0, -600, -400, -200.
 *
 * Sized like `BrandMark`: `size-3` inline by default, meant to sit wherever the mark
 * already sits (before a name, in a button, in a row). Pass a larger `className`
 * (`size-12` and up) for a standalone, full-panel wait.
 *
 * Decorative, same as `BrandMark`: it never carries a name, so whatever it sits
 * beside, or whatever announces the wait through `aria-live`, supplies the
 * accessible text.
 */
const TILE = 'animate-loader-replay motion-reduce:animate-none motion-reduce:opacity-100'

export function Loader({ className }: { className?: string }) {
  return (
    <span
      data-slot="loader"
      aria-hidden="true"
      className={cn('inline-grid size-3 shrink-0 grid-cols-2 grid-rows-2', className)}
    >
      {/* Peaks at 0ms: no delay needed, this tile's local clock already starts at its
          own 0%. */}
      <span className={cn(TILE, 'bg-foreground')} style={{ animationDelay: '0ms' }} />
      {/* Peaks at 200ms: delay = -(800 - 200) = -600ms. */}
      <span
        className={cn(TILE, 'bg-[var(--color-royal-gold)]')}
        style={{ animationDelay: '-600ms' }}
      />
      {/* Peaks at 400ms: delay = -(800 - 400) = -400ms, the cycle's own midpoint, so
          this is the one tile where reading order and the naive assignment agree. */}
      <span
        className={cn(TILE, 'bg-[var(--color-watermelon)]')}
        style={{ animationDelay: '-400ms' }}
      />
      {/* Peaks at 600ms: delay = -(800 - 600) = -200ms. */}
      <span className={cn(TILE, 'bg-foreground')} style={{ animationDelay: '-200ms' }} />
    </span>
  )
}
