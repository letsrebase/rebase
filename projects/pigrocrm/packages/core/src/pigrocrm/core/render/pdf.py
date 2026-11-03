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

import os
import re
import shutil
import subprocess
import tempfile
import threading
import zlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, NamedTuple

from pigrocrm.core.config import Settings
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.render.diagnostics import translate_typst_failure
from pigrocrm.core.templates.renderer import render_template

ENTITY = "document"
RENDER_TIMEOUT_SECONDS = 30
# How many renders may run their two subprocesses at once in this process. A burst of
# requests would otherwise fork as many Pandoc and Typst pairs as there are worker
# threads; the rest wait for a slot.
MAX_CONCURRENT_RENDERS = 2
_RENDER_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_RENDERS)
# Ceilings on what a render hands back to this process. The PDF bound is well under the
# 100 MiB the archive accepts; the intermediate Typst source of a real document is a few
# kilobytes, so a large one is a runaway; diagnostics are read only up to their first
# error.
MAX_PDF_BYTES = 50 * 1024 * 1024
MAX_INTERMEDIATE_BYTES = 16 * 1024 * 1024
MAX_DIAGNOSTIC_BYTES = 64 * 1024
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


def _png_chunk(kind: bytes, body: bytes) -> bytes:
    return (
        len(body).to_bytes(4, "big")
        + kind
        + body
        + (zlib.crc32(kind + body) & 0xFFFFFFFF).to_bytes(4, "big")
    )


# A 1x1 fully transparent PNG, built from its chunks rather than pasted as base64 (a pasted
# constant once turned out to be half opaque). One RGBA pixel of zeros behind a zero filter
# byte; the test decodes it and reads the alpha.
BLANK_PNG = (
    b"\x89PNG\r\n\x1a\n"
    + _png_chunk(b"IHDR", (1).to_bytes(4, "big") + (1).to_bytes(4, "big") + bytes([8, 6, 0, 0, 0]))
    + _png_chunk(b"IDAT", zlib.compress(bytes(5)))
    + _png_chunk(b"IEND", b"")
)
# The previous system's own reader extensions, carried over unchanged: `raw_attribute` is what makes
# ```{=typst} a raw block rather than a code listing, and without it the whole
# two-context escaping design has only one context.
PANDOC_FROM = "markdown+link_attributes+pipe_tables+raw_attribute"
# Typst 0.14 embeds Libertinus Serif, so no font package is needed in the image.
MAIN_FONT = "Libertinus Serif"

# Typst stamps the PDF's `/CreationDate` with the wall clock unless told otherwise, which
# gives byte-identical input two different hashes (`DocumentVersion.hash_sha256`) and
# breaks the reproducibility `regenerate` promises (spec 11). A fixed value, through
# Typst's SOURCE_DATE_EPOCH flag, makes every render reproducible.
PDF_CREATION_TIMESTAMP = "0"

# Typst merges a bare run of two or more "-" into an en/em dash and a bare run of three
# or more "." into an ellipsis in markup text, and has no flag to turn that off. A
# backslash-escaped character is a resolved literal that never takes part in the rule.
# `escape_markdown` protects a value only as far as Pandoc's Markdown reader: Pandoc's
# Typst writer re-serialises the parsed text as bare characters, so the runs Typst sees
# cannot be told apart from ordinary prose after that point. Between the two subprocesses
# a bare run and an escaped one are still different bytes, so the protection is applied
# here, to the whole intermediate file (this also suppresses the ligature for a run typed
# inside a raw ```{=typst} block, which no template asset relies on).
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


class _Outcome(NamedTuple):
    returncode: int
    stderr: bytes


def _tool_environment(workdir: Path) -> dict[str, str]:
    """The environment both tools run in.

    Typst downloads an `@preview/...` package on first import, and `typst compile` has
    no flag to turn that off (0.14.2). A template is admin-authored, but a compile that
    can reach the network is still a compile that can be made to. Every proxy variable
    Typst's downloader reads is pointed at a closed local port, `NO_PROXY` is dropped so
    nothing bypasses it, and both package directories are empty ones inside the throwaway
    workdir, so an `@preview` import fails fast with a diagnostic instead of fetching.
    """
    kept = {
        name: value
        for name, value in os.environ.items()
        if name.upper() not in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}
    }
    dead_proxy = "http://127.0.0.1:9"
    packages = str(workdir / "no-packages")
    return {
        **kept,
        "HTTP_PROXY": dead_proxy,
        "HTTPS_PROXY": dead_proxy,
        "ALL_PROXY": dead_proxy,
        "http_proxy": dead_proxy,
        "https_proxy": dead_proxy,
        "all_proxy": dead_proxy,
        "TYPST_PACKAGE_PATH": packages,
        "TYPST_PACKAGE_CACHE_PATH": packages,
    }


