"""The brand a contract is typeset in: five values of the palette and Outfit at the two
weights the site uses.

The same reading `tools/build_guide_pdf.py` does, written again here rather than
imported: a package cannot import a script, and that script's own bytes are part of the
guide's lock (`test_guide_pdf.py`), so it is not the one to move. `test_contract_pdf.py`
compares the two readings, so two documents of one brand cannot drift apart. The files
are read at the repository's own paths, which the API image mirrors (`Dockerfile.api`
copies both), so the woff2 in `shared/brand/fonts` stays the single source of the
typeface.
"""

import re
import tempfile
import threading
from pathlib import Path

from rebase_core.contracts.fields import ContractFailed

# `.../projects/hub/packages/core/src/rebase_core/contracts/brand.py`: seven levels up is
# the root of the checkout, or `/app` in the image, which mirrors the repository.
REPO = Path(__file__).resolve().parents[7]
PALETTE = REPO / "shared" / "brand" / "palette.css"
FONT = REPO / "shared" / "brand" / "fonts" / "outfit-variable-latin.woff2"

# Weight 300 is `body`'s in landing.css, 500 is what `h1`, `h2`, `h3` and `.kicker` share.
WEIGHTS = {300: "Light", 500: "Medium"}
# The five values the template needs, by the token that holds each one.
TOKENS = {
    "ink": "--color-prussian-blue",
    "inkquiet": "--color-charcoal-blue",
    "paper": "--color-paper",
    "cta": "--color-watermelon-strong",
    "gold": "--color-royal-gold",
}
# `--system-grid` is the ink at 7% over the ground (system.css), mixed here once.
GRID_INK_SHARE = 0.07


def palette() -> dict[str, str]:
    """The template's colours, by name, as `#rrggbb`: the five tokens, and the grid line
    mixed from two of them, exactly as `build_guide_pdf.palette` returns them."""
    try:
        css = PALETTE.read_text(encoding="utf-8")
    except OSError as exc:
        raise ContractFailed(f"cannot read {PALETTE}: {exc}") from exc
    found: dict[str, str] = {}
    for name, token in TOKENS.items():
        match = re.search(rf"^\s*{re.escape(token)}:\s*(#[0-9a-fA-F]{{6}});", css, re.M)
        if match is None:
            raise ContractFailed(f"{token} is gone from {PALETTE}")
        found[name] = match.group(1)
    found["gridline"] = mix(found["ink"], found["paper"], GRID_INK_SHARE)
    return found


def mix(top: str, bottom: str, share: float) -> str:
    """`top` at `share` over an opaque `bottom`, as a hex string."""

    def channels(value: str) -> tuple[int, int, int]:
        return (int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16))

    blended = (
        round(a * share + b * (1 - share))
        for a, b in zip(channels(top), channels(bottom), strict=True)
    )
    return "#" + "".join(f"{c:02x}" for c in blended)


def static_fonts(into: Path) -> None:
    """Outfit's variable woff2, as one static TTF per weight the site uses: Typst reads
    neither woff2 nor a variable axis."""
    try:
        # fontTools ships no py.typed marker and has no stubs package, so these two
        # names are Any under `strict`.
        from fontTools.ttLib import TTFont  # type: ignore[import-untyped]
        from fontTools.varLib.instancer import (  # type: ignore[import-untyped]
            instantiateVariableFont,
        )
    except ImportError as exc:  # pragma: no cover - a dependency of rebase_core
        raise ContractFailed("fontTools is missing: run `uv sync`") from exc
    if not FONT.is_file():
        raise ContractFailed(f"{FONT} is missing: the API image copies it from shared/brand/fonts")
    into.mkdir(parents=True, exist_ok=True)
    for weight, name in WEIGHTS.items():
        # recalcTimestamp=False: the same instance bytes on every run, as the guide keeps them.
        font = TTFont(FONT, recalcTimestamp=False)
        instantiateVariableFont(font, {"wght": weight}, inplace=True, updateFontNames=True)
        # `updateFontNames` names an instance after the axis' default (Thin 100).
        names = ((1, "Outfit"), (2, name), (4, f"Outfit {name}"), (6, f"Outfit-{name}"))
        for name_id, value in names:
            font["name"].setName(value, name_id, 3, 1, 0x409)
            font["name"].setName(value, name_id, 1, 0, 0)
        font.flavor = None
        font.save(into / f"Outfit-{name}.ttf")


_fonts: Path | None = None
_fonts_lock = threading.Lock()


def fonts_dir() -> Path:
    """The static instances, built once per process into a directory of their own and
    reused by every render after the first: instancing the variable font is the slowest
    step of a render and its output cannot change while the process lives. Built again
    if something removed the directory, as a temp cleaner on a long-lived host would."""
    global _fonts
    with _fonts_lock:
        present = _fonts is not None and all(
            (_fonts / f"Outfit-{name}.ttf").is_file() for name in WEIGHTS.values()
        )
        if not present:
            directory = Path(tempfile.mkdtemp(prefix="rebase-contract-fonts-"))
            static_fonts(directory)
            _fonts = directory
        assert _fonts is not None
        return _fonts
