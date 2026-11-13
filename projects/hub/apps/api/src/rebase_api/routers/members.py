"""The member area's API: a link in, a cookie out, one row behind it.

`POST /auth/link` answers 202 whether the address applied or not, and the mail goes out
in a background task after the response, so neither the status nor the timing nor a
provider failure says whether an address is known. `POST /auth/enter` spends the token
and sets `rebase_user`, for anyone with a `users` row, member or admin alike
(REB-278): the identity resolution behind both lives in `rebase_core.users`, not here.
Everything under `/me` reads the row from the session and never from the URL: there is
no `/me/{id}`. The one id the URL does carry is a company request's: `PATCH
/me/company/{id}` (REB-602) edits one of the caller's own requests, and somebody
else's, a soft-deleted one and an id that does not exist are the same 404.
`PATCH /me/company` (REB-314) is the id-less form of it, for the caller's newest
request. Neither reaches `status`, `note` or the company's own identity. `POST
/me/company` (REB-381) is a different door onto the same table: not an edit, a
brand-new `Company` request, the company's name carried forward and everything else
asked fresh -- a 404 for a referente with nothing to add to yet, the same as the
`PATCH`.

`GET /me/contracts` (REB-392) is the same discipline for the contracts: the caller's
own, a 404 for anybody else's.

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
from uuid import UUID

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

from rebase_api.deps import (
    CardLlmDep,
    CrmFilingDep,
    FreelancerDoorDep,
    MeDep,
    SenderDep,
    SessionDep,
    SessionOpenerDep,
    SettingsDep,
    clear_member_cookie,
    session_token,
    set_member_cookie,
)
from rebase_api.door_errors import door_unavailable
from rebase_api.downloads import cv_response, pdf_response, perk_response
from rebase_api.ratelimit import spend_one
from rebase_core.card_writer import write_after_response
from rebase_core.company_schemas import CompanyFields, CompanyUpdate
from rebase_core.contract_schemas import MemberContracts
from rebase_core.freelancer_schemas import MemberUpdate
from rebase_core.mail import send_after_response
from rebase_core.member_contracts import MemberContractService
from rebase_core.member_schemas import (
    ContactPointsUpdate,
    EnterRequest,
    LinkRequest,
    MemberLookup,
    MemberLookupRequest,
    MeRead,
)
from rebase_core.members import MemberService
from rebase_core.models import CV_MAX_BYTES
from rebase_core.perks import GUIDE_FILENAME, PerkService, guide_bytes
from rebase_core.pigro_door import PigroUnavailable
from rebase_core.referral_schemas import MemberReferral
from rebase_core.referrals import ReferralService
from rebase_core.schema_common import Ack
from rebase_core.users import VIA_LOGIN, UserService

router = APIRouter(prefix="/api/hub", tags=["hub-member"])

_log = logging.getLogger(__name__)


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
    users = UserService(session, settings)
    mail = users.request_link(payload.email, utm=payload.utm, via=VIA_LOGIN)
    if mail is not None:
        background.add_task(send_after_response, sender, mail, "the magic link mail")
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
    set_member_cookie(response, settings, raw)
    return MemberService(session).me_read(user.id)


@router.get("/me", response_model=MeRead)
def me(me: MeDep) -> MeRead:
    """Whoever the cookie resolves to; `talent_cloud` (REB-518) says whether a grant of
    theirs is live, which puts «Talent cloud» in the area's nav."""
    return me


@router.patch("/me", response_model=MeRead)
def update_me(me: MeDep, session: SessionDep, payload: MemberUpdate) -> MeRead:
    service = MemberService(session)
    freelancer = service.require_card(me.id)
    service.update(freelancer.id, payload)
    return service.me_read(me.id)


@router.put("/me/contact-points", response_model=MeRead)
def replace_my_contact_points(
    me: MeDep, session: SessionDep, payload: ContactPointsUpdate
) -> MeRead:
    """The caller's own list of extra contact points, replaced whole (REB-943): a phone,
    a second address or a profile with its label and note, whether they are a freelancer,
    a company's referente or both. The address they sign in with is read-only and not in
    it. An address that is somebody else's is a 422 that names it."""
    return MemberService(session).update_contact_points(me.id, payload.contact_points)


