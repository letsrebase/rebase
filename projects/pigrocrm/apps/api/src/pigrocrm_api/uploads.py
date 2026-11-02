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


def single_file_body(description: str) -> dict[str, Any]:
    """`openapi_extra` for a route whose body is one multipart part called `file`."""
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
                            }
                        },
                    }
                }
            },
        }
    }


async def read_upload(request: Request, limit: int) -> tuple[bytes, str | None]:
    """The `file` part of the request as `(bytes, client-supplied content type)`, at most
    `limit + 1` bytes of it: the service refuses an oversize body with its own sentence, so
    one byte over is all it has to see."""
    async with request.form() as form:
        part = form.get("file")
        if not isinstance(part, StarletteUploadFile):
            raise ValidationFailed(
                "upload", "file", "manca il file", expected="un file nel campo `file`"
            )
        return await part.read(limit + 1), part.content_type
