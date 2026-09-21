"""Signing up for a space: the second public write in the API, after Orbiters.

No `ActorDep`: the person has no account anywhere yet, which is the point. The router
works on the registry database (`TenantsRegistryDep`), never on a space, and answers
only for the root installation -- under `/<slug>/api/tenants` it is not registered
differently, but the page never offers it there, and a space creating spaces is not a
thing this product means.
"""

import secrets
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, EmailStr
from sqlalchemy import create_engine

from pigrocrm.core.auth.magic_link import MagicLinkService
from pigrocrm.core.auth.repository import UserRepository
from pigrocrm.core.auth.tokens import issue_access_token
from pigrocrm.core.db.session import session_factory
from pigrocrm.core.mail import welcome_mail
from pigrocrm.core.tenants import (
    TenantAvailability,
    TenantRead,
    TenantService,
    TenantSignup,
    lookup_member,
)
from pigrocrm.core.tenants.database import tenant_database_name, tenant_database_url
from pigrocrm_api.deps import SettingsDep, TenantsRegistryDep
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.ratelimit import (
    DISPONIBILE_REQUESTS_PER_MINUTE,
    TOO_MANY_REQUESTS_RESPONSE,
    spend_one,
)
from pigrocrm_api.sessions import SenderDep, set_access_cookie

router = APIRouter(prefix="/api/tenants", tags=["tenants"], responses=PROBLEM_RESPONSES)


class RootSpace(BaseModel):
    """The root installation's own space name (`PIGROCRM_ROOT_SLUG`), or null when the
    root answers only without a prefix."""

    slug: str | None


@router.get("/root", response_model=RootSpace)
def root_space(settings: SettingsDep) -> RootSpace:
    """What the SPA asks under a prefix to learn whether it is the root wearing its own
    name -- in which case its login may still offer to create a space."""
    return RootSpace(slug=settings.root_slug or None)


