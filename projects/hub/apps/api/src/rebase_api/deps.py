"""One engine per process, one session per request."""

import threading
from collections.abc import Callable, Iterator
from contextlib import closing
from datetime import datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from rebase_core.admin_tokens import AdminRead
from rebase_core.analytics import Tracker, tracker_from_settings
from rebase_core.audit import utcnow
from rebase_core.campaigns.sender import CampaignSender, campaign_sender_from_settings
from rebase_core.cloud import CLOUD_CLOSED, CloudCaller, CloudTalentService
from rebase_core.config import Settings, get_settings
from rebase_core.contracts.render import ContractRenderer, Renderer
from rebase_core.db import SessionOpener, create_engine_from_settings, session_factory
from rebase_core.documenso import DocumensoClient, client_from_settings
from rebase_core.engagements import EngagementService
from rebase_core.http import HttpCall, urllib_call, urllib_engagements_call
from rebase_core.llm import AiCall, LlmCall, call_for_task
from rebase_core.mail import EmailSender, sender_from_settings
from rebase_core.members import MemberService
from rebase_core.schemas import MeRead
from rebase_core.signing import SigningFactory, signing_from_settings
from rebase_core.team_builder import TeamBuilder
from rebase_core.users import UserService

MEMBER_COOKIE = "rebase_user"
# What the cookie was called until REB-668. The value is the same raw session token and
# the `sessions` row behind it does not change, so a person signed in before the rename
# stays signed in: `session_token` reads this name when the new one is absent and
# `adopt_legacy_cookie` reissues the session under the new name on the first request
# that resolves it. REB-697 drops both, and this constant, once `member_session_days`
# (30) have passed since the release that carries the rename: every cookie under the
# old name has expired by then.
LEGACY_MEMBER_COOKIE = "orbiters_user"


def session_tokens(request: Request) -> list[str]:
    """The raw session tokens the request carries, the new cookie's first and the old
    one's after it. A caller tries each in turn: a stale new cookie beside a live old
    one (a rollback followed by a roll-forward) must not hide the session."""
    names = (MEMBER_COOKIE, LEGACY_MEMBER_COOKIE)
    return list(dict.fromkeys(request.cookies[name] for name in names if request.cookies.get(name)))


def set_member_cookie(response: Response, settings: Settings, raw: str) -> None:
    """The session cookie under its current name, and the old name dropped in the same
    answer so a browser never holds both."""
    response.set_cookie(
        MEMBER_COOKIE,
        raw,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.member_session_days * 86400,
        path="/",
    )
    _drop_legacy_cookie(response, settings)


def clear_member_cookie(response: Response, settings: Settings) -> None:
    """Both names, so a sign-out leaves nothing behind in a browser that still has the old."""
    response.delete_cookie(
        MEMBER_COOKIE,
        path="/",
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )
    _drop_legacy_cookie(response, settings)


def _drop_legacy_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        LEGACY_MEMBER_COOKIE,
        path="/",
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )


def adopt_legacy_cookie(request: Request, response: Response, settings: Settings, raw: str) -> None:
    """A session that resolved through anything but the current cookie is reissued under
    the new name, with a fresh 30 days: the server already slid the row's own deadline, so
    the browser's copy follows it once. It rides the response of a route that answers with
    a model; a route that returns its own `Response` (a download) or raises does not carry
    it, and the next call that does, `/me` on every page load, reissues it."""
    if request.cookies.get(MEMBER_COOKIE) != raw:
        set_member_cookie(response, settings, raw)


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


def get_admin(
    request: Request, response: Response, session: SessionDep, settings: SettingsDep
) -> AdminRead:
    """A signed-in person whose role is admin: a signed-in non-admin is a real identity
    hitting the wrong door (403), nobody signed in at all is 401 (design record
    2026-09-17 §4). The password login and its `orbiters_admin` cookie are gone
    (REB-281): every admin resolves through the one cookie, `rebase_user` (`orbiters_user`
    until REB-668, still read), like everyone else."""
    users = UserService(session, settings)
    tokens = session_tokens(request)
    for raw in tokens:
        me = users.resolve_admin(raw)
        if me is not None:
            adopt_legacy_cookie(request, response, settings, raw)
            return AdminRead.model_validate(me)
    if any(users.resolve(raw) is not None for raw in tokens):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Serve il ruolo di amministratore")
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Autenticazione richiesta")


