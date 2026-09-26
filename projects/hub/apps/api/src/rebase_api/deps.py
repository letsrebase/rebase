"""One engine per process, one session per request."""

import threading
from collections.abc import Iterator
from contextlib import closing
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from rebase_core.admin_tokens import AdminRead
from rebase_core.analytics import Tracker, tracker_from_settings
from rebase_core.campaigns.sender import CampaignSender, campaign_sender_from_settings
from rebase_core.cloud import CLOUD_CLOSED, CloudCaller, CloudTalentService
from rebase_core.config import Settings, get_settings
from rebase_core.contracts.render import ContractRenderer, Renderer
from rebase_core.db import SessionOpener, create_engine_from_settings, session_factory
from rebase_core.documenso import DocumensoClient, client_from_settings
from rebase_core.http import HttpCall, urllib_call
from rebase_core.llm import LlmCall, call_from_settings
from rebase_core.mail import EmailSender, sender_from_settings
from rebase_core.members import MemberService
from rebase_core.schemas import MeRead
from rebase_core.signing import SigningFactory, signing_from_settings
from rebase_core.team_builder import TeamBuilder
from rebase_core.users import UserService

MEMBER_COOKIE = "orbiters_user"

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
    (REB-281): every admin resolves through the one cookie, `orbiters_user`, like
    everyone else."""
    users = UserService(session, settings)
    me = users.resolve_admin(request.cookies.get(MEMBER_COOKIE))
    if me is not None:
        return AdminRead.model_validate(me)
    if users.resolve(request.cookies.get(MEMBER_COOKIE)) is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Serve il ruolo di amministratore")
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Autenticazione richiesta")


AdminDep = Annotated[AdminRead, Depends(get_admin)]


def get_me(request: Request, session: SessionDep, settings: SettingsDep) -> MeRead:
    """Whoever the cookie resolves to, member or admin: the one dependency every
    signed-in route may depend on."""
    user = UserService(session, settings).resolve(request.cookies.get(MEMBER_COOKIE))
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


def get_llm(settings: SettingsDep) -> LlmCall | None:
    """The Claude seam (REB-508), or `None` without a key: the anonymous card is then
    not written (REB-510). A dependency, so a test hands a `RecordingCall` the way it
    hands `RecordingSender` for the mail."""
    return call_from_settings(settings)


LlmDep = Annotated[LlmCall | None, Depends(get_llm)]

# The proposals running now in this process (spec § 5): one semaphore for the process,
# sized by `REBASE_TEAM_BUILDER_CONCURRENCY` when the first proposal arrives. A proposal
# holds a worker thread for the seconds Claude takes, on the pool the member area and
# the webhooks share, so the one past the cap is refused at once, never queued.
_proposal_slots: tuple[int, threading.BoundedSemaphore] | None = None
_proposal_slots_lock = threading.Lock()


def get_proposal_slots(settings: SettingsDep) -> threading.BoundedSemaphore:
    """The process's proposal slots, acquired with `blocking=False` and released in a
    `finally`. Keyed by the size, the way `analytics.build_client` keys its client: the
    setting never changes in a running process, and a test that sets another size gets
    a semaphore of that size; a slot held on the old one is released into the old one."""
    global _proposal_slots
    size = settings.team_builder_concurrency
    with _proposal_slots_lock:
        if _proposal_slots is None or _proposal_slots[0] != size:
            _proposal_slots = (size, threading.BoundedSemaphore(size))
        return _proposal_slots[1]


ProposalSlotsDep = Annotated[threading.BoundedSemaphore, Depends(get_proposal_slots)]


def get_http_call() -> HttpCall:
    """The one HTTP seam, as a dependency so a test can hand a fake where production
    hands `urllib_call`: the registry of PigroCRM's spaces is read through it (ORB-142)."""
    return urllib_call


HttpCallDep = Annotated[HttpCall, Depends(get_http_call)]


def get_tracker(settings: SettingsDep) -> Tracker | None:
    """The server half of the wizard's analytics (REB-215): `None` without a key, so a
    route that has one schedules the event and a route that has none does nothing."""
    return tracker_from_settings(settings)


TrackerDep = Annotated[Tracker | None, Depends(get_tracker)]


def get_renderer() -> Renderer:
    """The contracts' typesetter (REB-387): pandoc and Typst in the image. A dependency
    so a test hands `FakeRenderer` and never needs either binary."""
    return ContractRenderer()


RendererDep = Annotated[Renderer, Depends(get_renderer)]


def get_documenso(settings: SettingsDep) -> DocumensoClient | None:
    """This environment's Documenso client (REB-387), or `None` when signing is off: the
    send answers 503 with a sentence, as the member area does without a mail key."""
    return client_from_settings(settings)


DocumensoDep = Annotated[DocumensoClient | None, Depends(get_documenso)]


def get_signing_factory(
    settings: SettingsDep, renderer: RendererDep, documenso: DocumensoDep, sender: SenderDep
) -> SigningFactory:
    """`SigningService` as this environment configures it, for any session: the request's
    own, or the one a background task opens for itself (the webhook's). Built by the
    core's `signing_from_settings`, the one builder the MCP server and the sweep use too,
    with this request's own renderer, Documenso client and sender, so a test's dependency
    override reaches the service. `REBASE_SIGNER_JSON` stays the raw setting, unparsed:
    `build` runs for every route behind `SigningDep` (a cancel, a refresh, a resend or the
    webhook among them), so parsing it here would 503 all of them on a malformed value.
    `SigningService` itself parses it once, lazily, only where a document is about to be
    typeset (REB-406)."""
    return signing_from_settings(settings, renderer, documenso=documenso, sender=sender)


SigningDep = Annotated[SigningFactory, Depends(get_signing_factory)]


def get_session_opener() -> SessionOpener:
    """A session for work that runs after the response (REB-387's webhook): a background
    task must not borrow the request's session, which its dependency closes."""
    factory = _get_session_factory()
    return lambda: closing(factory())


SessionOpenerDep = Annotated[SessionOpener, Depends(get_session_opener)]


def get_team_builder(
    session: SessionDep, llm: LlmDep, settings: SettingsDep, tracker: TrackerDep
) -> TeamBuilder:
    """The engine (REB-511) for this request: the Claude seam, `None` without a key, and
    the tracker that counts the proposal."""
    return TeamBuilder(session, llm, settings, tracker=tracker)


TeamBuilderDep = Annotated[TeamBuilder, Depends(get_team_builder)]
