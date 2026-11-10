"""Reading a one-file multipart upload without letting the body in before the role check.

A declared `File()` parameter makes FastAPI parse the multipart body before it calls the
handler, and Starlette spools every file part to disk while it does -- so a `readonly`
caller would have had up to the proxy's limit written to disk before the 403. A handler
that takes the `Request` instead checks the role first and reads the form itself, through
`read_upload`, which reads the form only after that check and keeps one byte past the
limit of the file at most, so a larger file is refused by the service's own sentence. The
body itself is still parsed (and spooled) by `request.form()`: what the order buys is that a
caller who may not write never gets that far.

The schema is stated by hand for the OpenAPI document (`single_file_body`).
"""

from typing import Any

from fastapi import Request
from starlette.datastructures import UploadFile as StarletteUploadFile

from pigrocrm.core.errors import ValidationFailed


def single_file_body(
    description: str, text_fields: dict[str, dict[str, Any]] | None = None
) -> dict[str, Any]:
    """`openapi_extra` for a route whose body is one multipart part called `file`, and optionally
    some text parts beside it (`text_fields`: the part's name and its JSON schema)."""
    return {
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["file"],
                        "properties": {
                            "file": {
                                "type": "string",
                                "format": "binary",
                                "description": description,
                            },
                            **(text_fields or {}),
                        },
                    }
                }
            },
        }
    }


async def read_upload_with_fields(
    request: Request, limit: int, *names: str
) -> tuple[bytes, str | None, dict[str, str]]:
    """As `read_upload`, and the text parts called `names` that came beside the file (the ones
    that were sent, as strings)."""
    async with request.form() as form:
        part = form.get("file")
        if not isinstance(part, StarletteUploadFile):
            raise ValidationFailed(
                "upload", "file", "manca il file", expected="un file nel campo `file`"
            )
        fields = {name: value for name in names if isinstance(value := form.get(name), str)}
        return await part.read(limit + 1), part.content_type, fields


async def read_upload(request: Request, limit: int) -> tuple[bytes, str | None]:
    """The `file` part of the request as `(bytes, client-supplied content type)`, at most
    `limit + 1` bytes of it: the service refuses an oversize body with its own sentence, so
    one byte over is all it has to see."""
    data, content_type, _ = await read_upload_with_fields(request, limit)
    return data, content_type
