"""The anonymous CV as a PDF (spec § The PDF, REB-862): Typst over the body, in the contracts'
brand, for an admin to send to a client company.

The toolchain and the brand are the contracts' (`contracts/brand.py`: the palette, Outfit at
300 and 500, the echo logo, the static fonts built once per process), but without pandoc:
the body is written as `cv.json` in the call's own directory and `cv.typ` reads it with
`json(...)`. Every string the CV carries reaches the page as text, never as markup, so a CV
that says `#set page(...)` or `*bold*` prints those characters and cannot change the page.
`--creation-timestamp 0`, as the contracts, so one body always gives the same bytes.

Every word the page prints that is not the CV's is here, not in the template: the headings
in the CV's language, the head's line and the footer. The template only lays them out.

Nothing here logs. A failure is `CvPrintFailed`, a `ContractFailed` so the API answers it as
it answers a contract that could not be typeset (a 503: the server's failure, not the
request's), with a sentence about the CV rather than a contract and nothing else: the API
neither returns nor logs a `ContractFailed`'s `detail`. That detail is Typst's own
diagnostic, which quotes the lines of `cv.typ` it failed on, not the CV the JSON carries.
"""

import json
import re
import shutil
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from rebase_core.anonymous_cv.schema import AnonymousCv
from rebase_core.contracts.brand import ECHO, fonts_dir, palette
from rebase_core.contracts.fields import ContractFailed
from rebase_core.contracts.render import TOOL_TIMEOUT_SECONDS
from rebase_core.errors import DomainError

TEMPLATE = Path(__file__).resolve().parent / "cv.typ"

# The web's `REMOTO_LABELS` (`lib/format.ts`), which Python has no copy of. Printed in
# Italian whatever the CV's language, like the footer: they are rebase's words.
MODALITA_LABELS = {"remoto": "Da remoto", "ibrido": "Ibrido", "in_sede": "In sede"}
# The web's `SENIORITY_LABELS`, the card's four levels as a company reads them.
SENIORITY_LABELS = {"junior": "Junior", "mid": "Mid", "senior": "Senior", "lead": "Lead"}

FOOTER = "Profilo proposto da rebase · ciao@letsrebase.com · letsrebase.com"

# The sections' headings in the CV's language (`AnonymousCv.lingua`); each of
# `altre_sezioni` is headed by its own `titolo`.
HEADINGS: dict[str, dict[str, str]] = {
    "it": {
        "sommario": "Profilo",
        "esperienze": "Esperienze",
        "competenze": "Competenze",
        "formazione": "Formazione",
        "certificazioni": "Certificazioni",
        "lingue": "Lingue",
    },
    "en": {
        "sommario": "Profile",
        "esperienze": "Experience",
        "competenze": "Skills",
        "formazione": "Education",
        "certificazioni": "Certifications",
        "lingue": "Languages",
    },
}

# The longest slug of the title a filename carries (`rebase-cv-<slug>-<ref>.pdf`).
SLUG_MAX_LENGTH = 40


class CvPrintFailed(ContractFailed):
    """The CV could not be typeset: Typst missing, slow or failing, the brand's files gone.
    A `ContractFailed`, so the API's handler answers it with a 503 and this sentence."""

    def __init__(self, detail: str) -> None:
        DomainError.__init__(self, "Non riesco a stampare il CV anonimo.", detail=detail)
        self.detail = detail


@dataclass(frozen=True)
class CvHead:
    """What the page's head says beside the CV's own title, from the hub rather than the CV:
    the card's seniority and years (the admin's correction over them), the card's city and
    the profile's work mode, each `None` when unknown. `ref` is the last eight characters
    of the freelancer's id (its random tail: the head of a UUIDv7 is when it was made),
    which the footer prints so a company can name the profile."""

    seniority: str | None
    anni: int | None
    luogo: str | None
    modalita: str | None
    ref: str


class CvRenderer(Protocol):
    """The seam the route takes, so the API's tests hand `FakeCvRenderer` and need no
    binary."""

    def render(self, cv: AnonymousCv, head: CvHead) -> bytes: ...


class TypstCvRenderer:
    """The production renderer: Typst on this machine."""

    def render(self, cv: AnonymousCv, head: CvHead) -> bytes:
        return render(cv, head)


class FakeCvRenderer:
    """Keeps every call, `(cv, head)`, in `.calls` and answers a PDF's first bytes, the way
    `RecordingSender` (`mail.py`) keeps every mail."""

    def __init__(self) -> None:
        self.calls: list[tuple[AnonymousCv, CvHead]] = []

    def render(self, cv: AnonymousCv, head: CvHead) -> bytes:
        self.calls.append((cv, head))
        return b"%PDF-fake"


