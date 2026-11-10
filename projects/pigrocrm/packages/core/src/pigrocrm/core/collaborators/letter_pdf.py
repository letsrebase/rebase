"""A letter's text, compiled and typeset: the engine's Markdown, pandoc, the markers, Typst.

The texts print a field the data leave empty as a blank line labelled with its name, so the same
file is both the template and the printable form (the hub's rule, moved unchanged). The engine
fails on a path it cannot resolve, so `fill_blanks` first gives every field the text reads that
the scope leaves empty a sentinel made of letters and digits, which pandoc passes through
untouched; after pandoc each sentinel becomes `#field("label");`. A proposal still to be decided,
`[[text]]`, becomes a highlighted `#proposal[...]` and is refused once the text is final.

`signature_blanks` compiles the same source and asks `typst query` for every `<signature-blank>`
the template left behind: the page and the box, in points from the page's top-left corner, of each
blank the signing site fills (the freelancer's signature and the date of signing). It moved here
with the renderer (collaborators design § 1, «Signature blanks»); sending for signature is a later
card.
"""

import json
import shutil
import subprocess
import tempfile
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pigrocrm.core.collaborators.letters import (
    blank_label,
    check_front_matter,
    split_front_matter,
)
from pigrocrm.core.config import Settings
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.render.diagnostics import translate_typst_failure
from pigrocrm.core.render.pdf import (
    ASSETS_DIR,
    MAIN_FONT,
    MAX_DIAGNOSTIC_BYTES,
    MAX_INTERMEDIATE_BYTES,
    MAX_PDF_BYTES,
    PDF_CREATION_TIMESTAMP,
    RENDER_SLOTS,
    RENDER_TIMEOUT_SECONDS,
    defeat_typst_autotypography,
    read_bounded,
    run_tool,
    tool_environment,
)
from pigrocrm.core.templates.ast import EachNode, IfNode, Node, VariableNode
from pigrocrm.core.templates.escaping import escape_for
from pigrocrm.core.templates.parser import parse_template
from pigrocrm.core.templates.renderer import render_template

ENTITY = "letter"
LETTER_TEMPLATE = ASSETS_DIR / "letter.typ.template"
SIGNATURE_LABEL = "signature-blank"
# `pandoc` reads the front matter, the pipe tables of the signatures and the smart quotes of the
# text; raw attributes are not read here: nothing in a letter is typeset by hand.
PANDOC_FROM = "markdown+smart+pipe_tables"
SENTINEL = "PGBLANK{number:04d}X"
# A proposal still to be decided is `[[text]]` in the template's own prose. The markers are turned
# into tokens of letters before the engine fills in any value, so a value that happens to contain
# `[[` is escaped text and never one (the tokens survive pandoc like the blank sentinels do).
_PROPOSAL_OPEN = "PGPROPOSALOPENX"
_PROPOSAL_CLOSE = "PGPROPOSALCLOSEX"


@dataclass(frozen=True)
class RenderedLetter:
    """A typeset letter: the Markdown the engine compiled, the PDF, the fields printed as blank
    lines, the text's `version` and whether it is still a draft (the BOZZA watermark is on)."""

    markdown: str
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


@dataclass(frozen=True)
class Compiled:
    """The engine's output before pandoc: the Markdown, and the label of each sentinel."""

    markdown: str
    labels: dict[str, str]
    front: dict[str, str]


def _variable_paths(nodes: tuple[Node, ...]) -> Iterator[tuple[str, ...]]:
    """Every `{{path}}` a text prints. A path an `{{#if}}` only tests is not one of them: asking
    whether a clause applies must not make it print a blank."""
    for node in nodes:
        if isinstance(node, VariableNode):
            yield node.path
        elif isinstance(node, IfNode):
            yield from _variable_paths(node.then)
            yield from _variable_paths(node.otherwise)
        elif isinstance(node, EachNode):
            yield from _variable_paths(node.body)


