# /// script
# requires-python = ">=3.12"
# dependencies = ["pypdfium2==5.15.2", "pypdf==7.2.0", "reportlab==5.1.2", "pillow==13.0.0"]
# ///
"""Turn a signature into a transparent PNG, and stamp it on a PDF's signature line.

    uv run tooling/google-docs/sign.py prepare signature.pdf ~/.config/rebase/signatures/me.png
    uv run tooling/google-docs/sign.py prepare photo.jpg ~/.config/rebase/signatures/me.png
    uv run tooling/google-docs/sign.py sign quote.pdf --signature ~/.config/rebase/signatures/me.png

`prepare` takes either a PDF whose first page carries only the signature (a blank page
signed in macOS Preview, «Strumenti > Firma», is the cleanest source) or a photo of a
signature on white paper in even light. It keeps only the ink: the paper level is read
from the brightest pixels, everything near it becomes transparent, the strokes keep their
darkness as opacity in one ink colour, and the result is cropped to them.

`sign` finds the anchor (`Firma:` by default, case-sensitive, the line every rebase
document ends with) on the last page that carries it, and lays the signature to the right
of the anchor, resting on its baseline over the underline. The height is capped so the
strokes stay below the line of text above. It refuses when the anchor is missing, appears
more than once on that page, or leaves no room to its right, so a signature never lands in
the wrong place. The output defaults to `<name> · firmato.pdf` beside the input.

The signature image is personal: it stays outside the repository and outside Drive (the
`google-docs` skill says where), and it is applied only when its owner asks for that
document.
"""

import argparse
import io
import os
import sys
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageFilter, ImageOps
from pypdf import PdfReader, PdfWriter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

INK = (20, 33, 61)  # dark blue-black, the colour of a ballpoint pen


def load_grey(source: Path) -> Image.Image:
    if source.suffix.lower() == ".pdf":
        page = pdfium.PdfDocument(source)[0]
        return page.render(scale=600 / 72, draw_annots=True).to_pil().convert("L")
    image = ImageOps.exif_transpose(Image.open(source)).convert("L")
    if max(image.size) > 2400:
        image.thumbnail((2400, 2400))
    return image.filter(ImageFilter.MedianFilter(3))


def level_at(histogram: list[int], share: float, from_bright: bool) -> int:
    total, seen = sum(histogram), 0
    levels = range(255, -1, -1) if from_bright else range(256)
    for level in levels:
        seen += histogram[level]
        if seen >= total * share:
            return level
    return 0 if from_bright else 255


