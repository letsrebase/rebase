"""Build the guide's PDF from its Markdown, or check that the committed one is current.

    uv run python projects/hub/tools/build_guide_pdf.py          # write it
    uv run python projects/hub/tools/build_guide_pdf.py --check  # verify it

The guide is a perk of the community, so it is not a public file: the hub's API serves
it to a member behind their own session (`GET /api/hub/me/guide`), which is why the PDF
lives inside `rebase_core` as package data rather than in the website's `dist`.
Lorenzo's call on ORB-70, 2026-09-10: «la guida deve essere scaricabile dall'hub una
volta registrati».

**Why the artefact is committed rather than built in the image.** Building it where it
is served would put pandoc, Typst and fontTools inside `Dockerfile.api`, a
`python:3.13-slim` image that today installs one dependency closure and nothing else,
for one 2,500-word document that changes when somebody edits a Markdown file. So the
PDF is generated here and committed; `guide-pdf.lock.json` records the SHA-256 of every
input and of the output, `packages/core/tests/test_guide_pdf.py` fails when a source
moved without a regeneration, and `--check` in preflight rebuilds it and compares the
bytes on a machine that has the three tools. ORB-70 sanctions this shape explicitly, and
it is the same trade the rest of the repository makes: verification moves tiers, it does
not disappear.

**The toolchain is PigroCRM's, not a second one.** pandoc for Markdown to Typst, Typst to
compile, `--creation-timestamp 0` so two runs of the same source produce the same bytes
(`pigrocrm/core/render/pdf.py` explains why that flag is not optional for a hashed
artefact). What is new here is the typeface: the brand ships Outfit as one variable
woff2, Typst reads neither woff2 nor a variable axis ("variable fonts are not currently
supported", and the file's default instance is Thin 100), so fontTools decompresses it
and pins the two weights the site's own stylesheet uses -- 300 for body text, 500 for
headings and strong. The static instances live in a temporary directory and are never
committed: the woff2 in `shared/brand/fonts` stays the single source of the typeface.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

HUB = Path(__file__).resolve().parent.parent
REPO = HUB.parent.parent
TOOLS = HUB / "tools"

CONTENT = HUB / "content" / "guide-first-steps-freelance.md"
TEMPLATE = TOOLS / "guide.typ.template"
PALETTE = REPO / "shared" / "brand" / "palette.css"
FONT = REPO / "shared" / "brand" / "fonts" / "outfit-variable-latin.woff2"
LOCK = TOOLS / "guide-pdf.lock.json"

# Package data of `rebase_core`, so the API can read it in a container that carries no
# static site: `Dockerfile.api` copies `projects/hub/packages` whole. The file name is
# what a member's browser saves, and it stays readable in a downloads folder a week
# later, which is the whole requirement on it.
OUTPUT = (
    HUB
    / "packages"
    / "core"
    / "src"
    / "rebase_core"
    / "perks"
    / ("rebase-guide-first-steps-freelance.pdf")
)

# The site is the only place a link in this file can point: a PDF has no origin, so a
# root-relative href that works in the page resolves to nothing in a reader.
SITE = "https://letsrebase.com"
# Under 2 MB is ORB-70's budget. The measured file is an order of magnitude below it;
# this catches a font that stopped being subset, not a paragraph.
SIZE_BUDGET = 2 * 1024 * 1024

# Weight 300 is `body`'s in landing.css, 500 is what `h1`, `h2`, `h3` and `.kicker`
# share. Nothing on the site uses another one, so nothing here instances another one.
WEIGHTS = {300: "Light", 500: "Medium"}

# The five values the template needs, by the token that holds each one. Read out of the
# shared palette rather than typed here, for the reason palette-plugin.ts exists.
TOKENS = {
    "ink": "--color-prussian-blue",
    "inkquiet": "--color-charcoal-blue",
    "paper": "--color-paper",
    "cta": "--color-watermelon-strong",
    "gold": "--color-royal-gold",
}
# `--system-grid` is the ink at 7% over the ground (system.css). A PDF has no
# compositing context to resolve that in, so it is mixed here, once.
GRID_INK_SHARE = 0.07


class Failed(RuntimeError):
    """Something the caller has to fix, reported without a traceback."""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tool_version(binary: str, *args: str) -> str:
    try:
        out = subprocess.run([binary, *args], capture_output=True, text=True, check=True).stdout
    except FileNotFoundError as exc:
        raise Failed(
            f"{binary} is not on PATH. This script needs pandoc and typst, the same two"
            " PigroCRM's document renderer uses."
        ) from exc
    match = re.search(r"\d+\.\d+(\.\d+)?", out)
    if match is None:
        raise Failed(f"cannot read a version out of `{binary} {' '.join(args)}`")
    return match.group(0)


def palette() -> dict[str, str]:
    """The template's colours, by token, as `rrggbb` without the hash."""
    css = PALETTE.read_text(encoding="utf-8")
    found: dict[str, str] = {}
    for name, token in TOKENS.items():
        match = re.search(rf"^\s*{re.escape(token)}:\s*(#[0-9a-fA-F]{{6}});", css, re.M)
        if match is None:
            raise Failed(f"{token} is gone from {PALETTE.relative_to(REPO)}")
        found[name] = match.group(1)
    ink = found["ink"]
    ground = found["paper"]
    found["gridline"] = mix(ink, ground, GRID_INK_SHARE)
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
    """Outfit's variable woff2, as one static TTF per weight the site uses."""
    try:
        # fontTools ships no py.typed marker and has no stubs package, so these two
        # names are Any under `strict`. Narrower than a mypy override for the whole
        # module: nothing else in the monorepo imports it.
        from fontTools.ttLib import TTFont  # type: ignore[import-untyped]
        from fontTools.varLib.instancer import (  # type: ignore[import-untyped]
            instantiateVariableFont,
        )
    except ImportError as exc:  # pragma: no cover - the dev group installs it
        raise Failed("fontTools is missing: run `uv sync`") from exc

    into.mkdir(parents=True, exist_ok=True)
    for weight, name in WEIGHTS.items():
        # recalcTimestamp=False: on save fontTools stamps head.modified with the wall
        # clock otherwise, which changes the instance's bytes on every run, which
        # changes the subset tag Typst derives from them, which changes the PDF. The
        # lock file's whole promise rests on this one keyword.
        font = TTFont(FONT, recalcTimestamp=False)
        instantiateVariableFont(font, {"wght": weight}, inplace=True, updateFontNames=True)
        # `updateFontNames` names an instance after the axis' *default* value, which for
        # this file is Thin 100, so the pinned weights would land in the PDF as
        # "OutfitThin-Medium" and "OutfitThin-Light". Reader-visible metadata, wrong on
        # its face, and one line to fix.
        names = ((1, "Outfit"), (2, name), (4, f"Outfit {name}"), (6, f"Outfit-{name}"))
        for name_id, value in names:
            font["name"].setName(value, name_id, 3, 1, 0x409)
            font["name"].setName(value, name_id, 1, 0, 0)
        # Typst reads a bare TTF, never a woff2 container.
        font.flavor = None
        font.save(into / f"Outfit-{name}.ttf")


