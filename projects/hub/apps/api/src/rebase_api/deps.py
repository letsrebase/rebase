"""One engine per process, one session per request."""

import threading
from collections.abc import Callable, Iterator
from contextlib import closing
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from rebase_core.admin_tokens import AdminRead
from rebase_core.analytics import Tracker, tracker_from_settings
from rebase_core.anonymous_cv.render import CvRenderer, TypstCvRenderer
from rebase_core.audit import utcnow
from rebase_core.campaigns.sender import CampaignSender, campaign_sender_from_settings
from rebase_core.card_batch import CardBatches
from rebase_core.cloud import CLOUD_CLOSED, CloudCaller, CloudTalentService
from rebase_core.config import Settings, get_settings
from rebase_core.crm_link import CrmLeadService, link_after_response
from rebase_core.db import SessionOpener, create_engine_from_settings, session_factory
from rebase_core.http import HttpCall, urllib_call, urllib_engagements_call
from rebase_core.llm import (
    AiCall,
    LlmCall,
    ModelFactory,
    build_model,
    call_for_task,
)
from rebase_core.llm_batch import batch_for_task
from rebase_core.mail import EmailSender, sender_from_settings
from rebase_core.member_schemas import MeRead
from rebase_core.members import MemberService
from rebase_core.pigro_door import FreelancerDoor, freelancer_door
from rebase_core.team_builder import TeamBuilder
from rebase_core.team_slots import ProposalSlots, proposal_slots
from rebase_core.users import UserService

MEMBER_COOKIE = "rebase_user"


def session_token(request: Request) -> str | None:
    """The raw session token the request carries, if any."""
    return request.cookies.get(MEMBER_COOKIE) or None


def set_member_cookie(response: Response, settings: Settings, raw: str) -> None:
    response.set_cookie(
        MEMBER_COOKIE,
        raw,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.member_session_days * 86400,
        path="/",
    )


def clear_member_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        MEMBER_COOKIE,
        path="/",
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )


_engine: Engine | None = None
_factory: sessionmaker[Session] | None = None
_lock = threading.Lock()


def _get_session_factory() -> sessionmaker[Session]:
    global _engine, _factory
    if _factory is None:
        with _lock:
            if _factory is None:
                _engine = create_engine_from_settings(get_settings())
                _factory = session_factory(_engine)
    return _factory


def get_session() -> Iterator[Session]:
    session = _get_session_factory()()
    try:
        yield session
    finally:
        session.close()


SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_admin(request: Request, session: SessionDep, settings: SettingsDep) -> AdminRead:
    """A signed-in person whose role is admin: a signed-in non-admin is a real identity
    hitting the wrong door (403), nobody signed in at all is 401 (design record
    2026-09-17 §4). The password login and its `orbiters_admin` cookie are gone
    (REB-281): every admin resolves through the one cookie, `rebase_user`, like everyone
    else."""
    users = UserService(session, settings)
    raw = session_token(request)
    me = users.resolve(raw) if raw else None
    if me is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Autenticazione richiesta")
    if me.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Serve il ruolo di amministratore")
    return AdminRead.model_validate(me)


AdminDep = Annotated[AdminRead, Depends(get_admin)]


def get_me(request: Request, session: SessionDep, settings: SettingsDep) -> MeRead:
    """Whoever the cookie resolves to, member or admin: the one dependency every
    signed-in route may depend on."""
    users = UserService(session, settings)
    raw = session_token(request)
    user = users.resolve(raw) if raw else None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Autenticazione richiesta")
    return MemberService(session).me_read(user.id)


MeDep = Annotated[MeRead, Depends(get_me)]


def get_cloud_caller(me: MeDep, session: SessionDep) -> CloudCaller:
    """A signed-in person with a live talent cloud grant (REB-519, spec § 4.2), with the
    company of their newest one, which a cloud proposal and request are for. Nobody
    signed in is `MeDep`'s 401; signed in with no live grant, an admin included, is a
    real identity at the wrong door: 403 with the sentence the page shows."""
    caller = CloudTalentService(session).caller(me.id)
    if caller is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, CLOUD_CLOSED)
    return caller


CloudDep = Annotated[CloudCaller, Depends(get_cloud_caller)]