def _years(anni: int, lingua: Literal["it", "en"]) -> str:
    """The card's years as the web's `formatExperience` writes them, in the CV's language."""
    if lingua == "en":
        if anni == 0:
            return "less than a year of experience"
        return "1 year of experience" if anni == 1 else f"{anni} years of experience"
    if anni == 0:
        return "meno di un anno di esperienza"
    return "1 anno di esperienza" if anni == 1 else f"{anni} anni di esperienza"


def head_line(head: CvHead, lingua: Literal["it", "en"]) -> str:
    """The line under the title: seniority, years, city and work mode, each left out when
    unknown, joined by « · ». Empty when nothing is known."""
    parts: list[str] = []
    if head.seniority:
        parts.append(SENIORITY_LABELS.get(head.seniority, head.seniority))
    if head.anni is not None:
        parts.append(_years(head.anni, lingua))
    if head.luogo and head.luogo.strip():
        parts.append(head.luogo.strip())
    if head.modalita in MODALITA_LABELS:
        parts.append(MODALITA_LABELS[head.modalita])
    return " · ".join(parts)


def filename(cv: AnonymousCv, ref: str) -> str:
    """`rebase-cv-<title as an ASCII slug>-<ref>.pdf`: accents folded, anything else a
    hyphen, at most `SLUG_MAX_LENGTH` characters cut at a word's end. A title with no ASCII
    letter or digit gives `rebase-cv-<ref>.pdf`."""
    folded = unicodedata.normalize("NFKD", cv.titolo).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")
    if len(slug) > SLUG_MAX_LENGTH:
        cut = slug[: SLUG_MAX_LENGTH + 1]
        slug = cut.rsplit("-", 1)[0] if "-" in cut else cut[:SLUG_MAX_LENGTH]
        slug = slug[:SLUG_MAX_LENGTH].strip("-")
    return f"rebase-cv-{slug}-{ref}.pdf" if slug else f"rebase-cv-{ref}.pdf"


def _document(cv: AnonymousCv, head: CvHead) -> dict[str, object]:
    """What `cv.typ` reads: the CV as validated, the head's words, the brand's colours."""
    return {
        "cv": cv.model_dump(mode="json"),
        "head": {"riga": head_line(head, cv.lingua), "rif": f"Rif. {head.ref}"},
        "headings": HEADINGS[cv.lingua],
        "footer": FOOTER,
        "palette": palette(),
        "echo": ECHO.name,
    }


def _typst(args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["typst", *args], capture_output=True, text=True, timeout=TOOL_TIMEOUT_SECONDS
        )
    except FileNotFoundError as exc:
        raise CvPrintFailed("typst is not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise CvPrintFailed(f"typst took longer than {TOOL_TIMEOUT_SECONDS} seconds") from exc


def render(cv: AnonymousCv, head: CvHead) -> bytes:
    """The PDF, typeset in a directory of its own that is removed when this returns. The
    echo is copied in beside the template, since Typst reads a picture only from inside its
    `--root`; the static fonts are the one thing shared between calls (`brand.fonts_dir`)."""
    workdir = Path(tempfile.mkdtemp(prefix="rebase-cv-"))
    try:
        try:
            document = _document(cv, head)
            fonts = fonts_dir()
            shutil.copyfile(TEMPLATE, workdir / TEMPLATE.name)
            shutil.copyfile(ECHO, workdir / ECHO.name)
        except ContractFailed as exc:
            raise CvPrintFailed(exc.detail) from exc
        except OSError as exc:
            raise CvPrintFailed(f"cannot copy the template or the echo: {exc}") from exc
        (workdir / "cv.json").write_text(json.dumps(document, ensure_ascii=False), "utf-8")
        output = workdir / "cv.pdf"
        compiled = _typst(
            [
                "compile",
                "--root",
                str(workdir),
                "--font-path",
                str(fonts),
                "--ignore-system-fonts",
                "--creation-timestamp",
                "0",
                str(workdir / TEMPLATE.name),
                str(output),
            ]
        )
        if compiled.returncode != 0:
            raise CvPrintFailed(f"typst failed:\n{compiled.stderr.strip()}")
        warnings = [line for line in compiled.stderr.splitlines() if "warning" in line]
        if warnings:
            raise CvPrintFailed("typst warned:\n" + "\n".join(warnings))
        return output.read_bytes()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
