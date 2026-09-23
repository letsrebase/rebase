# Print

The pieces rebase hands out on paper and skin: the business card and the stickers,
the temporary tattoo among them. Each is an HTML page in millimetres, drawn in the
picture system of the social carousels (`.claude/skills/social-content/`) and made
lighter for print: a full ground, the field of tiles in a corner, Outfit, the brand
colours, square corners. The pages read the lockup, the font and the echo logo from
`shared/brand` itself, so a change there shows up in the next render. The QR code
beside this file, `qr-letsrebase.svg`, is the one both pages print: `https://letsrebase.com`
at error correction M, made with `npx -y qrcode -t svg -e M https://letsrebase.com`,
then the `viewBox` narrowed to drop the quiet zone (the white padding around it on
the page is the quiet zone now) and the stroke recoloured to ink. Decode a rendered
PNG after any change to it.

The merch for events is roadmap #296. The design cards are REB-332 (business card)
and REB-334 (stickers and tattoo).

| Piece | Trim | With 3 mm bleed | px at 300 dpi, with bleed | Source |
|---|---|---|---|---|
| Business card, front and back | 55 × 84 mm, portrait | 61 × 90 mm | 720 × 1063 | `business-cards/business-cards.html` |
| «Ancora che fai il dipendente?» | 50 × 50 mm, gold ground | 56 × 56 mm | 661 × 661 | `stickers/stickers.html`, `employee` |
| «Hai dovuto prendere ferie per essere qua?» | 50 × 50 mm, ink ground | 56 × 56 mm | 661 × 661 | `stickers/stickers.html`, `day-off` |
| Echo logo | A8 landscape, 74 × 52 mm, gold ground | 80 × 58 mm | 945 × 685 | `stickers/stickers.html`, `echo` |
| Temporary tattoo, the four-tile mark | 40 × 40 mm sheet, 30 mm mark | 46 × 46 mm | 543 × 543 | `stickers/stickers.html`, `tattoo-mirrored` |

The safe area is 3 mm inside the trim everywhere. The corners stay square: a print
shop that rounds a sticker's corners does it in the die cut.

## Rendering

From the repository root, after `pnpm install`:

```bash
node shared/brand/print/stickers/render.mjs          # out/png, out/pdf
node shared/brand/print/business-cards/render.mjs    # out/png, out/business-cards.pdf
```

`--guides` on either script writes the same pictures to `out/guide/` with the trim
(grey) and the safe area (red) drawn over them, only for checking. Open an HTML page
in a browser with `?guides=1` to see the same thing live.

Each script writes into an `out/` folder beside its page, which git ignores:

- the PDF is **the file for the print shop**. It has one page per face or per sticker,
  bleed included, vector text and the font embedded;
- `png/<name>-with-bleed.png` is the same face at 300 dpi, for a shop that wants
  images;
- `png/<name>.png` is the face at the trim, for looking at it.

A finished piece's picture goes on roadmap #296, not into git. #296 is public: the
back of the business card goes there only as the `--placeholders` preview, and the
print file with the numbers goes to the print shop and the private Linear card, never
to GitHub.

Playwright is `@rebase/brand`'s own devDependency. The scripts cut every bitmap to its
exact pixel size with `sips`, because Chromium floors a fractional clip, so they run
on macOS.

Colours are RGB (ink `#011936`, gold `#f9dc5c`, melon `#ed254e`, quiet `#465362`,
spelled out from `palette.css` in each page); the print shop converts to CMYK.

## The phone numbers on the business card

The back of the card carries two phone numbers, Ivan's and Lorenzo's. This repository
is public, so the numbers are not in it. `business-cards/contacts.local.js`, ignored
by git, holds them: copy `contacts.example.js` and fill it in. Without that file, or
with a number that does not look like one (`+` and digits), the back shows
`+39 ··· ·· ·· ···` and `render.mjs` stops before writing anything. `--placeholders`
renders a preview on purpose: the page ignores `contacts.local.js` even when it is
there, and everything goes to `out/preview/`, so a preview never lands on the print
file's path and never carries the real numbers. The folder is also in
`.dockerignore`, so an image built on a machine that holds the file does not copy it
into a build stage.

## What has been printed

- **Business cards**, 100 matte laminated, MOO, ordered 2026-09-22. That batch
  carries the lockup from before the graft: the graft (`1b518d4c6`) merged the same
  afternoon, after the order. A render from this folder draws today's lockup. To
  reprint the September card exactly, render with the lockup from `2de656bca`
  (`git show 2de656bca:shared/brand/lockup.svg`, and `lockup-paper.svg`).
- **Temporary tattoos**, 20 pieces at 3 × 3 cm, Yatatu, ordered 2026-09-22, from
  `tattoo-mirrored`. The file is already mirrored: transfer paper prints the design
  reversed, and on the skin it reads the right way round, gold top right and melon
  bottom left, the tile order of `BRAND_TILES` in `mark.ts`. White is clear on
  transfer paper.
- **Stickers**: not ordered as of 2026-09-23.

## Not here

- **The embroidered t-shirts, hoodies and caps** (Spreadshirt, 2026-09-15) have no
  source in this folder: nothing of them was drawn here.
- **The three echo grounds that lost**: white, watermelon, and ink with a keyline.
  They were tried on 2026-09-22 against the gold one that `echo` prints. REB-334
  records the choice.
