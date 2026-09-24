"""One engine per process, one session per request."""

import threading
from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from rebase_core.admin_tokens import AdminRead
from rebase_core.analytics import Tracker, tracker_from_settings
from rebase_core.config import Settings, get_settings
from rebase_core.contracts.fields import signer_data
from rebase_core.contracts.render import ContractRenderer, Renderer
from rebase_core.db import create_engine_from_settings, session_factory
from rebase_core.documenso import DocumensoClient, client_from_settings
from rebase_core.http import HttpCall, urllib_call
from rebase_core.mail import EmailSender, sender_from_settings
from rebase_core.members import MemberService
from rebase_core.schemas import MeRead
from rebase_core.signing import SigningService
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


def get_sender(settings: SettingsDep) -> EmailSender | None:
    """`None` without a key: the route answers 503 and nothing pretends to send."""
    return sender_from_settings(settings)


SenderDep = Annotated[EmailSender | None, Depends(get_sender)]


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

SigningFactory = Callable[[Session], SigningService]


def get_signing_factory(
    settings: SettingsDep, renderer: RendererDep, documenso: DocumensoDep, sender: SenderDep
) -> SigningFactory:
    """`SigningService` as this environment configures it, for any session: the
    request's own, or the one a background task opens for itself (the webhook's).
    `REBASE_SIGNER_JSON` is read inside `build`, not here: this factory itself runs on
    every request a signing route takes, and a malformed value must not turn a route
    that never typesets (a future cancel or webhook) into a 503 (REB-406 controller
    ruling)."""

    def build(session: Session) -> SigningService:
        return SigningService(
            session,
            renderer=renderer,
            documenso=documenso,
            sender=sender,
            signer=signer_data(settings.signer_json),
            contracts_mail=settings.contracts_mail,
            allow_draft=settings.contracts_allow_draft,
        )

    return build


SigningDep = Annotated[SigningFactory, Depends(get_signing_factory)]