@router.get("/", response_model=list[TenantRead])
def list_spaces(
    registry: TenantsRegistryDep,
    settings: SettingsDep,
    authorization: Annotated[str | None, Header()] = None,
) -> list[TenantRead]:
    """Every space in the registry, newest first, for the one caller that holds
    `PIGROCRM_REGISTRY_TOKEN`: the Orbiters hub, whose admin area shows which spaces
    exist and whose they are (ORB-142). Without the token configured the route does not
    exist (404), so nothing says there is a door; with it, a missing or wrong bearer is a
    401. What comes back is the registry row and nothing about the database behind it."""
    if not settings.registry_token:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    presented = authorization.removeprefix("Bearer ").strip() if authorization else ""
    # Bytes, not str: Starlette decodes headers as latin-1 and `compare_digest` refuses a
    # `str` with a non-ASCII character, which would turn a stray byte into a 500.
    if not presented or not secrets.compare_digest(
        presented.encode("utf-8"), settings.registry_token.encode("utf-8")
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token non valido")
    return TenantService(registry, settings).list()


class MemberQuestion(BaseModel):
    """The address the signup asks about. In a body, never in the URL: a query string is
    written by the access log and by every proxy on the way, on both hosts."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr


class MemberAnswer(BaseModel):
    """What the signup learns about an address before the person has an account: whether
    the Orbiters hub knows them as a community member, their two names if so, and how
    many spaces the registry already holds in that name. A count, not the slugs: the
    address is not proven yet, and which spaces are whose is the mail's to tell."""

    membro: bool
    nome: str | None
    cognome: str | None
    spazi: int


# With the other public signup routes: no account exists yet when this is asked.
@router.post("/member", response_model=MemberAnswer)
def member(
    payload: MemberQuestion, request: Request, registry: TenantsRegistryDep, settings: SettingsDep
) -> MemberAnswer:
    """Whether an address belongs to an Orbiters community member, and how many spaces
    it already owns here (ORB-173). No auth, like the signup itself: the person has no
    account yet, so the route is throttled per client instead. The hub is asked with
    `PIGROCRM_REGISTRY_TOKEN` at `PIGROCRM_HUB_URL` and given five seconds; unreachable,
    unconfigured or refusing, the answer is `membro: false` and the signup goes on.
    `spazi` comes from this installation's own registry and answers even when the hub
    does not."""
    spend_one(request)
    address = str(payload.email).strip().lower()
    found = lookup_member(settings, address)
    return MemberAnswer(
        membro=found.membro,
        nome=found.nome,
        cognome=found.cognome,
        spazi=TenantService(registry, settings).count_for_owner(address),
    )


@router.get(
    "/{slug}/disponibile",
    response_model=TenantAvailability,
    responses={429: TOO_MANY_REQUESTS_RESPONSE},
)
def availability(
    slug: str, request: Request, registry: TenantsRegistryDep, settings: SettingsDep
) -> TenantAvailability:
    """Whether a name can still be taken, and if not why -- reserved, malformed or in
    use -- in the words the page shows while the person is still typing. Unauthenticated
    by design, like `member` and `signup`, so it is throttled the same way (REB-228) --
    but on its own budget (`scope="disponibile"`, `DISPONIBILE_REQUESTS_PER_MINUTE`),
    since this is the one route of the three a person's own typing calls repeatedly: a
    shared bucket with `member` and `signup` would let a few hesitations while naming a
    business starve the tokens the actual `POST /` still needs to create the space."""
    spend_one(request, scope="disponibile", per_minute=DISPONIBILE_REQUESTS_PER_MINUTE)
    return TenantService(registry, settings).availability(slug.strip().lower())


@router.post("/", response_model=TenantRead, status_code=status.HTTP_201_CREATED)
def signup(
    data: TenantSignup,
    request: Request,
    registry: TenantsRegistryDep,
    settings: SettingsDep,
    response: Response,
    background: BackgroundTasks,
    sender: SenderDep,
) -> TenantRead:
    """Creates the space: a registry row, a migrated database, its first admin, its
    defaults. 409 when the name is taken, 422 when it is malformed or reserved.

    Registering is entering (spec 2026-09-12 §6.4), for as long as an address nobody has
    proven deserves: the response carries the space's *access* cookie, at the space's
    path (the browser accepts it from the root's response: same host), and `Location` is
    the space's home. No refresh token: whoever typed somebody else's email works for
    `access_token_minutes` and then stops, cannot mint a personal token and cannot add a
    user (`require_verified_identity`). The welcome mail carries a link that enters:
    the first click proves the address, opens the durable session and revokes what came
    before (`MagicLinkService.enter`). Without a sender or a public origin the space is
    created all the same, and the login page's own link does the rest. Throttled per
    client like the member question: a `CREATE DATABASE` per anonymous POST."""
    spend_one(request)
    tenant = TenantService(registry, settings).provision(data)
    engine = create_engine(
        tenant_database_url(settings, tenant_database_name(tenant.slug)), future=True
    )
    try:
        with session_factory(engine)() as space:
            admin = UserRepository(space).get_by_email(tenant.owner_email)
            if admin is None:  # pragma: no cover - provision just created it
                raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "spazio senza admin")
            access = issue_access_token(admin.id, admin.ruolo, settings)
            origin = settings.public_url.strip().rstrip("/")
            raw = (
                MagicLinkService(space, settings).request(tenant.owner_email)
                if sender and origin
                else None
            )
    finally:
        engine.dispose()
    set_access_cookie(
        response,
        access,
        settings.access_token_minutes,
        secure=settings.cookie_secure,
        path=f"/{tenant.slug}/",
    )
    response.headers["Location"] = f"/{tenant.slug}/app/"
    if sender is not None and origin and raw:
        background.add_task(
            sender.send,
            welcome_mail(
                tenant.owner_email,
                f"{origin}/{tenant.slug}/app/verify?t={raw}",
                f"{origin}/{tenant.slug}/app/login",
                membro=data.membro,
            ),
        )
    return tenant
