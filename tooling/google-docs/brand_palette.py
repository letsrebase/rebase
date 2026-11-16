"""The colours of `shared/brand/palette.css`, read once for the three scripts here.

Each value is the six hex digits of a `--color-*` token, upper case, keyed by the token
name without `--color-` (`prussian-blue`, `watermelon`, ...). Nothing in this directory
writes a palette colour of its own.
"""

import re
from pathlib import Path

BRAND = Path(__file__).resolve().parent.parent.parent / "shared" / "brand"
_TOKEN = re.compile(r"--color-([a-z-]+):\s*#([0-9a-fA-F]{6});")


def palette() -> dict[str, str]:
    found = _TOKEN.findall((BRAND / "palette.css").read_text(encoding="utf-8"))
    if not found:
        raise SystemExit("No colour found in shared/brand/palette.css")
    return {name: value.upper() for name, value in found}
