"""pandoc and Typst over a contract's Markdown: the PDF, and where its signing blanks are.

The toolchain is PigroCRM's and the guide's: pandoc for Markdown to Typst, Typst to
compile, `--creation-timestamp 0` so the same data give the same bytes. The texts, the
template and rebase's own defaults are this package's data, so the API image needs
nothing from `content/` or `tools/`. Every call works in a directory of its own, removed
when it returns; the static fonts are the one thing shared (`brand.fonts_dir`).

`signature_blanks` compiles the same source and asks `typst query` for every
`<signature-blank>` the template left behind: the page and the box, in points from the
page's top-left corner, of each blank the signing site fills (the freelancer's
signatures and the date of signing). Phase 3 turns them into the signing site's own
percentages; phase 1's note says how the reported point relates to the drawn rule.
"""

import json
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from rebase_core.contracts.brand import fonts_dir, palette
from rebase_core.contracts.fields import (
    ContractFailed,
    Value,
    checked,
    fill,
    front_matter,
    is_draft,
    mark_proposals,
    read_layer,
    survived,
)

PACKAGE = Path(__file__).resolve().parent
TEXTS = PACKAGE / "texts"
TEMPLATE = PACKAGE / "contract.typ.template"
COMPANY_DEFAULTS = TEXTS / "rebase.json"
DOCUMENTS = ("contratto-quadro", "lettera-di-incarico")
SIGNATURE_LABEL = "signature-blank"
TOOL_TIMEOUT_SECONDS = 60
# The page the template sets (`paper: "a4"`), in points.
A4_WIDTH_PT = 595.2756
A4_HEIGHT_PT = 841.8898


@dataclass(frozen=True)
class Rendered:
    """A typeset contract: the bytes, the fields it printed as blank lines, the text's
    `version` and whether the text is still a draft (the BOZZA watermark is on)."""

    pdf: bytes
    blank: list[str]
    version: str
    draft: bool


@dataclass(frozen=True)
class SignatureBlank:
    """One blank the signing site fills, as Typst placed it: `name` is the field's label
    (`firma professionista`, `data firma`), `page` counts from 1, the rest are points."""

    name: str
    page: int
    x: float
    y: float
    width: float
    height: float


class Renderer(Protocol):
    """The seam the services take, so a test hands `FakeRenderer` and needs no binary."""

    def render(self, document: str, data: Mapping[str, Value]) -> Rendered: ...


class ContractRenderer:
    """The production renderer: pandoc and Typst on this machine."""

    def render(self, document: str, data: Mapping[str, Value]) -> Rendered:
        return render(document, data)


def text_path(document: str) -> Path:
    if document not in DOCUMENTS:
        raise ContractFailed(f"no such document: {document} (have: {', '.join(DOCUMENTS)})")
    return TEXTS / f"{document}.md"


def text_version(document: str) -> str:
    """The `version` of the text as it is in the package today."""
    source = text_path(document)
    version = front_matter(source.read_text(encoding="utf-8"), source.name).get("version")
    if not version:
        raise ContractFailed(f"{source.name}: the front matter says no `version`")
    return version


def company_defaults() -> dict[str, Value]:
    """rebase's company data and the defaults every letter starts from (`rebase.json`)."""
    return read_layer(COMPANY_DEFAULTS.read_text(encoding="utf-8"), COMPANY_DEFAULTS.name)


def _run(args: list[str], what: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=TOOL_TIMEOUT_SECONDS)
    except FileNotFoundError as exc:
        raise ContractFailed(f"{args[0]} is not on PATH: contracts need pandoc and typst") from exc
    except subprocess.TimeoutExpired as exc:
        raise ContractFailed(f"{what} took longer than {TOOL_TIMEOUT_SECONDS} seconds") from exc


def _typst_world(workdir: Path) -> list[str]:
    """What both `typst compile` and `typst query` need to see the same document."""
    return ["--root", str(workdir), "--font-path", str(fonts_dir()), "--ignore-system-fonts"]


def _typst_source(
    document: str, data: Mapping[str, Value], workdir: Path
) -> tuple[Path, list[str], str, bool]:
    """pandoc's Typst with every field filled and every proposal marked: the path, the
    fields left blank, the text's version and whether it is a draft."""
    source = text_path(document)
    markdown = source.read_text(encoding="utf-8")
    version = front_matter(markdown, source.name).get("version")
    if not version:
        raise ContractFailed(f"{source.name}: the front matter says no `version`")
    draft = is_draft(markdown, source.name)
    intermediate = workdir / f"{document}.typ"
    pandoc = _run(
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
        f"pandoc on {source.name}",
    )
    if pandoc.returncode != 0:
        raise ContractFailed(f"pandoc failed on {source.name}:\n{pandoc.stderr.strip()}")
    written = intermediate.read_text(encoding="utf-8")
    survived(markdown, written, source.name)
    typst, blank = fill(written, checked(dict(data)))
    intermediate.write_text(mark_proposals(typst, source.name, draft), encoding="utf-8")
    return intermediate, blank, version, draft


def render(document: str, data: Mapping[str, Value]) -> Rendered:
    workdir = Path(tempfile.mkdtemp(prefix="rebase-contract-"))
    try:
        intermediate, blank, version, draft = _typst_source(document, data, workdir)
        output = workdir / f"{document}.pdf"
        compiled = _run(
            [
                "typst",
                "compile",
                *_typst_world(workdir),
                "--creation-timestamp",
                "0",
                str(intermediate),
                str(output),
            ],
            f"typst on {document}",
        )
        if compiled.returncode != 0:
            raise ContractFailed(f"typst failed on {document}:\n{compiled.stderr.strip()}")
        warnings = [line for line in compiled.stderr.splitlines() if "warning" in line]
        if warnings:
            raise ContractFailed(f"typst warned on {document}:\n" + "\n".join(warnings))
        return Rendered(pdf=output.read_bytes(), blank=blank, version=version, draft=draft)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def signature_blanks(document: str, data: Mapping[str, Value]) -> list[SignatureBlank]:
    workdir = Path(tempfile.mkdtemp(prefix="rebase-contract-"))
    try:
        intermediate, _blank, _version, _draft = _typst_source(document, data, workdir)
        queried = _run(
            [
                "typst",
                "query",
                *_typst_world(workdir),
                "--field",
                "value",
                "--format",
                "json",
                str(intermediate),
                f"<{SIGNATURE_LABEL}>",
            ],
            f"typst query on {document}",
        )
        if queried.returncode != 0:
            raise ContractFailed(f"typst query failed on {document}:\n{queried.stderr.strip()}")
        try:
            values = json.loads(queried.stdout)
            return [
                SignatureBlank(
                    name=str(value["name"]),
                    page=int(value["page"]),
                    x=float(value["x"]),
                    y=float(value["y"]),
                    width=float(value["width"]),
                    height=float(value["height"]),
                )
                for value in values
            ]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ContractFailed(f"typst query on {document} answered {queried.stdout!r}") from exc
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
