/**
 * The four tiles the product signs itself with, in reading order.
 *
 * Every rebase surface draws this mark in its own technology and they must not
 * drift: the application composes it out of Tailwind classes (`BrandMark.tsx`), the
 * website draws it as four box-shadows on one element
 * (`.glyph` in `projects/website/src/system.css`), and rebase's favicon is the same
 * field at glyph scale (`rebase-logo.svg`).
 *
 * Three surfaces drawing the same thing three ways is exactly where a silent fork
 * happens, so the order lives here once and each surface asserts against it. Before
 * the website became its own project, the check was one of its tests reaching into the
 * application's `BrandMark.tsx` and reading its JSX as text: that only worked while
 * the two shared a directory, and it made the website depend on a React component it
 * cannot import.
 *
 * `ink` is `--color-prussian-blue` on the website and `bg-foreground` in the
 * application, which is the same colour on a light ground and stays legible on a dark
 * one, where the ground is Prussian Blue itself.
 */
export const BRAND_TILES = ['ink', 'royal-gold', 'watermelon', 'ink'] as const

export type BrandTile = (typeof BRAND_TILES)[number]

/** How the application names each tile as a Tailwind class. */
export const BRAND_TILE_CLASSES: Record<BrandTile, string> = {
  ink: 'bg-foreground',
  'royal-gold': 'bg-[var(--color-royal-gold)]',
  watermelon: 'bg-[var(--color-watermelon)]',
}

/** How the website names each tile inside `.glyph`'s box-shadow. */
export const BRAND_TILE_VARS: Record<BrandTile, string> = {
  ink: 'var(--color-prussian-blue)',
  'royal-gold': 'var(--color-royal-gold)',
  watermelon: 'var(--color-watermelon)',
}
