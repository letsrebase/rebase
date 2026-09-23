"""Typeset the freelancer contracts in `content/contratti/` into PDFs.

    uv run python projects/hub/tools/build_contract_pdf.py
    uv run python projects/hub/tools/build_contract_pdf.py lettera-di-incarico --data job.json

With no argument it builds every Markdown file in `content/contratti/` as a blank form;
naming one or more (by file stem) builds only those. `--data` fills the fields from a
JSON object, read over `content/contratti/rebase.json` (rebase's company data and the
defaults every letter starts from) and over `rebase.local.json` beside it when that file
exists: git ignores it, and it holds the real data of whoever signs for rebase today,
which a public repository does not print; `--public` leaves it out, for a PDF that is
going somewhere public, and writes to `dist/public/`. The PDFs land in `content/contratti/dist/`,
which git ignores too: unlike the guide, nothing serves these files, so nothing here is
committed or locked. The text a member signs in the hub (REB-339) is the Markdown itself.

Two markers are the reason this is not a plain pandoc call, and both are replaced in the
Typst pandoc writes, after pandoc has escaped everything else:

- `{{key}}` is a field: a value from the data when it has one, otherwise a blank line
  labelled with the key, so the same file is both the template and the printable form.
- `[[text]]` is a proposal still to be decided, highlighted in the draft. A document
  whose front matter no longer says `status: draft` may not carry one.

The fee is the one number a letter must not get wrong, so the build refuses one that is
not a JSON number, is not above zero, or has more decimals than the page prints.

The toolchain, the palette and the typeface are the guide's, imported from
`build_guide_pdf.py` rather than copied: two documents from one brand must not drift
apart one hand-edited hex at a time.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
from decimal import Decimal, InvalidOperation
from pathlib import Path

from build_guide_pdf import REPO, Failed, palette, static_fonts, tool_version

HUB = Path(__file__).resolve().parent.parent
CONTRACTS = HUB / "content" / "contratti"
TEMPLATE = Path(__file__).resolve().parent / "contract.typ.template"
COMPANY = CONTRACTS / "rebase.json"
COMPANY_LOCAL = CONTRACTS / "rebase.local.json"
OUTPUT = CONTRACTS / "dist"

# A field key is lowercase words joined by single hyphens. Pandoc's Typst writer escapes
# neither braces nor hyphens, so the token reaches the intermediate file as written.
FIELD = re.compile(r"\{\{([a-z0-9]+(?:-[a-z0-9]+)*)\}\}")
# `[[` and `]]` do not survive pandoc as written: the Typst writer escapes every bracket.
PROPOSAL_OPEN = r"\[\["
PROPOSAL_CLOSE = r"\]\]"

FEE = "compenso"
CENT = Decimal("0.01")

Value = str | int | float | bool | None


def load_data(path: Path | None, local: bool = True) -> dict[str, Value]:
    """rebase's company data and defaults, then the local file, then the caller's.

    `null` means not known yet, and a later file's `null` blanks an earlier value."""
    data: dict[str, Value] = {}
    signer = COMPANY_LOCAL if local and COMPANY_LOCAL.is_file() else None
    for source in (COMPANY, signer, path):
        if source is None:
            continue
        try:
            loaded = json.loads(source.read_text(encoding="utf-8"), parse_constant=not_a_number)
        except (OSError, json.JSONDecodeError) as exc:
            raise Failed(f"cannot read {source}: {exc}") from exc
        if not isinstance(loaded, dict):
            raise Failed(f"{source} must hold one JSON object of field: value")
        for key, value in loaded.items():
            if not FIELD.fullmatch("{{" + key + "}}"):
                raise Failed(f"{source}: {key!r} is not a field name (lowercase-with-hyphens)")
            if not isinstance(value, (str, int, float, bool)) and value is not None:
                raise Failed(f"{source}: {key} must be text, a number or null")
            if isinstance(value, float) and not math.isfinite(value):
                raise Failed(f"{source}: {key} is {value}, not a number a contract can print")
            data[key] = value
    return data


