"""One way to answer with a file.

Every route that returns bytes as a download builds its `Content-Disposition` here, so the
filename is percent-encoded into `filename*` (RFC 5987) and never interpolated into a
header, and a stored file is never sniffed into something executable. The names are server
slugs today, so nothing dangerous reaches a header; encoding them anyway means a future
change to a slug cannot turn a document title into a response header.
"""

from typing import Literal
from urllib.parse import quote

from fastapi.responses import Response


def attachment_response(
    data: bytes,
    content_type: str,
    filename: str,
    *,
    disposition: Literal["attachment", "inline"] = "attachment",
    headers: dict[str, str] | None = None,
) -> Response:
    """`data` as a file called `filename`. `inline` is for an image the page draws from the
    API (a logo), `attachment` for everything the browser should save; `headers` adds to
    the two every file carries."""
    return Response(
        content=data,
        media_type=content_type,
        headers={
            **(headers or {}),
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(filename, safe='')}",
            "X-Content-Type-Options": "nosniff",
        },
    )