def split_front_matter(markdown: str) -> tuple[str, str, str]:
    """The title, the paragraph that goes on the cover, and everything else.

    The `#` title and the paragraph under it are the cover, so the body starts at the
    second paragraph. Nothing is dropped: the rest of the introduction still opens the
    first text page, above the first `##`.
    """
    title = re.match(r"#\s+(.+)\n", markdown)
    if title is None:
        raise Failed(f"{CONTENT.name} does not start with a `# ` title")
    rest = markdown[title.end() :].lstrip("\n")
    paragraphs = rest.split("\n\n", 1)
    if len(paragraphs) != 2 or "\n## " not in rest:
        raise Failed(f"{CONTENT.name} needs a standfirst paragraph and at least one `## ` section")
    return title.group(1).strip(), paragraphs[0].strip(), paragraphs[1].lstrip("\n")


def absolute_links(markdown: str) -> str:
    """Root-relative Markdown links, made absolute against the site."""
    return re.sub(r"\]\((/[^)]*)\)", lambda m: f"]({SITE}{m.group(1)})", markdown)


def build(workdir: Path) -> bytes:
    markdown = CONTENT.read_text(encoding="utf-8")
    title, standfirst, body = split_front_matter(markdown)
    source = workdir / "guide.md"
    source.write_text(absolute_links(body), encoding="utf-8")
    static_fonts(workdir / "fonts")
    colours = palette()

    intermediate = workdir / "guide.typ"
    variables = [
        *(f"--variable={name}:{value.lstrip('#')}" for name, value in colours.items()),
        f"--variable=title:{title}",
        f"--variable=standfirst:{standfirst}",
        f"--variable=shorttitle:{title.split(':')[0]}",
        f"--variable=siteurl:{SITE}",
        # Labelled rather than bare: Typst turns a bare URL in markup into a link of
        # its own accord, and the label a reader wants on a cover is the domain.
        f"--variable=sitelabel:{SITE.removeprefix('https://')}",
    ]
    pandoc = subprocess.run(
        [
            "pandoc",
            # `smart` is what turns the source's straight apostrophes into typographic
            # ones; the guillemets it already carries pass through either way.
            "--from=markdown+smart",
            "--to=typst",
            "--standalone",
            f"--template={TEMPLATE}",
            # The sections are `##` in a document whose `#` is the cover's title, so they
            # are the top level of what is left. The template styles that level.
            "--shift-heading-level-by=-1",
            *variables,
            "--output",
            str(intermediate),
            str(source),
        ],
        capture_output=True,
        text=True,
    )
    if pandoc.returncode != 0:
        raise Failed(f"pandoc failed:\n{pandoc.stderr.strip()}")

    output = workdir / "guide.pdf"
    typst = subprocess.run(
        [
            "typst",
            "compile",
            "--root",
            str(workdir),
            "--font-path",
            str(workdir / "fonts"),
            # No system fonts: whether this machine happens to have Outfit installed
            # must not change the bytes.
            "--ignore-system-fonts",
            # Reproducibility. Without it Typst stamps the wall clock into the PDF and
            # every build produces a different hash for identical input.
            "--creation-timestamp",
            "0",
            str(intermediate),
            str(output),
        ],
        capture_output=True,
        text=True,
    )
    if typst.returncode != 0:
        raise Failed(f"typst failed:\n{typst.stderr.strip()}")
    warnings = [line for line in typst.stderr.splitlines() if "warning" in line]
    if warnings:
        raise Failed("typst warned, which for this document is a defect:\n" + "\n".join(warnings))

    pdf = output.read_bytes()
    if len(pdf) > SIZE_BUDGET:
        raise Failed(f"the PDF is {len(pdf)} bytes, over the {SIZE_BUDGET} budget")
    return pdf