def prepare(source: Path, out: Path) -> None:
    image = load_grey(source)
    paper = level_at(image.histogram(), 0.10, from_bright=True)
    # A signature covers a tiny share of a page, so the ink is measured inside the box of
    # everything clearly darker than the paper (a faint frame or shadow stays out of it),
    # not across the whole page.
    strokes = image.point(lambda v: 255 if v < paper - 60 else 0).getbbox()
    if strokes is None:
        sys.exit(
            "prepare: no clear ink on paper; sign a blank page, "
            "or photograph the signature on white paper in even light"
        )
    ink = level_at(image.crop(strokes).histogram(), 0.005, from_bright=False)
    if paper - ink < 40:
        sys.exit(
            "prepare: no clear ink on paper; sign a blank page, "
            "or photograph the signature on white paper in even light"
        )
    low, high = ink + (paper - ink) * 0.15, ink + (paper - ink) * 0.5

    def alpha(value: int) -> int:
        if value <= low:
            return 255
        if value >= high:
            return 0
        return round(255 * (high - value) / (high - low))

    mask = image.point(alpha)
    box = mask.point(lambda a: 255 if a > 40 else 0).getbbox()
    if box is None:
        sys.exit("prepare: no strokes found")
    pad = 12
    mask = mask.crop(
        (
            max(box[0] - pad, 0),
            max(box[1] - pad, 0),
            min(box[2] + pad, mask.width),
            min(box[3] + pad, mask.height),
        )
    )
    signature = Image.new("RGBA", mask.size, INK + (0,))
    signature.putalpha(mask)
    out.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with os.fdopen(os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as file:
        signature.save(file, format="PNG")
    out.chmod(0o600)
    print(f"{out} ({signature.width}x{signature.height})")


def find_anchor(pdf: Path, anchor: str) -> tuple[int, tuple[float, float, float, float], float]:
    """The last page carrying the anchor, the anchor's box in PDF points, and the bottom
    of the nearest text above it (the ceiling a signature must stay under)."""
    document = pdfium.PdfDocument(pdf)
    for number in range(len(document) - 1, -1, -1):
        text = document[number].get_textpage()
        searcher = text.search(anchor, match_case=True)
        hits = []
        while (hit := searcher.get_next()) is not None:
            hits.append(hit)
        if not hits:
            continue
        if len(hits) > 1:
            sys.exit(
                f"sign: {anchor!r} appears {len(hits)} times on page {number + 1}; "
                "refusing to guess"
            )
        start, count = hits[0]
        boxes = [text.get_charbox(i) for i in range(start, start + count)]
        left = min(b[0] for b in boxes)
        bottom = min(b[1] for b in boxes)
        right = max(b[2] for b in boxes)
        top = max(b[3] for b in boxes)
        above = [
            box[1]
            for i in range(text.count_chars())
            if (box := text.get_charbox(i))[1] > top and box[2] > right
        ]
        ceiling = min(above, default=float(document[number].get_size()[1]))
        return number, (left, bottom, right, top), ceiling
    sys.exit(f"sign: {anchor!r} not found in {pdf}")


def sign(pdf: Path, signature: Path, out: Path, anchor: str, height: float) -> None:
    number, (_, bottom, right, _), ceiling = find_anchor(pdf, anchor)
    writer = PdfWriter(clone_from=pdf)
    page = writer.pages[number]
    with Image.open(signature) as image:
        ratio = image.width / image.height
    # Rest the strokes on the anchor's baseline, where the underline sits, dipping slightly
    # below it the way a pen does, and keep their top a point under the text above.
    x = right + 8
    height = min(height, (ceiling - 1 - bottom) / 0.82)
    width = min(height * ratio, float(page.mediabox.right) - 40 - x)
    if width < 40:
        sys.exit(f"sign: no room for a signature right of {anchor!r} on page {number + 1}")
    height = width / ratio
    y = bottom - height * 0.18
    overlay = io.BytesIO()
    sheet = canvas.Canvas(overlay, pagesize=(float(page.mediabox.right), float(page.mediabox.top)))
    sheet.drawImage(ImageReader(str(signature)), x, y, width=width, height=height, mask="auto")
    sheet.save()
    page.merge_page(PdfReader(overlay).pages[0])
    writer.write(out)
    print(f"{out}: signed on page {number + 1}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("prepare", help="signature PDF or photo to a transparent PNG")
    p.add_argument("source", type=Path)
    p.add_argument("out", type=Path)
    s = commands.add_parser("sign", help="stamp a signature PNG on a PDF's signature line")
    s.add_argument("pdf", type=Path)
    s.add_argument("--signature", type=Path, required=True)
    s.add_argument(
        "-o", "--out", type=Path, help="defaults to '<name> · firmato.pdf' beside the input"
    )
    s.add_argument("--anchor", default="Firma:")
    s.add_argument(
        "--height", type=float, default=32.0, help="signature height in points (default 32)"
    )
    args = parser.parse_args()
    inputs = [args.source] if args.command == "prepare" else [args.pdf, args.signature]
    for path in inputs:
        if not path.is_file():
            sys.exit(f"{args.command}: {path} is not a file")
    if args.command == "prepare":
        prepare(args.source, args.out)
    else:
        out = args.out or args.pdf.with_name(f"{args.pdf.stem} · firmato.pdf")
        sign(args.pdf, args.signature, out, args.anchor, args.height)


if __name__ == "__main__":
    main()
