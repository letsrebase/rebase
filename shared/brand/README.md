# @rebase/brand

The palette, the typeface, the four-tile mark, the wordmark, and the contrast maths
every surface measures its colour pairs with. One source, no copies, read by every
surface.

| File | What it holds | Who reads it |
|---|---|---|
| `palette.css` | Six colours and `--font-sans`, in a Tailwind `@theme` block | The CRM's `tokens.css` imports it; the website extracts it at build time |
| `font.css` + `fonts/` | Outfit, self-hosted, one variable file for the 300-700 range | Both surfaces |
| `mark.ts` | The order of the four tiles, and how each surface names them | Both surfaces' tests |
| `contrast.ts` | WCAG's ratio, the sRGB blend and the palette reader, with the AA thresholds named | The site's token test, the hub's, and `@rebase/ui`'s |
| `wordmark.svg` | «rebase», as outlines | Any surface that shows the name, and every export |
| `lockup.svg` | The mark at cap height, then the word | The same, plus the social pictures |
| `wordmark-paper.svg`, `lockup-paper.svg` | The same two on a dark ground | The landing's dark bands, a dark slide |
| `tools/build-wordmark.py` | How the four SVGs were drawn, and the only way to redraw them | Nobody at build time: run it by hand when the face changes |
| `echo/` | The echo logo: «rebase» solid under three outlined copies of itself, six colourways as PNG | Covers, slides, social pictures; nothing yet at header size |
| `tools/build-echo.mjs` | How the six PNGs were drawn, and the only way to redraw them | Nobody at build time: `pnpm --filter @rebase/brand build:echo` |

## Why a package rather than a file in one of the projects

The CRM is built with Tailwind and the website deliberately has none, so they cannot
share a stylesheet: the application consumes `@theme` and generates utilities from it,
while the website reads the same declarations as text and injects them as plain custom
properties. What they must share is the *values*. Keeping those in either project would
make one of them depend on the other, and keeping a copy in each is the drift this
package exists to prevent.

## What is deliberately not here

`--radius`. The website's visual system is square by design: tiles, hard edges and a
stepped shadow, with no rounded corner anywhere. The radius scale is the application's
own and lives in its `tokens.css`. It used to arrive here by accident, along with the
26 semantic re-exports of `@theme inline`, back when the website extracted them out of
the application's own stylesheet and used none of them.

## Changing a colour

Edit `palette.css`. Both surfaces pick it up from the same declaration, and three test
suites will tell you if something drifted: the application's `tokens.test.ts` checks
contrast ratios against the values, the website's `landing-tokens.test.ts` checks that
no stylesheet restates them, and `palette-plugin.test.ts` asserts the exact set of
seven tokens the website receives.

## The wordmark, and why it is not a font

The name is set in Space Grotesk 700 at -0.035em, decided on 2026-09-14
(`docs/design/DECISIONS.md`), and it ships as outlines. Serving a second webfont would
put 20-odd KB on the critical path of every page and let the one string a visitor uses
to tell where they are arrive late and reflow. So `--font-sans` stays Outfit and the
logotype is a picture.

A picture still has to say the name. Inlined, each file's own `role="img"` and `<title>`
carry it. Referenced as an `<img>` those are outside the page's accessibility tree and
are ignored, so that consumer writes `alt="rebase"`; as a `background-image` the name
has to be in text beside it.

`wordmark.svg` is the word alone, `lockup.svg` is the mark and the word together, with
the tile at half the cap height and the gap at three quarters of the tile, so a consumer
sets one width and the pair holds. Neither carries a `width` or a `height`: the same file
is the 18px chip in the header and a 1584px LinkedIn cover. All four spell their colours
as literal hex, the way `orbiters-logo.svg` already does, because an SVG opened as a file
resolves no custom property; what is new is that `landing-style.test.ts` holds those
hexes to `palette.css`, the four tiles to `BRAND_TILES`, and the geometry to the
proportions above.

`wordmark-paper.svg` and `lockup-paper.svg` are the same geometry for a dark ground:
the word in `--color-paper`, and in the lockup the two ink tiles too, since on Prussian
Blue those tiles *are* the ground, which is the mark's own rule (`mark.ts`). Two files
rather than a CSS override because a surface that uses the asset as an `<img>` or a
`background-image` cannot recolour it, and the landing already alternates dark bands.

## Changing the face, the weight or the tracking

Edit the three constants at the top of `tools/build-wordmark.py` and run it. It fetches
the variable font from google/fonts, pinned to the commit the SVGs were drawn from and
refused unless it checksums to `FONT_SHA256`, instances the weight, shapes the word
through HarfBuzz so the kerning is the font's own, and rewrites all four SVGs. Never
hand-edit an SVG: the next run would silently undo it, and the `wordmark` check in
`.github/preflight.json` reruns `--check` locally, before the push, on any diff
touching `shared/brand/**`, failing while it stands, byte for byte, without writing
anything. The source font is not committed, because nothing serves it and the
artefacts are the files it produces.

## The echo logo

Ivan chose it on 2026-09-15 (ORB-199, committed by ORB-200): the word solid, and above it
three outlined copies of the word stacked like cut-outs, each one hiding what of the
copies behind it falls inside its own letters. It reads «rebase» and it looks like a
branch being replayed, which the plain wordmark does not say.

| File | On | Word | Copies |
|---|---|---|---|
| `echo/echo-ink-watermelon-outlines.png` | white and light grounds, **the default** | `--color-prussian-blue` | `--color-watermelon` |
| `echo/echo-white.png` | dark grounds, **the default** | white | white |
| `echo/echo-ink.png` | light grounds, one colour | `--color-prussian-blue` | `--color-prussian-blue` |
| `echo/echo-watermelon.png` | light grounds, one colour | `--color-watermelon` | `--color-watermelon` |
| `echo/echo-watermelon-white-outlines.png` | dark grounds | `--color-watermelon` | white |
| `echo/echo-black.png` | print, and a light ground that is not ours, one colour | black | black |

All six are transparent, 2572x1222, and drawn by `tools/build-echo.mjs` from
`palette.css` and the committed woff2, so a change of colour or of face is a redraw and
never an edit in a design tool. The script's header lists the rules that make it look
right (the outline traced from the letter's edge rather than stroked, and what a letter
covers), and a directory as its argument renders there without touching the committed
files. Glyph rasterisation differs between macOS and Linux, so a redraw on another
machine is a binary diff with no visible change: quote the chromium line it prints.

Two things are still open on ORB-199, deliberately not settled here. The echo is set in
Outfit 700, while the wordmark above is Space Grotesk 700 (ORB-197), so one of the two
faces has to give. And the stack is a picture: at the 18px of the header chip the copies
crowd the word, so the lockup, the header and an avatar need a compact variant.
