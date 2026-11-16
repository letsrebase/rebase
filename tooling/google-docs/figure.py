"""Draw a figure for a document as a PNG, in Outfit and the palette's colours.

    python3 tooling/google-docs/figure.py flow.html flow.png --size 960x470

The HTML is a fragment: what goes inside `<body>`. This wraps it in a page that loads
Outfit from `shared/brand/fonts` and declares every colour of `palette.css` as a custom
property (`var(--color-watermelon)` and the rest), then has headless Chrome photograph
it at twice the size given, so the picture stays sharp at the width of a page. Draw
figures with HTML and CSS, or inline SVG, never with a palette hex value of your own.
The page is written to a temporary directory, so a relative `<img src>` in the fragment
does not resolve: use an absolute `file://` URI for a picture.
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from brand_palette import BRAND, palette

BROWSERS = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")


def page(fragment: str) -> str:
    colours = "\n".join(f"  --color-{name}: #{value};" for name, value in palette().items())
    font = (BRAND / "fonts" / "outfit-variable-latin.woff2").as_uri()
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><style>
@font-face {{ font-family: Outfit; src: url("{font}") format("woff2"); font-weight: 300 700; }}
:root {{
{colours}
}}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; background: white; }}
body {{ font-family: Outfit, sans-serif; color: var(--color-prussian-blue); }}
</style></head><body>
{fragment}
</body></html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path, help="HTML fragment")
    parser.add_argument("output", type=Path, help="PNG to write")
    parser.add_argument("--size", default="960x540", help="CSS pixels, WIDTHxHEIGHT")
    args = parser.parse_args()
    chrome = next((path for name in BROWSERS if (path := shutil.which(name))), None)
    if chrome is None:
        sys.exit(f"A browser is needed to draw a figure: none of {', '.join(BROWSERS)}")
    width, height = args.size.lower().split("x")
    output = args.output.resolve()
    output.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        html = Path(tmp) / "figure.html"
        html.write_text(page(args.source.read_text(encoding="utf-8")), encoding="utf-8")
        result = subprocess.run(
            [
                chrome,
                "--headless=new",
                # A profile of its own, so an open Chrome cannot take the job over.
                f"--user-data-dir={Path(tmp) / 'profile'}",
                "--no-sandbox",
                "--disable-gpu",
                "--hide-scrollbars",
                "--allow-file-access-from-files",
                "--force-device-scale-factor=2",
                f"--window-size={width},{height}",
                f"--screenshot={output}",
                html.as_uri(),
            ],
            capture_output=True,
            text=True,
        )
    if not output.exists():
        sys.exit(f"The browser wrote no picture (exit {result.returncode}):\n{result.stderr}")
    print(output)


if __name__ == "__main__":
    main()