def not_a_number(constant: str) -> float:
    """Python's JSON reader accepts `NaN` and `Infinity`, which JSON itself does not."""
    raise Failed(f"{constant} is not a number a contract can print")


def amount(data: dict[str, Value], key: str) -> Decimal | None:
    """A number the page prints to the cent, or None when the field is not filled in."""
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Failed(f"{key} must be a JSON number, not {value!r}")
    exact = Decimal(str(value))
    try:
        cents = exact.quantize(CENT)
    except InvalidOperation as exc:
        raise Failed(f"{key} is {value}, not a number a contract can print") from exc
    if exact != cents:
        raise Failed(f"{key} is {exact}: at most two decimals, which is what the page prints")
    return exact


def checked(data: dict[str, Value]) -> dict[str, Value]:
    """The data, once the fee is a number the page can print as it was given."""
    fee = amount(data, FEE)
    if fee is not None and fee <= 0:
        raise Failed(f"{FEE} is {fee}: a fee above zero")
    return data


def italian(number: Decimal, places: int) -> str:
    """`1234.5` as `1.234,50`: a dot between thousands, a comma before the decimals."""
    text = f"{number:,.{places}f}"
    return text.replace(",", "_").replace(".", ",").replace("_", ".")


def rendered(key: str, value: Value) -> str:
    """What the page prints for a value. Only the fee is reformatted: a letter number or
    a VAT number is an identifier, printed as it was given."""
    if isinstance(value, bool):
        return "sì" if value else "no"
    if isinstance(value, (int, float)):
        if key == FEE:
            return f"{italian(Decimal(str(value)), 2)} €"
        return str(value)
    # Pandoc's `smart` curls the apostrophes of the Markdown around this value; a value
    # typed with a straight one would sit beside them looking like a typo.
    return str(value).replace("'", "\u2019")


