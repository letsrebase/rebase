"""What the accountant opens: the door of a share link (REB-1047; spec accountant export § 3).

Unauthenticated by design: the link is the only credential, so everything about it is here and in
`ExportShareDoor`. Under a space's prefix it opens that space's export (`TenantPrefixMiddleware`
resolves it like any other route), at the root the root's.

The token never rides in a URL. It is sent in a POST body, so it reaches no access log, no
browser history and no `Referer`, and the page that carries it reads it from the address once and
takes it out (`stripVerifyToken`, the shape of the CRM's other links by mail). Every answer is
`Cache-Control: no-store` and `Referrer-Policy: no-referrer`.

Throttled twice, under nginx's own `limit_req` zone (`pigro_share`): a bucket per client, and a
backoff on a client that keeps presenting tokens that do not resolve, which with 256 random bits
costs nobody anything and tells a scanner nothing.
"""

from typing import Annotated

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from pigrocrm.core.accountant_export.schemas import SharedExportRead
from pigrocrm.core.accountant_export.service import ZIP_MIME
from pigrocrm.core.accountant_export.sharing import ExportLinkUnknown, ExportShareDoor
from pigrocrm.core.validation import SafeStr
from pigrocrm_api.deps import SessionDep, SettingsDep, StorageDep
from pigrocrm_api.downloads import attachment_response
from pigrocrm_api.errors import EXPORT_LINK_GONE_RESPONSE, PROBLEM_RESPONSES
from pigrocrm_api.ratelimit import (
    TOO_MANY_REQUESTS_RESPONSE,
    client_key,
    log_unauthorized,
    record_failure,
    refuse_if_locked,
    spend_one,
)

router = APIRouter(
    prefix="/api/accountant-export/shared",
    tags=["accountant-export"],
    responses={
        **PROBLEM_RESPONSES,
        410: EXPORT_LINK_GONE_RESPONSE,
        429: TOO_MANY_REQUESTS_RESPONSE,
    },
)

SCOPE = "export_share"
REQUESTS_PER_MINUTE = 30
# Tokens that do not resolve, per client, before the backoff starts: a person who mistyped one
# by hand is not locked out, a scanner is.
FREE_UNKNOWN_TOKENS = 10
# A token is 43 characters; anything much longer is not ours.
TOKEN_MAX_LENGTH = 128
NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


class SharedToken(BaseModel):
    token: Annotated[SafeStr, Field(max_length=TOKEN_MAX_LENGTH)]


class SharedPart(SharedToken):
    part: int = Field(default=1, ge=1, description="The number of the ZIP, from 1")


def _door(session: SessionDep, storage: StorageDep, settings: SettingsDep) -> ExportShareDoor:
    return ExportShareDoor(session, storage, settings)


def _guarded(request: Request) -> str:
    key = f"{SCOPE}:{client_key(request)}"
    spend_one(request, scope=SCOPE, per_minute=REQUESTS_PER_MINUTE)
    refuse_if_locked(request, key)
    return key


@router.post("/describe", response_model=SharedExportRead)
def describe_shared_export(
    request: Request,
    response: Response,
    body: SharedToken,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
) -> SharedExportRead:
    """What a share link opens: the company and period, who shared it, when it expires and the
    ZIP files with their sizes. Reading it is not an opening. An unknown, expired or revoked
    link answers a refusal that says which."""
    key = _guarded(request)
    response.headers.update(NO_STORE)
    try:
        return _door(session, storage, settings).describe(body.token)
    except ExportLinkUnknown:
        record_failure(key, free=FREE_UNKNOWN_TOKENS)
        log_unauthorized(request, SCOPE)
        raise


@router.post(
    "/download",
    response_class=Response,
    responses={200: {"content": {ZIP_MIME: {}}, "description": "One ZIP of the export"}},
)
def download_shared_export(
    request: Request,
    body: SharedPart,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
) -> Response:
    """One ZIP of the export a share link opens. Each call counts as an opening of the link and
    writes an audit row with the moment and the client address; the link's owner sees both."""
    key = _guarded(request)
    try:
        opened = _door(session, storage, settings).open(body.token, body.part, client_key(request))
    except ExportLinkUnknown:
        record_failure(key, free=FREE_UNKNOWN_TOKENS)
        log_unauthorized(request, SCOPE)
        raise
    return attachment_response(opened.data, opened.content_type, opened.filename, headers=NO_STORE)