def _value_at(scope: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = scope
    for part in path:
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _set_at(scope: dict[str, Any], path: tuple[str, ...], value: str) -> None:
    current = scope
    for part in path[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[path[-1]] = value


def _yaml_safe(value: Any) -> Any:
    """A scope value for a double-quoted YAML string in the front matter: escaped for Markdown
    first, since pandoc reads a metadata string as Markdown, then for YAML's own backslash and
    quote."""
    if isinstance(value, Mapping):
        return {key: _yaml_safe(item) for key, item in value.items()}
    if isinstance(value, str):
        return escape_for("markdown", value).replace("\\", "\\\\").replace('"', '\\"')
    return value


def compile_text(body: str, scope: Mapping[str, Any]) -> Compiled:
    """The text's Markdown with `scope` filled in. A field the scope leaves empty (or does not
    have) prints as a sentinel, which `typeset` turns into a labelled blank line.

    The front matter is YAML, where the engine's Markdown escaping would be an error (a backslash
    in a double-quoted string), so its fields are escaped for YAML and the rest of the text for
    Markdown."""
    body = body.replace("[[", _PROPOSAL_OPEN).replace("]]", _PROPOSAL_CLOSE)
    nodes = parse_template(body)
    filled: dict[str, Any] = {
        key: dict(value) if isinstance(value, Mapping) else value for key, value in scope.items()
    }
    labels: dict[str, str] = {}
    for path in _variable_paths(nodes):
        value = _value_at(filled, path)
        if value is None or (isinstance(value, str) and not value.strip()):
            sentinel = SENTINEL.format(number=len(labels))
            labels[sentinel] = blank_label(path)
            _set_at(filled, path, sentinel)
    head, rest = split_front_matter(body)
    markdown = render_template(head, _yaml_safe(filled), context="plain") + render_template(
        rest, filled
    )
    return Compiled(markdown, labels, check_front_matter(markdown))


def _typst_string(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return '"' + escaped.replace("\r", "").replace("\n", "\\n").replace("\t", " ") + '"'


def _count_proposals(typst: str) -> tuple[int, int]:
    return typst.count(_PROPOSAL_OPEN), typst.count(_PROPOSAL_CLOSE)


def _typst_source(
    compiled: Compiled, issuer: str, workdir: Path, settings: Settings, *, signing: bool
) -> tuple[Path, list[str]]:
    """pandoc's Typst with every blank filled and every proposal marked: the path, and the
    fields left blank."""
    front = compiled.front
    draft = front["status"] == "draft"
    source = workdir / "source.md"
    source.write_text(compiled.markdown, encoding="utf-8")
    intermediate = workdir / "letter.typ"
    with RENDER_SLOTS:
        pandoc = run_tool(
            [
                settings.pandoc_binary,
                "--sandbox",
                f"--from={PANDOC_FROM}",
                "--to=typst",
                "--standalone",
                "--template",
                str(LETTER_TEMPLATE),
                # The articles are `##`, under a title that comes from the front matter.
                "--shift-heading-level-by=-1",
                "--wrap=preserve",
                "-V",
                f"draft={'true' if draft else 'false'}",
                "-V",
                f"forsigning={'true' if signing else 'false'}",
                "-V",
                f"issuer={escape_for('typst', issuer)}",
                "-V",
                f"mainfont={MAIN_FONT}",
                "-o",
                str(intermediate),
                str(source),
            ],
            workdir,
            "pandoc",
        )
    if pandoc.returncode != 0:
        raise ValidationFailed(
            ENTITY,
            "body_markdown",
            "il Markdown della lettera non e' convertibile",
            expected="Markdown valido",
        )
    typst = defeat_typst_autotypography(
        read_bounded(intermediate, MAX_INTERMEDIATE_BYTES).decode("utf-8")
    )
    # The markers rely on pandoc leaving a run of letters and digits alone and on how its Typst
    # writer escapes brackets, which a release could change: count them on either side of pandoc
    # rather than trust it, since a lost blank or an unmarked proposal prints without an error.
    lost = [sentinel for sentinel in compiled.labels if sentinel not in typst]
    if lost:
        raise ValidationFailed(
            ENTITY,
            "body_markdown",
            f"pandoc ha perso {len(lost)} campi della lettera",
            expected="i campi della lettera attraversano pandoc intatti",
        )
    blank: list[str] = []
    for sentinel, label in compiled.labels.items():
        typst = typst.replace(sentinel, f"#field({_typst_string(label)});")
        blank.append(label)
    opened, closed = _count_proposals(typst)
    if opened != closed:
        raise ValidationFailed(
            ENTITY,
            "body_markdown",
            f"il testo ha {opened} `[[` contro {closed} `]]`",
            expected="proposte chiuse",
        )
    if opened and not draft:
        raise ValidationFailed(
            ENTITY,
            "status",
            f"il testo non e' piu' una bozza e porta ancora {opened} proposte",
            expected="nessuna proposta in un testo final",
        )
    typst = typst.replace(_PROPOSAL_OPEN, "#proposal[").replace(_PROPOSAL_CLOSE, "];")
    intermediate.write_text(typst, encoding="utf-8")
    return intermediate, blank


def typeset(
    compiled: Compiled,
    issuer: str,
    settings: Settings,
    *,
    signing: bool = False,
    temp_root: Path | None = None,
) -> RenderedLetter:
    """The PDF of a compiled letter. `signing` asks for the copy that goes out for signature:
    the labels under the blanks the signing site fills are laid out and not drawn."""
    workdir = Path(tempfile.mkdtemp(prefix="pigrocrm-letter-", dir=temp_root))
    try:
        intermediate, blank = _typst_source(compiled, issuer, workdir, settings, signing=signing)
        output = workdir / "letter.pdf"
        with RENDER_SLOTS:
            result = run_tool(
                [
                    settings.typst_binary,
                    "compile",
                    "--root",
                    str(workdir),
                    "--creation-timestamp",
                    PDF_CREATION_TIMESTAMP,
                    str(intermediate),
                    str(output),
                ],
                workdir,
                "typst",
            )
        if result.returncode != 0 or not output.is_file():
            stderr = result.stderr.decode("utf-8", "replace")
            for spelling in {str(workdir), str(workdir.resolve())}:
                stderr = stderr.replace(spelling, "")
            raise ValidationFailed(
                ENTITY,
                "body_markdown",
                translate_typst_failure(stderr, intermediate.read_text(encoding="utf-8")),
                expected="un testo che Typst sa comporre",
            )
        front = compiled.front
        return RenderedLetter(
            markdown=compiled.markdown,
            pdf=read_bounded(output, MAX_PDF_BYTES),
            blank=blank,
            version=front["version"],
            draft=front["status"] == "draft",
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def signature_blanks(
    compiled: Compiled,
    issuer: str,
    settings: Settings,
    *,
    signing: bool = True,
    temp_root: Path | None = None,
) -> list[SignatureBlank]:
    """Where the blanks the signing site fills sit on the page: the same source as `typeset`,
    queried instead of compiled."""
    workdir = Path(tempfile.mkdtemp(prefix="pigrocrm-letter-", dir=temp_root))
    try:
        intermediate, _blank = _typst_source(compiled, issuer, workdir, settings, signing=signing)
        try:
            queried = subprocess.run(
                [
                    settings.typst_binary,
                    "query",
                    "--root",
                    str(workdir),
                    "--field",
                    "value",
                    "--format",
                    "json",
                    str(intermediate),
                    f"<{SIGNATURE_LABEL}>",
                ],
                cwd=workdir,
                capture_output=True,
                check=False,
                timeout=RENDER_TIMEOUT_SECONDS,
                env=tool_environment(workdir),
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            raise ValidationFailed(
                ENTITY,
                "render",
                "la ricerca delle firme nella lettera non e' riuscita",
                expected="typst installato e un testo che compone",
            ) from exc
        if queried.returncode != 0:
            raise ValidationFailed(
                ENTITY,
                "render",
                "la ricerca delle firme nella lettera e' fallita: "
                + queried.stderr[:MAX_DIAGNOSTIC_BYTES].decode("utf-8", "replace").strip(),
                expected="un testo che Typst sa comporre",
            )
        try:
            return [
                SignatureBlank(
                    name=str(value["name"]),
                    page=int(value["page"]),
                    x=float(value["x"]),
                    y=float(value["y"]),
                    width=float(value["width"]),
                    height=float(value["height"]),
                )
                for value in json.loads(queried.stdout)
            ]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ValidationFailed(
                ENTITY,
                "render",
                "Typst ha risposto con blanks che la lettera non sa leggere",
                expected="nome, pagina e riquadro di ogni firma",
            ) from exc
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


__all__ = [
    "Compiled",
    "RenderedLetter",
    "SignatureBlank",
    "compile_text",
    "signature_blanks",
    "typeset",
]
