"""One way to hand a stored CV to a browser, for the admin's download and the member's."""

from urllib.parse import quote

from fastapi import Response

from rebase_core.schemas import CvFile


def cv_response(cv: CvFile) -> Response:
    """The bytes as an attachment. ASCII-safe filename: a quote or a newline in a name
    the applicant chose must not become a header injection, and this is the one place
    that rule lives. `isalnum()` alone is Unicode-aware and would let a script outside
    Latin-1 through, which Starlette then fails to encode into the header at all.

    The real name travels too, as `filename*` (RFC 5987, PigroCRM's own shape for this
    header), percent-encoded so it stays pure ASCII: a browser that understands it shows
    an accented Italian name whole rather than the ASCII fallback with every accent
    turned into an underscore, and one that does not falls back to `filename=`."""
    safe = (
        "".join(
            ch if (ch.isascii() and ch.isalnum()) or ch in "._- " else "_" for ch in cv.filename
        )
        or "cv.pdf"
    )
    disposition = f'attachment; filename="{safe}"'
    if safe != cv.filename:
        disposition += f"; filename*=UTF-8''{quote(cv.filename, safe='')}"
    return Response(
        content=cv.content,
        media_type=cv.mime,
        headers={"Content-Disposition": disposition},
    )


def pdf_response(filename: str, content: bytes) -> Response:
    """A contract's PDF as an attachment (REB-387), through `cv_response` so the one
    header-safety rule stays in one place. The name carries a letter number, never
    something a person typed, but a second shape for this header is what would drift."""
    return cv_response(CvFile(filename=filename, mime="application/pdf", content=content))


def perk_response(content: bytes, filename: str) -> Response:
    """A perk's file as an attachment. Its name is ours rather than something a person
    typed, so it needs no sanitising, but it is written here anyway so a second perk
    cannot invent a shape of its own for this header."""
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
