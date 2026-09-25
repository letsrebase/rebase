"""pandoc and Typst over a contract's Markdown: the PDF, and where its signing blanks are.

The toolchain is PigroCRM's and the guide's: pandoc for Markdown to Typst, Typst to
compile, `--creation-timestamp 0` so the same data give the same bytes. The texts, the
template and rebase's own defaults are this package's data, so the API image needs
nothing from `content/` or `tools/`. Every call works in a directory of its own, removed
when it returns; the static fonts and the scaled echo are the two things shared
(`brand.built_once`), and the echo is copied into each call's directory, since Typst
reads a picture only from inside its `--root`.

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

from rebase_core.contracts.brand import ECHO, built_once, fonts_dir, palette
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
# The echo in the title block is 11 mm tall (the template's `image`). Typst embeds a
# picture's own pixels whatever size it prints it at, and the brand's 2572x1222 would add
# about 250 KB to every contract; 260 pixels tall is 600 per inch at 11 mm, about 60 KB.
ECHO_HEIGHT_PX = 260


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
    """The seam the services take, so a test hands `FakeRenderer` and needs no binary.

    `signing` asks for the copy that goes out for signature (REB-387 phase 3): the same
    page, with the labels under the blanks the signing site fills laid out and not drawn,
    since they show through the signature and the date (probe § 11.9). `is_draft` says
    whether the text in the package today is `status: draft`, which never leaves."""

    def render(
        self, document: str, data: Mapping[str, Value], *, signing: bool = False
    ) -> Rendered: ...

    def signature_blanks(
        self, document: str, data: Mapping[str, Value], *, signing: bool = False
    ) -> list[SignatureBlank]: ...

    def is_draft(self, document: str) -> bool: ...


class ContractRenderer:
    """The production renderer: pandoc and Typst on this machine."""

    def render(
        self, document: str, data: Mapping[str, Value], *, signing: bool = False
    ) -> Rendered:
        return render(document, data, signing=signing)

    def signature_blanks(
        self, document: str, data: Mapping[str, Value], *, signing: bool = False
    ) -> list[SignatureBlank]:
        return signature_blanks(document, data, signing=signing)

    def is_draft(self, document: str) -> bool:
        return text_is_draft(document)


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


def text_is_draft(document: str) -> bool:
    """Whether the text in the package today says `status: draft` (spec § 1f)."""
    source = text_path(document)
    return is_draft(source.read_text(encoding="utf-8"), source.name)


def company_defaults() -> dict[str, Value]:
    """rebase's company data and the defaults every letter starts from (`rebase.json`)."""
    return read_layer(COMPANY_DEFAULTS.read_text(encoding="utf-8"), COMPANY_DEFAULTS.name)


def _run(args: list[str], what: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args, input=stdin, capture_output=True, text=True, timeout=TOOL_TIMEOUT_SECONDS
        )
    except FileNotFoundError as exc:
        raise ContractFailed(f"{args[0]} is not on PATH: contracts need pandoc and typst") from exc
    except subprocess.TimeoutExpired as exc:
        raise ContractFailed(f"{what} took longer than {TOOL_TIMEOUT_SECONDS} seconds") from exc


def scaled_echo(into: Path) -> None:
    """The brand's echo at `ECHO_HEIGHT_PX` pixels tall, drawn by Typst's own PNG export
    on a transparent page the picture's size: the one scaler every machine that renders
    a contract already has, and the same pixels on every run."""
    if not ECHO.is_file():
        raise ContractFailed(f"{ECHO} is missing: the API image copies it from shared/brand/echo")
    page = (
        "#set page(width: auto, height: auto, margin: 0pt, fill: none)\n"
        f'#image("{ECHO.name}", height: {ECHO_HEIGHT_PX}pt)\n'
    )
    scaled = _run(
        [
            "typst",
            "compile",
            "--root",
            str(ECHO.parent),
            "--ignore-system-fonts",
            "--format",
            "png",
            # At 72 pixels per inch a point is a pixel.
            "--ppi",
            "72",
            "-",
            str(into / ECHO.name),
        ],
        f"typst scaling {ECHO.name}",
        stdin=page,
    )
    if scaled.returncode != 0:
        raise ContractFailed(f"typst failed scaling {ECHO.name}:\n{scaled.stderr.strip()}")


def echo() -> Path:
    """The scaled echo, made once per process."""
    return built_once("echo", (ECHO.name,), scaled_echo) / ECHO.name


def _typst_world(workdir: Path) -> list[str]:
    """What both `typst compile` and `typst query` need to see the same document."""
    return ["--root", str(workdir), "--font-path", str(fonts_dir()), "--ignore-system-fonts"]


def _typst_source(
    document: str, data: Mapping[str, Value], workdir: Path, signing: bool = False
) -> tuple[Path, list[str], str, bool]:
    """pandoc's Typst with every field filled and every proposal marked, and the echo
    beside it: the path, the fields left blank, the text's version and whether it is a
    draft."""
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
            f"--variable=forsigning:{'true' if signing else 'false'}",
            f"--variable=echo:{ECHO.name}",
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
    shutil.copyfile(echo(), workdir / ECHO.name)
    return intermediate, blank, version, draft


def render(document: str, data: Mapping[str, Value], signing: bool = False) -> Rendered:
    workdir = Path(tempfile.mkdtemp(prefix="rebase-contract-"))
    try:
        intermediate, blank, version, draft = _typst_source(document, data, workdir, signing)
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


def signature_blanks(
    document: str, data: Mapping[str, Value], signing: bool = False
) -> list[SignatureBlank]:
    workdir = Path(tempfile.mkdtemp(prefix="rebase-contract-"))
    try:
        intermediate, _blank, _version, _draft = _typst_source(document, data, workdir, signing)
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