def _run(argv: list[str], workdir: Path, stage: str) -> _Outcome:
    """Always a list, never a string; never `shell=True`; always bounded in time and in
    the diagnostics it keeps.

    Neither tool writes its result to stdout (both take an output path), so stdout is
    discarded. Stderr goes to a file in the workdir and only its first
    `MAX_DIAGNOSTIC_BYTES` are read back, so a tool that prints without end cannot grow
    this process's memory.

    `check=False` on purpose: the whole point is to read the compiler's diagnostics
    and translate them, which `check=True` would replace with a `CalledProcessError`
    nobody can render for a user.
    """
    log = workdir / f"{stage}.log"
    try:
        with log.open("wb") as stderr:
            completed = subprocess.run(
                argv,
                cwd=workdir,
                stdout=subprocess.DEVNULL,
                stderr=stderr,
                check=False,
                timeout=RENDER_TIMEOUT_SECONDS,
                env=_tool_environment(workdir),
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
    with log.open("rb") as stderr:
        return _Outcome(completed.returncode, stderr.read(MAX_DIAGNOSTIC_BYTES))


def _read_bounded(path: Path, limit: int) -> bytes:
    """The file's bytes, refused when it is larger than `limit`: the size is read from
    the file system before a single byte is loaded."""
    if path.stat().st_size > limit:
        raise ValidationFailed(
            ENTITY,
            "corpo_markdown",
            f"il documento composto supera {limit} byte",
            expected="un documento piu' semplice",
        )
    return path.read_bytes()


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
    `MEDIA_FIRMA`, as `LegalEntityAssets.media_for` builds it. The names are a fixed set
    (`_MEDIA_NAMES`): a caller cannot write an arbitrary file into the compile root.

    The whole render happens inside one throwaway directory that is removed on every
    path, success or failure. `media/` is copied in rather than referenced in place,
    so `--root` can be the workdir: a shared, possibly read-only assets directory as
    `--root` would let two concurrent renders write into the same place, and -- the
    risk that actually matters -- would let a hostile Typst expression (`#read(...)`,
    a failing `#import` whose error message quotes the target file's own first line
    back) reach anything else living under that wider root. The compile root is
    exactly this one document's own throwaway directory and nothing else.

    At most `MAX_CONCURRENT_RENDERS` renders run the two subprocesses at once in this
    process; the others wait. A caller that holds a database transaction open while it
    waits holds a pooled connection too, so read what the render needs, end the
    transaction, then call this.

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

        with _RENDER_SLOTS:
            pandoc = _run(
                [
                    settings.pandoc_binary,
                    "--sandbox",
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
                "pandoc",
            )
            if pandoc.returncode != 0:
                raise ValidationFailed(
                    ENTITY,
                    "corpo_markdown",
                    "il Markdown del documento non e' convertibile",
                    expected="Markdown valido",
                )

            # Between the two subprocesses, not before and not after: see
            # `_defeat_typst_autotypography` for why this is the only point where the
            # fix can take effect at all.
            typst_source = _defeat_typst_autotypography(
                _read_bounded(intermediate, MAX_INTERMEDIATE_BYTES).decode("utf-8")
            )
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
                "typst",
            )
        if typst.returncode != 0 or not output.is_file():
            # A hostile expression can put the workdir's absolute path anywhere in
            # Typst's message, not only in the "┌─ path:line:col" location that
            # `translate_typst_failure` reads the line number from (`#read("/etc/passwd")`
            # fails with "file not found (searched at <workdir>/etc/passwd)" on the first
            # line). The one absolute path this render's own subprocess could have
            # printed is its own workdir, so scrub it, both spellings: where the system
            # temp directory is itself a symlink (macOS, `/var` -> `/private/var`) Typst
            # reports the resolved path while `workdir` holds the unresolved one.
            stderr = typst.stderr.decode("utf-8", "replace")
            for spelling in {str(workdir), str(workdir.resolve())}:
                stderr = stderr.replace(spelling, "")
            raise ValidationFailed(
                ENTITY,
                "corpo_markdown",
                translate_typst_failure(stderr, typst_source),
                expected="un template Typst valido",
            )
        return _read_bounded(output, MAX_PDF_BYTES)
    finally:
        # `ignore_errors=True`: a failed cleanup must never mask the real error, and
        # the directory is disposable by construction.
        shutil.rmtree(workdir, ignore_errors=True)
