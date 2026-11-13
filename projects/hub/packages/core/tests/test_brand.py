"""The brand the hub typesets its PDFs in (`rebase_core.contracts.brand`), read by the anonymous
CV's print: the same palette and typeface as the guide's, and the echo the image carries.

The contracts' own text and renderer moved to PigroCRM with the engagements (REB-1108); the brand
reading stayed, so this is what is left of the old `test_contract_pdf.py`."""

import os
import struct
import subprocess
import sys
from pathlib import Path

import build_guide_pdf

from rebase_core.contracts import brand


def test_the_brand_is_read_exactly_as_the_guide_does() -> None:
    """`brand` is a copy of the guide script's reading, because that script's bytes are
    locked and a package cannot import a script: this keeps the two documents of one
    brand from drifting apart one hand-edited hex at a time."""
    assert brand.TOKENS == build_guide_pdf.TOKENS
    assert brand.WEIGHTS == build_guide_pdf.WEIGHTS
    assert brand.GRID_INK_SHARE == build_guide_pdf.GRID_INK_SHARE
    assert brand.PALETTE == build_guide_pdf.PALETTE
    assert brand.FONT == build_guide_pdf.FONT
    assert brand.palette() == build_guide_pdf.palette()


def test_the_echo_is_read_from_the_checkout_or_from_the_directory_nix_names(
    tmp_path: Path,
) -> None:
    """The echo is `shared/brand`'s own file in a checkout and in the image, which mirrors
    it; the Nix `hub-api` package names another brand directory with
    `REBASE_CONTRACTS_BRAND_DIR` (REB-403). `brand` reads the variable once, when it is
    imported, so a fresh interpreter is what shows the second case."""
    name = Path("echo") / "echo-ink-watermelon-outlines-260.png"
    assert brand.REPO / "shared" / "brand" / name == brand.ECHO
    assert brand.ECHO.is_file()
    printed = subprocess.run(
        [sys.executable, "-c", "from rebase_core.contracts import brand; print(brand.ECHO)"],
        env={**os.environ, "REBASE_CONTRACTS_BRAND_DIR": str(tmp_path)},
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert Path(printed) == tmp_path / name


def _png_size(path: Path) -> tuple[int, int]:
    """A PNG's width and height in pixels, from its header chunk."""
    width, height = struct.unpack(">II", path.read_bytes()[16:24])
    return width, height


def test_the_echo_a_pdf_prints_is_the_brands_own_drawing_at_document_size() -> None:
    """Typst embeds a picture's own pixels, so the PDFs take the copy
    `shared/brand/tools/build-echo.mjs` draws 260 pixels tall, 600 per inch at the 11 mm
    the title block prints it at, rather than the 2572x1222 file (REB-479). It is the
    same drawing: the full file's proportions, to within a pixel."""
    full = brand.REPO / "shared" / "brand" / "echo" / "echo-ink-watermelon-outlines.png"
    width, height = _png_size(brand.ECHO)
    full_width, full_height = _png_size(full)
    assert height == 260
    assert abs(width - full_width * height / full_height) < 1
