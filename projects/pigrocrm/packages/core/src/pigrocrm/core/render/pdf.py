"""Pandoc, then Typst. Two subprocesses, no shell, no user input on either argv.

The previous system ran a single `pandoc --pdf-engine=typst`. This runs the two stages separately
so the intermediate .typ exists as a file we own -- which is the only way to satisfy
spec 6's "l'errore contiene la riga del template", because Typst's diagnostics name a
line in that file and nothing else can map it back.

Every path in either argument list is generated here from `tempfile.mkdtemp()`. Every
value a user or an agent supplied is already inside `markdown`, already escaped by
`pigrocrm.core.templates.renderer`. There is no code path in this module where a
request-derived string becomes a command-line argument.
"""

import base64
import re
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pigrocrm.core.config import Settings
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.render.diagnostics import translate_typst_failure
from pigrocrm.core.templates.renderer import render_template

ENTITY = "document"
RENDER_TIMEOUT_SECONDS = 30
ASSETS_DIR = Path(__file__).resolve().parent / "assets"
PANDOC_TEMPLATE = ASSETS_DIR / "pandoc-template.typst"
HEADER_TEMPLATE = ASSETS_DIR / "header.typ.template"

# The names a Typst job writes an azienda's images under, which are the names the
# header templates and the seeded offer template reference (REB-627, spec 2026-10-03
# §6). The bundle carries no logo and no signature any more: REB-48 called shipping
# one person's assets a defect, and a per-azienda upload is what finally removed the
# need. A job that is handed no signature still gets `sign_is.png`, a transparent
# pixel, so an offer template seeded before the upload existed keeps compiling.
MEDIA_LOGO_PNG = "logo.png"
MEDIA_LOGO_SVG = "logo.svg"
MEDIA_FIRMA = "sign_is.png"
# A 1x1 fully transparent PNG, the smallest valid one.
BLANK_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
# The previous system's own reader extensions, carried over unchanged: `raw_attribute` is what makes
# ```{=typst} a raw block rather than a code listing, and without it the whole
# two-context escaping design has only one context.
PANDOC_FROM = "markdown+link_attributes+pipe_tables+raw_attribute"
# Typst 0.14 embeds Libertinus Serif, so no font package is needed in the image.
MAIN_FONT = "Libertinus Serif"

# Typst stamps the PDF's own `/CreationDate` with the wall-clock time of the compile
# by default -- confirmed live: two `render_pdf` calls on byte-identical `markdown`
# and `header_typst`, seconds apart, produced PDFs differing only in that one
# metadata field, and therefore two different SHA-256 hashes (`DocumentVersion.
# hash_sha256`). That silently breaks spec 11's whole reproducibility promise: a
# `regenerate` that reproduces the same *source* but not the same *bytes* is not
# reproduction, it is merely re-rendering. Pinned to a fixed value -- the
# reproducible-builds.org SOURCE_DATE_EPOCH convention, which Typst implements
# natively as this same flag -- rather than left to derive from `document.created_at`
# or similar: the document's own true creation instant is already recorded, honestly,
# in the database row and (via `{{offerta.data}}`-style declared variables) in the
# document's own visible text; the PDF container's internal metadata field is not a
# fact this project has ever promised to keep truthful, and making every render use
# the same fixed value is what buys byte-for-byte reproducibility unconditionally,
# for every render, not only a regeneration that remembers to thread the original
# instant back through.
PDF_CREATION_TIMESTAMP = "0"

# Typst's own markup grammar -- independent of Pandoc, and with no compile-time flag
# to turn it off (checked against `typst compile --help` on the pinned version) --
# unconditionally merges a bare run of two-or-more adjacent "-" into an en/em dash and
# a bare run of three-or-more adjacent "." into an ellipsis, wherever such a run
# occurs in *markup* text. That is exactly the residual `templates/escaping.py`
# documents and cannot close itself: `escape_markdown`'s backslash protects a value
# only up to Pandoc's own Markdown *reader*; Pandoc's Typst *writer* then re-serialises
# the parsed text as bare characters with no memory of which run was an escaped
# customer value and which was ordinary prose (confirmed live -- see
# test_the_render_pipeline_closes_the_residual_dash_and_ellipsis_gap in
# test_template_escaping.py), and Typst's lexer runs a second time, independently of
# Pandoc, over whatever bare text the writer produced.
#
# The fix has to live here, between the two subprocesses, because this is the only
# point where a bare run and a `\`-escaped one are still different byte sequences:
# Typst treats a backslash-escaped character as a fully resolved literal, never a
# candidate for the ligature rule, which only ever fires on unescaped runs (confirmed
# live: `a-\-b` compiles to the two literal characters "--", not an en dash, and
# `a\-\-b` -- every character escaped -- compiles the same way for a run of any
# length). Applied to the *whole* intermediate file rather than only the parts Pandoc
# generated from ordinary body text: the one thing that additionally suppresses is
# Typst's own ligature for a hyphen/dot run a raw ```{=typst} block's author typed
# directly as native Typst markup, a cosmetic trade-off this project's own template
# assets (`assets/pandoc-template.typst`, `assets/header.typ.template`,
# `assets/template-offer.md`) never rely on -- against the alternative of tracking
# which byte ranges of Pandoc's *output* came from which input segment, which Pandoc's
# own architecture does not preserve past the parse step.
_BARE_HYPHEN_RUN = re.compile(r"-{2,}")
_BARE_DOT_RUN = re.compile(r"\.{3,}")


