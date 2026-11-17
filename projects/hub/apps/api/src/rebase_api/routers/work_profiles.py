"""`POST /api/hub/work-profiles`: the intake of a work profile (REB-1124, spec 2026-11-17 § 3).

`Authorization: Bearer <access_token>` of the CLI's sign-in (`rebase_core.cli_auth`), multipart
with the one part `profile` (`candidate.json`, at most 512 KB). The token is checked first: the
body is read only for a bearer that names a live authorization, so a stranger's upload costs
nothing. Any other part is a 422 with the sentence of the spec.
"""

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from rebase_api.deps import SessionDep, SettingsDep
from rebase_core.errors import WorkProfileRefused
from rebase_core.work_profiles.intake import (
    ATTACHMENTS_REFUSED,
    MAX_BYTES,
    WorkProfileService,
)
from rebase_core.work_profiles.schemas import WorkProfileAccepted

router = APIRouter(prefix="/api/hub/work-profiles", tags=["hub"])

# The multipart envelope around the 512 KB file: boundaries and headers.
_ENVELOPE_BYTES = 16 * 1024
_SCHEME = "bearer "
_TOOL = "work-profile/"


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


@router.post("", response_model=WorkProfileAccepted, status_code=status.HTTP_201_CREATED)
async def submit(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    authorization: Annotated[str | None, Header()] = None,
    user_agent: Annotated[str | None, Header()] = None,
) -> WorkProfileAccepted:
    service = WorkProfileService(session, settings)
    token = _bearer(authorization)
    await run_in_threadpool(service.live_token, token)  # 401 before the body is read
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_BYTES + _ENVELOPE_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Il profilo supera i 512 KB.")
    if not request.headers.get("content-type", "").startswith("multipart/form-data"):
        raise WorkProfileRefused("Il profilo va inviato come multipart, nella parte «profile».")
    form = await request.form(max_files=2, max_fields=2)
    parts = form.multi_items()
    if any(name != "profile" for name, _ in parts):
        raise WorkProfileRefused(ATTACHMENTS_REFUSED)
    if len(parts) != 1:
        raise WorkProfileRefused("Serve una sola parte, «profile», con il file del profilo.")
    value = parts[0][1]
    body = await value.read(MAX_BYTES + 1) if isinstance(value, UploadFile) else value.encode()
    return await run_in_threadpool(service.submit, token, body, _tool_version(user_agent))
