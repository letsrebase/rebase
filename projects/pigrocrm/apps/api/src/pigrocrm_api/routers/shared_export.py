"""What the accountant opens: the door of a share link (REB-1047; spec accountant export § 3).

Unauthenticated by design: the link is the only credential, so everything about it is here and in
`ExportShareDoor`. Under a space's prefix it opens that space's export (`TenantPrefixMiddleware`
resolves it like any other route), at the root the root's.

The token never rides in a URL. It is sent in a POST body, so it reaches no access log, no
browser history and no `Referer`, and the page that carries it reads it from the address once and
takes it out (`stripVerifyToken`, the shape of the CRM's other links by mail). Every answer is
`Cache-Control: no-store` and `Referrer-Policy: no-referrer`.

An export that carries pay is opened with a one-time code too (REB-1048, spec § 3): `/code` mails
a code to the address the link was made for, `/verify` trades the right code for a session (a
random value, shown once), and `/describe` and `/download` present that session in the body.
Nothing about the code is ever in an address.

Throttled twice, under nginx's own `limit_req` zone (`pigro_share`): a bucket per client, and a
backoff on a client that keeps presenting tokens that do not resolve, which with 256 random bits
costs nobody anything and tells a scanner nothing.
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from pigrocrm.core.accountant_export.schemas import SharedExportRead
from pigrocrm.core.accountant_export.service import ZIP_MIME
from pigrocrm.core.accountant_export.sharing import (
    CODE_DIGITS,
    ExportCodeInvalid,
    ExportLinkUnknown,
    ExportShareDoor,
)
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
from pigrocrm_api.sessions import SenderDep

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


class SharedAccess(SharedToken):
    access: Annotated[SafeStr, Field(max_length=TOKEN_MAX_LENGTH)] | None = Field(
        default=None,
        description="The session a right code opened, for a link that asks a code",
    )


class SharedPart(SharedAccess):
    part: int = Field(default=1, ge=1, description="The number of the ZIP, from 1")


class SharedCode(SharedToken):
    code: Annotated[SafeStr, Field(min_length=1, max_length=CODE_DIGITS + 4)] = Field(
        description="The one-time code mailed to the link's recipient"
    )


class CodeSent(BaseModel):
    sent_to: str = Field(description="The address the code went to, with most of it hidden")


class CodeAccepted(BaseModel):
    access: str = Field(
        description=(
            "The session the code opened, to present with `describe` and `download` for the next "
            "half hour. Shown only in this answer"
        )
    )


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
    body: SharedAccess,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
) -> SharedExportRead:
    """What a share link opens: the company and period, who shared it, when it expires and the
    ZIP files with their sizes. Reading it is not an opening. A link with a code lists no file
    until the session a right code opened is given. An unknown, expired or revoked link
    answers a refusal that says which."""
    key = _guarded(request)
    response.headers.update(NO_STORE)
    try:
        return _door(session, storage, settings).describe(body.token, access=body.access)
    except ExportLinkUnknown:
        record_failure(key, free=FREE_UNKNOWN_TOKENS)
        log_unauthorized(request, SCOPE)
        raise


@router.post("/code", response_model=CodeSent)
def send_shared_export_code(
    request: Request,
    response: Response,
    body: SharedToken,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    sender: SenderDep,
) -> CodeSent:
    """Mails a one-time code to the address the link was made for: six digits, good for a
    quarter of an hour and five tries. One a minute and five an hour per link. The answer
    says where it went, never the code."""
    key = _guarded(request)
    response.headers.update(NO_STORE)
    if sender is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "L'installazione non può mandare posta, quindi non può mandare il codice.",
        )
    try:
        return CodeSent(sent_to=_door(session, storage, settings).send_code(body.token, sender))
    except ExportLinkUnknown:
        record_failure(key, free=FREE_UNKNOWN_TOKENS)
        log_unauthorized(request, SCOPE)
        raise


@router.post("/verify", response_model=CodeAccepted)
def verify_shared_export_code(
    request: Request,
    response: Response,
    body: SharedCode,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
) -> CodeAccepted:
    """Trades the one-time code for a session that opens the files for half an hour. A wrong,
    expired or spent code is one refusal, and a client that keeps guessing is held back."""
    key = _guarded(request)
    response.headers.update(NO_STORE)
    try:
        return CodeAccepted(
            access=_door(session, storage, settings).verify_code(body.token, body.code)
        )
    except (ExportLinkUnknown, ExportCodeInvalid):
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
        opened = _door(session, storage, settings).open(
            body.token, body.part, client_key(request), access=body.access
        )
    except ExportLinkUnknown:
        record_failure(key, free=FREE_UNKNOWN_TOKENS)
        log_unauthorized(request, SCOPE)
        raise
    return attachment_response(opened.data, opened.content_type, opened.filename, headers=NO_STORE)