def _defeat_typst_autotypography(typst_source: str) -> str:
    """Backslash-protect every bare hyphen/dot run left in `typst_source` so
    Typst's own ligature rule never fires on text this pipeline must reproduce
    byte for byte. Does not touch line counts (every replacement stays on its own
    line), so `diagnostics.template_line_for`'s marker arithmetic is unaffected."""
    typst_source = _BARE_HYPHEN_RUN.sub(lambda m: "\\-" * len(m.group(0)), typst_source)
    return _BARE_DOT_RUN.sub(lambda m: "\\." * len(m.group(0)), typst_source)


def logo_flags(logo: str | None) -> dict[str, bool]:
    """The three flags a header template branches on: which logo file the job carries,
    or none, in which case the header sets the azienda's name in type instead."""
    return {
        "png": logo == MEDIA_LOGO_PNG,
        "svg": logo == MEDIA_LOGO_SVG,
        "nessuno": logo is None,
    }


def build_header(profile: dict[str, Any], *, logo: str | None = None) -> str:
    """The Typst page header, filled from the emitter profile. `logo` is the media file
    the job carries for this azienda (`MEDIA_LOGO_PNG`, `MEDIA_LOGO_SVG`) or `None`,
    which draws the `ragione_sociale` in type where the image would be.

    Rendered through the same engine as the document body, so the issuer's own values
    get the same escaping -- an `@` in an email address is a Typst reference and would
    otherwise fail the compile, which is precisely the bug the previous system's `header.typ` worked
    around by hand-writing `mario\\@example.com` in the source. `header.typ`
    itself is never handed to Pandoc (it reaches Typst directly, via
    `--include-in-header`), so the "markdown" context `render_template` applies by
    default here never goes through Pandoc's writer at all -- it is not the context
    the placeholders in this file semantically belong to (they are markup position
    inside a raw Typst file, `escape_typst`'s job), but `escape_markdown` and
    `escape_typst` are the identical escaper today (see `templates/escaping.py`), and
    wrapping this file's content in a ```{=typst} fence to get the "typst" context on
    purpose would leave the fence delimiters themselves as literal, invalid text in
    the rendered header -- worse than the mismatch it would fix.
    """
    return render_template(
        HEADER_TEMPLATE.read_text(encoding="utf-8"),
        {"emittente": profile, "logo": logo_flags(logo)},
    )


_MEDIA_NAMES = frozenset({MEDIA_LOGO_PNG, MEDIA_LOGO_SVG, MEDIA_FIRMA})


