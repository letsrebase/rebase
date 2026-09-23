"""The member area's API: a link in, a cookie out, one row behind it.

`POST /auth/link` answers 202 whether the address applied or not, and the mail goes out
in a background task after the response, so neither the status nor the timing nor a
provider failure says whether an address is known. `POST /auth/enter` spends the token
and sets `orbiters_user`, for anyone with a `users` row, member or admin alike
(REB-278): the identity resolution behind both lives in `rebase_core.users`, not here.
Everything under `/me` reads the row from the session and never from the URL: there is
no `/me/{id}`. `PATCH /me/company` (REB-314) is the same discipline for the referente's
company side: it reaches only their most recent `Company` request, never `stato`,
`note` or the company's own identity. `POST /me/company` (REB-381) is a different
door onto the same row: not an edit, a brand-new `Company` request, the company's
name carried forward and everything else asked fresh -- a 404 for a referente with
nothing to add to yet, the same as the `PATCH`.

`POST /auth/link`, `POST /auth/enter` and `PUT /me/cv` all spend from the public rate
limit: the first two because they are unauthenticated by design, `PUT /me/cv` because
FastAPI reads its multipart body while resolving parameters, before `MeDep` gets a
chance to reject an anonymous caller with a 401.

`POST /members/lookup` is the one route here for another product rather than for a
person: PigroCRM's signup asks whether an address belongs to a member (ORB-173), under
the token the two hosts already share for the registry of spaces (ORB-142). A POST with
the address in the body, so no access log on either host writes it.
"""

import logging
import secrets
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)

from rebase_api.deps import MEMBER_COOKIE, MeDep, SenderDep, SessionDep, SettingsDep
from rebase_api.downloads import cv_response, perk_response
from rebase_api.ratelimit import spend_one
from rebase_core.mail import EmailSender, Mail
from rebase_core.members import MemberService
from rebase_core.models import CV_MAX_BYTES
from rebase_core.perks import GUIDE_FILENAME, PerkService, guide_bytes
from rebase_core.schemas import (
    Ack,
    CompanyFields,
    CompanyUpdate,
    EnterRequest,
    LinkRequest,
    MemberLookup,
    MemberLookupRequest,
    MemberUpdate,
    MeRead,
)
from rebase_core.users import UserService

router = APIRouter(prefix="/api/hub", tags=["hub-member"])

_log = logging.getLogger(__name__)


def _send(sender: EmailSender, mail: Mail) -> None:
    """Runs after the response. A refusal is logged without the address or the key: the
    operator needs to know the provider said no, not to whom."""
    if not sender.send(mail):
        _log.warning("the magic link mail was refused by the provider")


@router.post("/auth/link", response_model=Ack, status_code=status.HTTP_202_ACCEPTED)
def request_link(
    payload: LinkRequest,
    request: Request,
    background: BackgroundTasks,
    session: SessionDep,
    settings: SettingsDep,
    sender: SenderDep,
) -> Ack:
    spend_one(request)
    if sender is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "L'accesso via email non è ancora attivo. Riprova più avanti.",
        )
    mail = UserService(session, settings).request_link(payload.email)
    if mail is not None:
        background.add_task(_send, sender, mail)
    return Ack()


@router.post("/auth/enter", response_model=MeRead)
def enter(
    payload: EnterRequest,
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
) -> MeRead:
    spend_one(request)
    outcome = UserService(session, settings).enter(payload.token)
    if outcome is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Link non valido o scaduto. Chiedine un altro."
        )
    user, raw = outcome
    response.set_cookie(
        MEMBER_COOKIE,
        raw,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.member_session_days * 86400,
        path="/",
    )
    return MemberService(session).me_read(user.id)


@router.get("/me", response_model=MeRead)
def me(me: MeDep) -> MeRead:
    return me


@router.patch("/me", response_model=MeRead)
def update_me(me: MeDep, session: SessionDep, payload: MemberUpdate) -> MeRead:
    service = MemberService(session)
    freelancer = service.require_card(me.id)
    service.update(freelancer.id, payload)
    return service.me_read(me.id)


@router.patch("/me/company", response_model=MeRead)
def update_my_company(me: MeDep, session: SessionDep, payload: CompanyUpdate) -> MeRead:
    return MemberService(session).update_company(me.id, payload)


@router.post("/me/company", response_model=MeRead, status_code=status.HTTP_201_CREATED)
def create_my_company_request(me: MeDep, session: SessionDep, payload: CompanyFields) -> MeRead:
    return MemberService(session).create_additional_request(me.id, payload)


@router.put("/me/cv", response_model=MeRead)
def replace_my_cv(
    me: MeDep,
    request: Request,
    session: SessionDep,
    cv: Annotated[UploadFile, File()],
) -> MeRead:
    # The multipart body is already parsed by the time any dependency runs, so an
    # anonymous caller has made the server read it regardless of the 401 that follows;
    # charge it to the public bucket and never materialise more than the limit `check_cv`
    # enforces anyway.
    spend_one(request)
    content = cv.file.read(CV_MAX_BYTES + 1)
    service = MemberService(session)
    freelancer = service.require_card(me.id)
    service.replace_cv(freelancer.id, content, cv.filename or "", cv.content_type or "")
    return service.me_read(me.id)


@router.get("/me/cv")
def my_cv(me: MeDep, session: SessionDep) -> Response:
    service = MemberService(session)
    freelancer = service.require_card(me.id)
    cv = service.cv(freelancer.id)
    return cv_response(cv)


@router.get("/me/guide")
def my_guide(me: MeDep, session: SessionDep) -> Response:
    """The guide, to anyone signed in and to nobody else.

    `MeDep` is the whole access rule: an anonymous caller gets the same 401 as `/me`
    rather than a redirect or a teaser. The file is `rebase_core`'s own package data.
    Since ORB-156 the download is written down first, who and when, for the admin
    area's counter.
    """
    PerkService(session).record_guide_download(me.id)
    return perk_response(guide_bytes(), GUIDE_FILENAME)


@router.post("/members/lookup", response_model=MemberLookup)
def lookup_member(
    payload: MemberLookupRequest,
    session: SessionDep,
    settings: SettingsDep,
    authorization: Annotated[str | None, Header()] = None,
) -> MemberLookup:
    """Whether an address belongs to a freelancer in the community, and their names, for
    the one caller that holds `REBASE_PIGRO_REGISTRY_TOKEN`: PigroCRM's signup, which
    greets a member by name instead of asking for it (ORB-173). The same shape as the
    CRM's `GET /api/tenants/` in the other direction (ORB-142): without the token
    configured the route does not exist (404), so nothing says there is a door; with it,
    a missing or wrong bearer is a 401. An unknown address is `membro: false`, never an
    error: the hub says who is a member, not who is at the keyboard. A POST so the address
    travels in the body and not in a URL the access log would keep."""
    if not settings.pigro_registry_token:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    presented = authorization.removeprefix("Bearer ").strip() if authorization else ""
    # Bytes, not str: Starlette decodes headers as latin-1 and `compare_digest` refuses a
    # `str` with a non-ASCII character, which would turn a stray byte into a 500.
    if not presented or not secrets.compare_digest(
        presented.encode("utf-8"), settings.pigro_registry_token.encode("utf-8")
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token non valido")
    return MemberService(session).lookup(payload.email)


@router.post("/me/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request, response: Response, session: SessionDep, settings: SettingsDep
) -> None:
    UserService(session, settings).close_session(request.cookies.get(MEMBER_COOKIE))
    response.delete_cookie(MEMBER_COOKIE, path="/")