def typst_string(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return '"' + escaped.replace("\r", "").replace("\n", "\\n").replace("\t", " ") + '"'


def fill(typst: str, data: dict[str, Value]) -> tuple[str, list[str]]:
    """Replace every field token; return the Typst and the fields left blank.

    Each call ends in a semicolon, which closes the embedded expression: without it a
    field followed by `(` or by `.word` would be read as a call or a field access.
    """
    blank: list[str] = []

    def one(match: re.Match[str]) -> str:
        key = match.group(1)
        value = data.get(key)
        if value is None or value == "":
            if key not in blank:
                blank.append(key)
            return "#field(" + typst_string(key.replace("-", " ")) + ");"
        return "#value(" + typst_string(rendered(key, value)) + ");"

    return FIELD.sub(one, typst), blank


def mark_proposals(typst: str, name: str, draft: bool) -> str:
    opened, closed = typst.count(PROPOSAL_OPEN), typst.count(PROPOSAL_CLOSE)
    if opened != closed:
        raise Failed(f"{name}: {opened} `[[` against {closed} `]]`")
    if opened and not draft:
        raise Failed(f"{name} is no longer a draft and still carries {opened} proposals")
    return typst.replace(PROPOSAL_OPEN, "#proposal[").replace(PROPOSAL_CLOSE, "];")


def survived(markdown: str, typst: str, name: str) -> None:
    """Both markers rely on how pandoc's Typst writer escapes, which a pandoc release could
    change. A field it escaped would print as `{{key}}` and an unescaped proposal as plain
    text, both without an error, so count them on either side of pandoc instead."""
    for marker, before, after in (
        ("fields", len(FIELD.findall(markdown)), len(FIELD.findall(typst))),
        ("proposals", markdown.count("[["), typst.count(PROPOSAL_OPEN)),
    ):
        if after < before:
            raise Failed(
                f"{name}: {before} {marker} in the Markdown, {after} in pandoc's Typst."
                " pandoc escapes them differently now; adjust the markers in this script."
            )


def is_draft(markdown: str, name: str) -> bool:
    front = re.match(r"---\n(.*?)\n---\n", markdown, re.S)
    if front is None:
        raise Failed(f"{name} has no front matter (title, version, date, status)")
    status = re.search(r"^status:\s*(.+?)\s*$", front.group(1), re.M)
    if status is None:
        raise Failed(f"{name}: the front matter says no `status`")
    return status.group(1).strip("\"'") == "draft"


def build(
    source: Path, data: dict[str, Value], fonts: Path, workdir: Path
) -> tuple[bytes, list[str]]:
    markdown = source.read_text(encoding="utf-8")
    draft = is_draft(markdown, source.name)
    intermediate = workdir / f"{source.stem}.typ"
    pandoc = subprocess.run(
        [
            "pandoc",
            "--from=markdown+smart",
            "--to=typst",
            "--standalone",
            f"--template={TEMPLATE}",
            # The articles are `##`, under a title that comes from the front matter.
            "--shift-heading-level-by=-1",
            "--wrap=preserve",
            *(f"--variable={name}:{value.lstrip('#')}" for name, value in palette().items()),
            f"--variable=draft:{'true' if draft else 'false'}",
            "--output",
            str(intermediate),
            str(source),
        ],
        capture_output=True,
        text=True,
    )
    if pandoc.returncode != 0:
        raise Failed(f"pandoc failed on {source.name}:\n{pandoc.stderr.strip()}")

    written = intermediate.read_text(encoding="utf-8")
    survived(markdown, written, source.name)
    typst, blank = fill(written, data)
    intermediate.write_text(mark_proposals(typst, source.name, draft), encoding="utf-8")

    output = workdir / f"{source.stem}.pdf"
    compiled = subprocess.run(
        [
            "typst",
            "compile",
            "--root",
            str(workdir),
            "--font-path",
            str(fonts),
            "--ignore-system-fonts",
            "--creation-timestamp",
            "0",
            str(intermediate),
            str(output),
        ],
        capture_output=True,
        text=True,
    )
    if compiled.returncode != 0:
        raise Failed(f"typst failed on {source.name}:\n{compiled.stderr.strip()}")
    warnings = [line for line in compiled.stderr.splitlines() if "warning" in line]
    if warnings:
        raise Failed(f"typst warned on {source.name}:\n" + "\n".join(warnings))
    return output.read_bytes(), blank


def documents(names: list[str]) -> list[Path]:
    available = {path.stem: path for path in sorted(CONTRACTS.glob("*.md"))}
    if not names:
        return list(available.values())
    unknown = [name for name in names if name not in available]
    if unknown:
        raise Failed(f"no such document: {', '.join(unknown)} (have: {', '.join(available)})")
    return [available[name] for name in names]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("documents", nargs="*", help="stems in content/contratti; all if none")
    parser.add_argument("--data", type=Path, help="JSON object of field: value to fill in")
    parser.add_argument(
        "--public",
        action="store_true",
        help=f"leave out {COMPANY_LOCAL.name}, and write to dist/public unless --out says",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help=f"directory for the PDFs (default {OUTPUT.relative_to(REPO)})",
    )
    args = parser.parse_args()
    out: Path = args.out or (OUTPUT / "public" if args.public else OUTPUT)

    workdir = Path(tempfile.mkdtemp(prefix="rebase-contract-"))
    try:
        tool_version("pandoc", "--version")
        tool_version("typst", "--version")
        data = checked(load_data(args.data, local=not args.public))
        fonts = workdir / "fonts"
        static_fonts(fonts)
        out.mkdir(parents=True, exist_ok=True)
        for source in documents(args.documents):
            pdf, blank = build(source, data, fonts, workdir)
            suffix = f"-{args.data.stem}" if args.data else ""
            target = out / f"{source.stem}{suffix}.pdf"
            target.write_bytes(pdf)
            print(f"{target}: {len(pdf)} bytes")
            if args.data and blank:
                print(f"  left blank: {', '.join(blank)}")
        return 0
    except Failed as exc:
        print(f"build_contract_pdf: {exc}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