def lock_contents(pdf: bytes) -> dict[str, Any]:
    return {
        "output": str(OUTPUT.relative_to(REPO)),
        "bytes": len(pdf),
        # What the member area says beside the download, so nobody clicks blind. Read
        # off the file rather than typed, and the web's own test reads it from here.
        "pages": len(re.findall(rb"/Type\s*/Page[^s]", pdf)),
        "sha256": hashlib.sha256(pdf).hexdigest(),
        "sources": {
            str(path.relative_to(REPO)): sha256(path)
            for path in (CONTENT, TEMPLATE, PALETTE, FONT, Path(__file__).resolve())
        },
        "tools": {
            "pandoc": tool_version("pandoc", "--version"),
            "typst": tool_version("typst", "--version"),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="rebuild in a temporary directory and compare with the committed file",
    )
    args = parser.parse_args()

    workdir = Path(tempfile.mkdtemp(prefix="rebase-guide-"))
    try:
        pdf = build(workdir)
        lock = lock_contents(pdf)
        if not args.check:
            OUTPUT.parent.mkdir(parents=True, exist_ok=True)
            OUTPUT.write_bytes(pdf)
            LOCK.write_text(json.dumps(lock, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print(f"{OUTPUT.relative_to(REPO)}: {len(pdf)} bytes, {lock['sha256'][:12]}")
            return 0

        if not OUTPUT.is_file():
            raise Failed(f"{OUTPUT.relative_to(REPO)} is not committed")
        committed = OUTPUT.read_bytes()
        if committed != pdf:
            raise Failed(
                f"{OUTPUT.relative_to(REPO)} is stale: the sources build a different file"
                f" ({len(pdf)} bytes, {hashlib.sha256(pdf).hexdigest()[:12]}) than the one"
                f" committed ({len(committed)} bytes,"
                f" {hashlib.sha256(committed).hexdigest()[:12]})."
                " Run the same command without --check and commit the result."
            )
        recorded = json.loads(LOCK.read_text(encoding="utf-8"))
        if recorded.get("sources") != lock["sources"] or recorded.get("sha256") != lock["sha256"]:
            raise Failed(
                f"{LOCK.relative_to(REPO)} does not describe these sources. Run the same"
                " command without --check and commit the result."
            )
        print(f"{OUTPUT.relative_to(REPO)}: current, {len(pdf)} bytes")
        return 0
    except Failed as exc:
        print(f"build_guide_pdf: {exc}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
