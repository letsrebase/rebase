#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["fonttools>=4.60", "uharfbuzz>=0.56"]
# ///
"""Draw the wordmark, as outlines, from the face the brand decided on.

The word «rebase» is set once here and committed as `wordmark.svg` and `lockup.svg`
next to this script, so nothing downstream loads a second webfont: the two surfaces
that draw the brand use the paths, and `--font-sans` (Outfit) stays the only family
the product ships. That also removes the one FOUT nobody can accept, on the string a
visitor uses to tell whether they are on the right site.

The face is Space Grotesk 700 (SIL OFL 1.1, Florian Karsten), picked on 2026-09-14
(`docs/design/DECISIONS.md`). The source is the variable font from google/fonts, which
this script fetches and checksums rather than committing: the artefacts are the SVGs,
and a font nobody serves has no business in the dependency graph. Run it only when the
face, the weight or the tracking changes.

    ./shared/brand/tools/build-wordmark.py            # rewrite the four SVGs
    ./shared/brand/tools/build-wordmark.py --check    # fail if they are not what this draws

Kerning comes from the font's own GPOS through HarfBuzz, not from an average
letter-spacing: at display size the `re` and `se` pairs are what a hand-spaced wordmark
would be judged on.
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from io import BytesIO
from pathlib import Path
from tempfile import gettempdir

import uharfbuzz as hb
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

# Pinned to the commit that last touched this blob, not to `main`: the checksum below
# would otherwise start refusing the file the day Google ships a revision, with no way
# left to fetch the bytes these SVGs were drawn from.
FONT_URL = (
    "https://raw.githubusercontent.com/google/fonts/"
    "2861cb7b12f90c0a294a12ed666e381e2211872f/ofl/spacegrotesk/SpaceGrotesk%5Bwght%5D.ttf"
)
FONT_SHA256 = "acad6de1fc93436f5c0f1f4137751ef04f1aea3063e7036535970ffcfbd79f72"

WORD = "rebase"
WEIGHT = 700
# -0.035em. A wordmark is read as one shape, so it is set tighter than the same face
# would be in a paragraph; past about -0.05em the `rb` pair starts to touch.
TRACKING = -0.035

BRAND = Path(__file__).resolve().parent.parent
PALETTE = BRAND / "palette.css"


def source_font() -> bytes:
    """The variable TTF, fetched and checksummed, cached outside the repository."""
    cache = Path(gettempdir()) / f"SpaceGrotesk-{FONT_SHA256[:12]}.ttf"
    if cache.is_file() and hashlib.sha256(cache.read_bytes()).hexdigest() == FONT_SHA256:
        return cache.read_bytes()
    # A timeout, because everything else here fails in milliseconds and a stalled
    # connection would otherwise hang a script a person or an agent runs by hand.
    with urllib.request.urlopen(FONT_URL, timeout=30) as response:
        data = response.read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != FONT_SHA256:
        raise SystemExit(f"{FONT_URL} is not the font this wordmark was drawn from: {digest}")
    cache.write_bytes(data)
    return data


def palette(token: str) -> str:
    """One colour, read from the single source rather than spelled out here."""
    for line in PALETTE.read_text(encoding="utf-8").splitlines():
        name, _, value = line.strip().partition(":")
        if name == token:
            return value.strip().rstrip(";")
    raise SystemExit(f"{token} is not in {PALETTE.name}")


Placed = list[tuple[str, float, float]]
Box = tuple[float, float, float, float]


def shape(font_bytes: bytes) -> Placed:
    """Every glyph of the word with the pen origin it is drawn from."""
    face = hb.Face(font_bytes)
    hb_font = hb.Font(face)
    hb_font.set_variations({"wght": WEIGHT})
    buf = hb.Buffer()
    buf.add_str(WORD)
    buf.guess_segment_properties()
    hb.shape(hb_font, buf)

    upem = face.upem
    tracking = TRACKING * upem
    order = hb_font.glyph_to_string
    placed: Placed = []
    pen_x = 0.0
    for info, pos in zip(buf.glyph_infos, buf.glyph_positions, strict=True):
        placed.append((order(info.codepoint), pen_x + pos.x_offset, pos.y_offset))
        pen_x += pos.x_advance + tracking
    return placed


def word_path(font: TTFont, placed: Placed) -> tuple[str, Box]:
    """The whole word as one `d`, in SVG coordinates, plus its tight bounding box."""
    glyphs = font.getGlyphSet()
    parts: list[str] = []
    bounds = BoundsPen(glyphs)
    for name, x, y in placed:
        pen = SVGPathPen(glyphs, ntos=lambda v: f"{round(v, 1):g}")
        # SVG's y grows downwards, the font's upwards: flip here and draw from a
        # baseline at y=0, which the caller then offsets to start the box at the origin.
        transform = (1, 0, 0, -1, x, -y)
        glyphs[name].draw(TransformPen(pen, transform))
        glyphs[name].draw(TransformPen(bounds, transform))
        if commands := pen.getCommands():
            parts.append(commands)
    if bounds.bounds is None:
        raise SystemExit(f"«{WORD}» drew nothing: the font has no outline for it")
    return " ".join(parts), bounds.bounds


def draw() -> dict[str, str]:
    """The four files, by name, as they should be on disk."""
    font_bytes = source_font()
    placed = shape(font_bytes)
    font = instantiateVariableFont(TTFont(BytesIO(font_bytes)), {"wght": WEIGHT})
    d, box = word_path(font, placed)
    ink = palette("--color-prussian-blue")
    paper = palette("--color-paper")
    gold = palette("--color-royal-gold")
    watermelon = palette("--color-watermelon")

    # Round the extremes, not their difference: every path coordinate is rounded to
    # one decimal on its own, so a viewBox measured off the unrounded bounds can end
    # up a tenth short of the coordinate actually drawn.
    x_min, y_min, x_max, y_max = (round(value, 1) for value in box)
    width = round(x_max - x_min, 1)

    # The lockup: the mark standing on the baseline, cap height tall, then the word.
    # The tile is half the cap height, the proportion the mark already has beside the
    # header's type (12px of 18px). The gap is display spacing and is tighter than the
    # header chip's, which is CSS this asset replaces once a surface uses it.
    cap = font["OS/2"].sCapHeight
    tile = round(cap / 2, 1)
    gap = round(tile * 0.75, 1)

    # The word's own box, and the lockup's, which is the union of the word and a mark
    # standing on the baseline: taking the word's height alone would clip the tiles for
    # any word whose tallest ink sits below the cap line.
    word_height = round(y_max - y_min, 1)
    lockup_top = min(y_min, -cap)
    lockup_height = round(max(y_max, 0) - lockup_top, 1)
    lockup_width = round(cap + gap + width, 1)
    word_x = round(cap + gap - x_min, 1)
    baseline_y = round(-lockup_top, 1)

    # The graft (direction A, docs/design/DECISIONS.md, 2026-09-22): rebase replaying
    # a commit onto a new parent, drawn in the mark's own square tiles rather than a
    # stroke, so "no radius" holds for this device too. A solid staircase, one tile
    # tighter each step, rises from under the mark's own left edge to a line that then
    # runs on, as a dotted rule, under the whole word: the branch being grafted, then
    # the base it lands on. Quiet, per the decision: every square is `colour`, the
    # same ink the word and the lockup's own ink tiles already stand in, so the device
    # never needs a fifth colour or the accent square the card drew in Watermelon.
    graft_unit = round(tile * 0.16, 1)
    graft_rise = round(tile * 0.22, 1)
    graft_pitch = round(tile * 0.34, 1)
    graft_row = round(baseline_y + graft_rise, 1)
    graft_bottom = round(graft_row + 3 * graft_unit, 1)
    lockup_box_height = round(max(lockup_height, graft_bottom), 1)

    def svg(box_width: float, box_height: float, body: str, note: str) -> str:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {box_width:g} {box_height:g}" role="img" aria-label="{WORD}">\n'
            f"  <!-- {note}\n"
            f"       Generated by tools/build-wordmark.py; edit that, never this file. -->\n"
            f"  <title>{WORD}</title>\n"
            f"{body}"
            "</svg>\n"
        )

    def word(colour: str, x: float, baseline: float) -> str:
        return f'  <path transform="translate({x:g} {baseline:g})" fill="{colour}" d="{d}"/>\n'

    def mark(ink_tile: str, top: float) -> str:
        # BRAND_TILES order: ink, royal gold, watermelon, ink. On a dark ground the two
        # ink tiles are the ground, which is the mark's own rule (mark.ts), so the paper
        # variant repaints them rather than inventing a fifth colour.
        # crispEdges on the group, as orbiters-logo.svg carries it on its root: the
        # tiles share edges with no overlap, so antialiasing paints a blended hairline
        # along the two seams at small sizes. Not on the root here, which would alias
        # the letterforms too.
        tiles = [(0, 0, ink_tile), (tile, 0, gold), (0, tile, watermelon), (tile, tile, ink_tile)]
        rects = "\n".join(
            f'    <rect x="{tx:g}" y="{round(top + ty, 1):g}" '
            f'width="{tile:g}" height="{tile:g}" fill="{fill}"/>'
            for tx, ty, fill in tiles
        )
        return f'  <g shape-rendering="crispEdges">\n{rects}\n  </g>\n'

    def graft(colour: str) -> str:
        # The riser: three squares, one tile tighter each step, standing under the
        # mark's own left edge and stepping up to the line below the word. The run:
        # the same squares, spaced out into a dotted line, filling the rest of the
        # lockup's width. One shape, drawn twice at two rhythms, never a stroke.
        riser = [
            (round(step * graft_unit, 1), round((2 - step) * graft_unit, 1)) for step in range(3)
        ]
        dots = list(riser)
        x = round(riser[-1][0] + graft_pitch, 1)
        while x + graft_unit <= lockup_width:
            dots.append((x, 0.0))
            x = round(x + graft_pitch, 1)
        rects = "\n".join(
            f'    <rect x="{sx:g}" y="{round(graft_row + sy, 1):g}" '
            f'width="{graft_unit:g}" height="{graft_unit:g}" fill="{colour}"/>'
            for sx, sy in dots
        )
        return f'  <g shape-rendering="crispEdges">\n{rects}\n  </g>\n'

    word_note = f"«{WORD}» in Space Grotesk {WEIGHT} at {TRACKING:g}em, as outlines."
    lockup_note = (
        f"The mark and the word, one file: the four tiles at cap height, then\n"
        f"       «{WORD}» in Space Grotesk {WEIGHT}, then the graft (direction A,\n"
        f"       docs/design/DECISIONS.md, 2026-09-22): a staircase of the same\n"
        f"       square tiles, running on as a dotted line under the word. The tile\n"
        f"       order is BRAND_TILES in mark.ts, the colours are palette.css and\n"
        f"       the proportions are the README's; all three, plus the graft's own\n"
        f"       tiles, are asserted by projects/website/src/landing-style.test.ts."
    )
    on_dark = " The paper cut, for a dark ground."

    def lockup(colour: str, note: str) -> str:
        body = (
            mark(colour, round(-cap - lockup_top, 1))
            + word(colour, word_x, baseline_y)
            + graft(colour)
        )
        return svg(lockup_width, lockup_box_height, body, note)

    return {
        "wordmark.svg": svg(width, word_height, word(ink, -x_min, -y_min), word_note),
        "wordmark-paper.svg": svg(
            width, word_height, word(paper, -x_min, -y_min), word_note + on_dark
        ),
        "lockup.svg": lockup(ink, lockup_note),
        "lockup-paper.svg": lockup(paper, lockup_note + on_dark),
    }


def main() -> None:
    files = draw()
    stale = [name for name, content in files.items() if (BRAND / name).read_text() != content]
    if "--check" in sys.argv[1:]:
        if stale:
            raise SystemExit(
                f"{', '.join(stale)}: not what this script draws. "
                "Run ./shared/brand/tools/build-wordmark.py and commit the result."
            )
        print(f"{len(files)} files are what this script draws")
        return
    for name, content in files.items():
        (BRAND / name).write_text(content, encoding="utf-8")
    print(f"{len(files)} files written, {len(stale)} of them changed")


if __name__ == "__main__":
    main()