AdminDep = Annotated[AdminRead, Depends(get_admin)]


def get_me(
    request: Request, response: Response, session: SessionDep, settings: SettingsDep
) -> MeRead:
    """Whoever the cookie resolves to, member or admin: the one dependency every
    signed-in route may depend on."""
    users = UserService(session, settings)
    for raw in session_tokens(request):
        user = users.resolve(raw)
        if user is not None:
            adopt_legacy_cookie(request, response, settings, raw)
            return MemberService(session).me_read(user.id)
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Autenticazione richiesta")


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


def get_prova_call(settings: SettingsDep) -> Callable[[str], LlmCall]:
    """«Prova» on «Modelli AI» (REB-833): the seam for one task, recording its usage as
    `purpose="prova"`. A dependency, so a test hands a stub for the provider."""
    factory = _get_session_factory()
    return lambda task: AiCall(factory, settings, task, purpose="prova")


ProvaCallDep = Annotated[Callable[[str], LlmCall], Depends(get_prova_call)]

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


def _engagements_call(http: HttpCall) -> HttpCall:
    """The seam the link to Pigro reads through (REB-499): the request's own
    `HttpCallDep`, so a test's override of `get_http_call` reaches it too, except that
    production's ten-second `urllib_call` becomes `urllib_engagements_call`, whose 90
    seconds leave room for the first link of a freelancer, which opens their space."""
    return urllib_engagements_call if http is urllib_call else http


def get_engagements(
    session: SessionDep, settings: SettingsDep, http: HttpCallDep, sender: SenderDep
) -> EngagementService:
    """The link of an active match to its deal on Pigro, and that deal's hours
    (REB-499): «Riprova su Pigro» and «Consuntivo». The freelancer's mail goes through
    the request's own sender, as every other mail of the API does."""
    return EngagementService(session, settings, _engagements_call(http), sender=sender)


EngagementsDep = Annotated[EngagementService, Depends(get_engagements)]


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
    settings: SettingsDep,
    renderer: RendererDep,
    documenso: DocumensoDep,
    sender: SenderDep,
    http: HttpCallDep,
) -> SigningFactory:
    """`SigningService` as this environment configures it, for any session: the request's
    own, or the one a background task opens for itself (the webhook's). Built by the
    core's `signing_from_settings`, the one builder the MCP server and the sweep use too,
    with this request's own renderer, Documenso client and sender, so a test's dependency
    override reaches the service. `REBASE_SIGNER_JSON` stays the raw setting, unparsed:
    `build` runs for every route behind `SigningDep` (a cancel, a refresh, a resend or the
    webhook among them), so parsing it here would 503 all of them on a malformed value.
    `SigningService` itself parses it once, lazily, only where a document is about to be
    typeset (REB-406). The engagement service it hands over, which links a match the
    signature turns active (REB-499), reads through the seam `get_engagements` uses."""
    return signing_from_settings(
        settings, renderer, documenso=documenso, sender=sender, http=_engagements_call(http)
    )


SigningDep = Annotated[SigningFactory, Depends(get_signing_factory)]


def get_session_opener() -> SessionOpener:
    """A session for work that runs after the response (REB-387's webhook): a background
    task must not borrow the request's session, which its dependency closes."""
    factory = _get_session_factory()
    return lambda: closing(factory())


SessionOpenerDep = Annotated[SessionOpener, Depends(get_session_opener)]


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


CardLlmDep = Annotated[LlmCall | None, Depends(get_card_llm)]
TeamLlmDep = Annotated[LlmCall | None, Depends(get_team_llm)]


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