def _run(argv: list[str], workdir: Path) -> subprocess.CompletedProcess[bytes]:
    """Always a list, never a string; never `shell=True`; always bounded.

    `check=False` on purpose: the whole point is to read the compiler's diagnostics
    and translate them, which `check=True` would replace with a `CalledProcessError`
    nobody can render for a user.
    """
    try:
        return subprocess.run(
            argv,
            cwd=workdir,
            capture_output=True,
            check=False,
            timeout=RENDER_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValidationFailed(
            ENTITY,
            "corpo_markdown",
            f"la composizione del PDF ha superato {RENDER_TIMEOUT_SECONDS} secondi",
            expected="un documento piu' semplice",
        ) from exc
    except FileNotFoundError as exc:
        raise ValidationFailed(
            ENTITY,
            "render",
            f"strumento di composizione non installato: {argv[0]}",
            expected="pandoc e typst nell'immagine dell'API",
        ) from exc


def render_pdf(
    markdown: str,
    *,
    header_typst: str,
    settings: Settings,
    temp_root: Path | None = None,
    media: Mapping[str, bytes] | None = None,
) -> bytes:
    """Compiled Markdown in, PDF bytes out.

    `media` is what the job's `media/` directory holds beyond the signature placeholder:
    the azienda's logo under `MEDIA_LOGO_PNG` or `MEDIA_LOGO_SVG` and its signature under
    `MEDIA_FIRMA`, as `AziendaAssets.media_for` builds it. The names are a fixed set
    (`_MEDIA_NAMES`): a caller cannot write an arbitrary file into the compile root.

    The whole render happens inside one throwaway directory that is removed on every
    path, success or failure. `media/` is copied in rather than referenced in place,
    so `--root` can be the workdir: a shared, possibly read-only assets directory as
    `--root` would let two concurrent renders write into the same place, and -- the
    risk that actually matters -- would let a hostile Typst expression (`#read(...)`,
    a failing `#import` whose error message quotes the target file's own first line
    back) reach anything else living under that wider root. The compile root is
    exactly this one document's own throwaway directory and nothing else.

    `temp_root` is where that throwaway directory is created; `None` means the system
    temp directory, which is what every production caller uses. It exists so a test
    can watch a directory nobody else writes to: the system temp directory is shared
    by every process on the host, and under xdist another worker's in-flight render
    is indistinguishable there from a directory this render failed to remove (ORB-40).
    """
    workdir = Path(tempfile.mkdtemp(prefix="pigrocrm-render-", dir=temp_root))
    try:
        media_dir = workdir / "media"
        media_dir.mkdir()
        files = {MEDIA_FIRMA: BLANK_PNG, **(media or {})}
        for name, data in files.items():
            if name not in _MEDIA_NAMES:
                raise ValueError(f"not a media file a render takes: {name!r}")
            (media_dir / name).write_bytes(data)
        source = workdir / "source.md"
        header = workdir / "header.typ"
        intermediate = workdir / "intermediate.typ"
        output = workdir / "out.pdf"
        source.write_text(markdown, encoding="utf-8")
        header.write_text(header_typst, encoding="utf-8")

        pandoc = _run(
            [
                settings.pandoc_binary,
                f"--from={PANDOC_FROM}",
                "--to=typst",
                "--standalone",
                "--template",
                str(PANDOC_TEMPLATE),
                "--include-in-header",
                str(header),
                "--resource-path",
                str(workdir),
                "-V",
                f"mainfont={MAIN_FONT}",
                "-o",
                str(intermediate),
                str(source),
            ],
            workdir,
        )
        if pandoc.returncode != 0:
            raise ValidationFailed(
                ENTITY,
                "corpo_markdown",
                "il Markdown del documento non e' convertibile",
                expected="Markdown valido",
            )

        # Between the two subprocesses, not before and not after: see
        # `_defeat_typst_autotypography`'s own comment for why this is the only point
        # where the fix can take effect at all.
        typst_source = _defeat_typst_autotypography(intermediate.read_text(encoding="utf-8"))
        intermediate.write_text(typst_source, encoding="utf-8")
        typst = _run(
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
        )
        if typst.returncode != 0 or not output.is_file():
            # `translate_typst_failure` only ever reads the *line number* out of
            # Typst's own "┌─ path:line:col" location, never the path text itself --
            # but a hostile expression can also put the workdir's absolute path
            # somewhere else in the message body, not only in that location line.
            # Confirmed live: `#read("/etc/passwd")` fails with
            # "file not found (searched at <workdir>/etc/passwd)" on the error's own
            # first line, which `_ERROR_RE` captures as part of `message`, not as the
            # location. Scrubbing the one absolute path this render's own subprocess
            # could possibly have printed -- its own workdir, known here and nowhere
            # else -- closes that regardless of which part of Typst's message it
            # turns up in. Both spellings, not just one: on a host where the system
            # temp directory is itself a symlink (confirmed live on macOS, `/var` ->
            # `/private/var`), Typst reports the *resolved* path while `workdir` still
            # holds the unresolved one Python handed back from `mkdtemp`, and
            # scrubbing only one spelling left the other sitting in the message.
            stderr = typst.stderr.decode("utf-8", "replace")
            for spelling in {str(workdir), str(workdir.resolve())}:
                stderr = stderr.replace(spelling, "")
            raise ValidationFailed(
                ENTITY,
                "corpo_markdown",
                translate_typst_failure(stderr, typst_source),
                expected="un template Typst valido",
            )
        return output.read_bytes()
    finally:
        # `ignore_errors=True`: a failed cleanup must never mask the real error, and
        # the directory is disposable by construction.
        shutil.rmtree(workdir, ignore_errors=True)