def get_sender(settings: SettingsDep) -> EmailSender | None:
    """`None` without a key: the route answers 503 and nothing pretends to send."""
    return sender_from_settings(settings)


SenderDep = Annotated[EmailSender | None, Depends(get_sender)]


def get_campaign_sender(settings: SettingsDep) -> CampaignSender | None:
    """Campaigns' own Resend seam (P-REB-41): the member area's plus the id, the tags,
    the headers and the idempotency key. `None` without a key."""
    return campaign_sender_from_settings(settings)


CampaignSenderDep = Annotated[CampaignSender | None, Depends(get_campaign_sender)]


def get_proposal_slots(settings: SettingsDep) -> ProposalSlots:
    """The process's proposal gate (`rebase_core.team_slots`, spec § 5): the slots, the
    clients' daily shares and the refusal writers, one for the process."""
    return proposal_slots(settings)


ProposalSlotsDep = Annotated[ProposalSlots, Depends(get_proposal_slots)]


def get_http_call() -> HttpCall:
    """The one HTTP seam, as a dependency so a test can hand a fake where production
    hands `urllib_call`: the registry of PigroCRM's spaces is read through it (ORB-142)."""
    return urllib_call


HttpCallDep = Annotated[HttpCall, Depends(get_http_call)]


def _engagements_call(http: HttpCall) -> HttpCall:
    """The seam the CRM doors read through: the request's own
    `HttpCallDep`, so a test's override of `get_http_call` reaches it too, except that
    production's ten-second `urllib_call` becomes `urllib_engagements_call`, whose 90
    seconds leave room for the first link of a freelancer, which opens their space."""
    return urllib_engagements_call if http is urllib_call else http


def get_freelancer_door(settings: SettingsDep, http: HttpCallDep) -> FreelancerDoor:
    """The CRM's reads of a freelancer (REB-1107): the member's contracts, the anonymous CV's
    redaction list, the merge check and availability. The request's own `HttpCallDep`, so a
    test's override of `get_http_call` reaches it too."""
    return freelancer_door(settings, http)


FreelancerDoorDep = Annotated[FreelancerDoor, Depends(get_freelancer_door)]


def get_tracker(settings: SettingsDep) -> Tracker | None:
    """The server half of the wizard's analytics (REB-215): `None` without a key, so a
    route that has one schedules the event and a route that has none does nothing."""
    return tracker_from_settings(settings)


TrackerDep = Annotated[Tracker | None, Depends(get_tracker)]


def get_session_opener() -> SessionOpener:
    """A session for work that runs after the response (REB-387's webhook): a background
    task must not borrow the request's session, which its dependency closes."""
    factory = _get_session_factory()
    return lambda: closing(factory())


SessionOpenerDep = Annotated[SessionOpener, Depends(get_session_opener)]


def get_crm_llm(settings: SettingsDep, opener: SessionOpenerDep) -> Callable[[], LlmCall | None]:
    """What answers the seam of the `crm_lead` task (REB-867) when a request's draft is
    needed: a callable, so «Modelli AI» is read at that moment and not when the request
    arrived. A dependency, so a test hands a `RecordingCall`."""
    return lambda: call_for_task(opener, settings, "crm_lead")


CrmLlmDep = Annotated[Callable[[], LlmCall | None], Depends(get_crm_llm)]


def get_crm_leads(
    session: SessionDep, settings: SettingsDep, http: HttpCallDep, llm: CrmLlmDep
) -> CrmLeadService:
    """The filing of a company request in rebase's CRM (REB-868): «Riprova» and «Collega a
    un altro deal». It reads through the engagements seam, whose 90 seconds leave room for
    a CRM that is slow to answer."""
    return CrmLeadService(session, settings, _engagements_call(http), llm)


CrmLeadsDep = Annotated[CrmLeadService, Depends(get_crm_leads)]

CrmFiling = Callable[[UUID], None]


def get_crm_filing(
    settings: SettingsDep, http: HttpCallDep, opener: SessionOpenerDep, llm: CrmLlmDep
) -> CrmFiling:
    """The first attempt at filing a new request in the CRM, for `BackgroundTasks`: runs
    after the response in a session of its own and never raises (the sweep recovers)."""
    engagements = _engagements_call(http)
    return lambda company_id: link_after_response(opener, settings, engagements, llm, company_id)