@router.patch("/me/company", response_model=MeRead)
def update_my_company(me: MeDep, session: SessionDep, payload: CompanyUpdate) -> MeRead:
    """The caller's newest request, the id-less form of the route below."""
    return MemberService(session).update_company(me.id, payload)


@router.patch("/me/company/{company_id}", response_model=MeRead)
def update_my_company_request(
    me: MeDep, session: SessionDep, company_id: UUID, payload: CompanyUpdate
) -> MeRead:
    return MemberService(session).update_company(me.id, payload, company_id)


@router.post("/me/company", response_model=MeRead, status_code=status.HTTP_201_CREATED)
def create_my_company_request(
    me: MeDep,
    session: SessionDep,
    background: BackgroundTasks,
    crm_filing: CrmFilingDep,
    payload: CompanyFields,
) -> MeRead:
    read, company_id = MemberService(session).create_additional_request(me.id, payload)
    # Written `to_link` with the row; the first attempt at the CRM runs after the answer.
    background.add_task(crm_filing, company_id)
    return read


@router.put("/me/cv", response_model=MeRead)
def replace_my_cv(
    me: MeDep,
    request: Request,
    session: SessionDep,
    background: BackgroundTasks,
    llm: CardLlmDep,
    open_session: SessionOpenerDep,
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
    # A new CV is a new anonymous card (REB-510), written after the answer in a session
    # of its own, as the wizard's first one is.
    background.add_task(write_after_response, open_session, llm, freelancer.id)
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


@router.get("/me/contracts", response_model=MemberContracts)
def my_contracts(me: MeDep, session: SessionDep, door: FreelancerDoorDep) -> MemberContracts:
    """«Contratti» (REB-392): the caller's own framework agreement and letters, read from
    the session and never from the URL. A person with no card is a 404 named «scheda»,
    as `/me/cv` is. The documents are the CRM's (REB-1107), read through its door by the
    caller's freelancer id: 502 when the CRM does not answer and 503 when this environment
    cannot reach it, and the page shows its error line rather than an empty list."""
    try:
        return MemberContractService(session, door).for_user(me.id)
    except PigroUnavailable as exc:
        raise door_unavailable(exc) from exc


@router.get("/me/contracts/{document_id}/pdf")
def my_signed_contract(
    me: MeDep, session: SessionDep, door: FreelancerDoorDep, document_id: UUID
) -> Response:
    """The signed copy of one of the caller's own documents, served by the CRM through its
    door. Someone else's document, and one not signed yet, are the same 404 as a document
    that does not exist: a 403 would say it exists. 502 and 503 as `/me/contracts`."""
    try:
        pdf = MemberContractService(session, door).signed_pdf(me.id, document_id)
    except PigroUnavailable as exc:
        raise door_unavailable(exc) from exc
    return pdf_response(pdf.filename, pdf.content)


@router.get("/me/referral", response_model=MemberReferral)
def my_referral(me: MeDep, session: SessionDep) -> MemberReferral:
    """The member's own link, issued the first time this route is asked for one
    (P-REB-44): the code, who has signed up under it so far, and the two rates in
    force now (REB-610), read live on every call. Never a euro figure of a real
    engagement -- rebase's own margin is an admin-only number the member area never
    shows, the same isolation `daily_budget` already keeps."""
    referral = ReferralService(session).for_user(me.id)
    session.commit()  # `code_for` only flushes the code it issues
    return referral


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
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    presented = authorization.removeprefix("Bearer ").strip() if authorization else ""
    # Bytes, not str: Starlette decodes headers as latin-1 and `compare_digest` refuses a
    # `str` with a non-ASCII character, which would turn a stray byte into a 500.
    if not presented or not secrets.compare_digest(
        presented.encode("utf-8"), settings.pigro_registry_token.encode("utf-8")
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_token")
    return MemberService(session).lookup(payload.email)


@router.post("/me/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request, response: Response, session: SessionDep, settings: SettingsDep
) -> None:
    users = UserService(session, settings)
    raw = session_token(request)
    if raw:
        users.close_session(raw)
    clear_member_cookie(response, settings)
