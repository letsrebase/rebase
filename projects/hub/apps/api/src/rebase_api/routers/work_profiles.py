"""`POST /api/hub/work-profiles`: the intake of a work profile (REB-1124, spec 2026-11-17 § 3).

`Authorization: Bearer <access_token>` of the CLI's sign-in (`rebase_core.cli_auth`), multipart
with the one part `profile` (`candidate.json`, at most 512 KB). The token is checked first: the
body is read only for a bearer that names a live authorization, so a stranger's upload costs
nothing, and it is read in chunks up to the cap, so a body with no `Content-Length` cannot grow
past it either. Any other part is a 422 with the sentence of the spec. Whatever refuses a profile
after the token was accepted spends the token.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from rebase_api.deps import SessionDep, SettingsDep
from rebase_core.errors import WorkProfileRefused
from rebase_core.schema_common import ResponseModel
from rebase_core.work_profiles.intake import (
    ATTACHMENTS_REFUSED,
    MAX_BYTES,
    WorkProfileService,
)
from rebase_core.work_profiles.schemas import WorkProfileAccepted

router = APIRouter(prefix="/api/hub/work-profiles", tags=["hub"])

# The multipart envelope around the 512 KB file: boundaries and headers.
_ENVELOPE_BYTES = 16 * 1024
_MAX_PARTS = 10
_SCHEME = "bearer "
_TOOL = "work-profile/"
_TOO_BIG = "Il profilo supera i 512 KB."


class Detail(ResponseModel):
    """An error of this route: one sentence."""

    detail: str


_MULTIPART: dict[str, Any] = {
    "required": True,
    "content": {
        "multipart/form-data": {
            "schema": {
                "type": "object",
                "required": ["profile"],
                "properties": {
                    "profile": {
                        "type": "string",
                        "format": "binary",
                        "description": "candidate.json, at most 512 KB",
                    }
                },
            }
        }
    },
}


def _bearer(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith(_SCHEME):
        return authorization[len(_SCHEME) :].strip() or None
    return None


def _tool_version(user_agent: str | None) -> str | None:
    """`work-profile/1.2.3` in the `User-Agent`, if the CLI says so; its payload's
    `toolVersion` wins when it has one."""
    for part in (user_agent or "").split():
        if part.startswith(_TOOL):
            return part[len(_TOOL) :] or None
    return None


async def _body(request: Request) -> bytes:
    """The request's body, read in chunks and refused (413) the moment it passes the cap."""
    limit = MAX_BYTES + _ENVELOPE_BYTES
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, _TOO_BIG)
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, _TOO_BIG)
    return bytes(body)


async def _profile_part(request: Request) -> bytes:
    if not request.headers.get("content-type", "").startswith("multipart/form-data"):
        raise WorkProfileRefused("Il profilo va inviato come multipart, nella parte «profile».")
    buffered = await _body(request)

    async def replay() -> dict[str, Any]:
        return {"type": "http.request", "body": buffered, "more_body": False}

    form = await Request(request.scope, replay).form(max_files=_MAX_PARTS, max_fields=_MAX_PARTS)
    parts = form.multi_items()
    if any(name != "profile" for name, _ in parts):
        raise WorkProfileRefused(ATTACHMENTS_REFUSED)
    if len(parts) != 1:
        raise WorkProfileRefused("Serve una sola parte, «profile», con il file del profilo.")
    value = parts[0][1]
    body = await value.read(MAX_BYTES + 1) if isinstance(value, UploadFile) else value.encode()
    if len(body) > MAX_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, _TOO_BIG)
    return body


@router.post(
    "",
    response_model=WorkProfileAccepted,
    status_code=status.HTTP_201_CREATED,
    openapi_extra={"requestBody": _MULTIPART},
    responses={
        401: {"model": Detail, "description": "No live bearer: unknown, expired or spent"},
        403: {"model": Detail, "description": "The person has no live freelancer card"},
        413: {"model": Detail, "description": "The profile is over 512 KB"},
        422: {"model": Detail, "description": "The profile is refused, with the reason"},
    },
)
async def submit(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    authorization: Annotated[str | None, Header()] = None,
    user_agent: Annotated[str | None, Header()] = None,
) -> WorkProfileAccepted:
    service = WorkProfileService(session, settings)
    token = _bearer(authorization)
    await run_in_threadpool(service.precheck, token)  # 401 before the body is read
    try:
        body = await _profile_part(request)
    except (WorkProfileRefused, HTTPException):
        await run_in_threadpool(service.spend, token)
        raise
    return await run_in_threadpool(service.submit, token, body, _tool_version(user_agent))