CrmFilingDep = Annotated[CrmFiling, Depends(get_crm_filing)]


def get_card_llm(settings: SettingsDep, opener: SessionOpenerDep) -> LlmCall | None:
    """The model seam for the anonymous card (REB-510, REB-831), or `None` when no model
    is configured for the `card` task in «Modelli AI»: the card is then not written. A
    dependency, so a test hands a `RecordingCall` the way it hands `RecordingSender` for
    the mail."""
    return call_for_task(opener, settings, "card")


def get_team_llm(settings: SettingsDep, opener: SessionOpenerDep) -> LlmCall | None:
    """The same seam for the team proposal (REB-508, REB-831): `None` when no model is
    configured for the `team_proposal` task, and the builder answers as with the switch
    off."""
    return call_for_task(opener, settings, "team_proposal")


def get_card_batches(settings: SettingsDep, opener: SessionOpenerDep) -> CardBatches | None:
    """The Message Batches path of an admin's regeneration (REB-853), or `None` when the
    `card` task's provider has none (only Anthropic does): the cards are then written with
    the workers. A dependency, so a test hands a `RecordingBatch`."""
    call = batch_for_task(opener, settings, "card")
    return None if call is None else CardBatches(call, settings.cards_batch_threshold)


CardLlmDep = Annotated[LlmCall | None, Depends(get_card_llm)]
CardBatchesDep = Annotated[CardBatches | None, Depends(get_card_batches)]
TeamLlmDep = Annotated[LlmCall | None, Depends(get_team_llm)]


def get_cv_llm(settings: SettingsDep, opener: SessionOpenerDep) -> LlmCall | None:
    """The model seam for the anonymous CV (REB-862), or `None` when no model is configured
    for the `anonymous_cv` task: a kept body is then still served, and only a CV that needs
    writing is refused. The writer names the talent on each request, so every usage row of
    the call names the person it was about."""
    return call_for_task(opener, settings, "anonymous_cv")


def get_cv_renderer() -> CvRenderer:
    """What prints the anonymous CV: Typst. A dependency, so the API's tests hand
    `FakeCvRenderer` and need no binary."""
    return TypstCvRenderer()


CvLlmDep = Annotated[LlmCall | None, Depends(get_cv_llm)]
CvRendererDep = Annotated[CvRenderer, Depends(get_cv_renderer)]


def get_model_factory() -> ModelFactory:
    """What builds a provider's model for a call: `build_model`. A dependency, so a test
    hands a stub model and still runs the real `AiCall` around it."""
    return build_model


def get_test_call(
    settings: SettingsDep,
    opener: SessionOpenerDep,
    model_factory: Annotated[ModelFactory, Depends(get_model_factory)],
) -> Callable[[str], LlmCall]:
    """«Prova» on «Modelli AI» (REB-833): the seam for one task, recording its usage as
    `purpose="test"`."""
    return lambda task: AiCall(opener, settings, task, purpose="test", model_factory=model_factory)


TestCallDep = Annotated[Callable[[str], LlmCall], Depends(get_test_call)]


def get_clock() -> Callable[[], datetime]:
    """The team builder's «now» (REB-581): the clock a proposal is stamped with and the
    daily cap counts from (`team_caps.require_daily_room`), one for both. A dependency so
    a test pins one instant for its rows and its request, the way it hands a
    `RecordingCall` for Claude, instead of racing midnight in Rome on the wall clock."""
    return utcnow


ClockDep = Annotated[Callable[[], datetime], Depends(get_clock)]


def get_team_builder(
    session: SessionDep,
    llm: TeamLlmDep,
    settings: SettingsDep,
    tracker: TrackerDep,
    clock: ClockDep,
) -> TeamBuilder:
    """The engine (REB-511) for this request: the Claude seam, `None` without a key, the
    tracker that counts the proposal and the clock that stamps it."""
    return TeamBuilder(session, llm, settings, tracker=tracker, now=clock)


TeamBuilderDep = Annotated[TeamBuilder, Depends(get_team_builder)]
