"""Signing up for a space: the second public write in the API, after the rebase signup.

No `ActorDep`: the person has no account anywhere yet, which is the point. The router
works on the registry database (`TenantsRegistryDep`), never on a space, and answers
only for the root installation -- under `/<slug>/api/tenants` it is not registered
differently, but the page never offers it there, and a space creating spaces is not a
thing this product means.
"""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, EmailStr
from sqlalchemy.exc import SQLAlchemyError

from pigrocrm.core.auth.repository import UserRepository
from pigrocrm.core.auth.tokens import issue_access_token
from pigrocrm.core.tenants import (
    TenantAvailability,
    TenantRead,
    TenantService,
    TenantSignup,
    lookup_member,
)
from pigrocrm.core.tenants.welcome import welcome
from pigrocrm_api.deps import SettingsDep, TenantsRegistryDep, space_session_factory
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.ratelimit import (
    AVAILABLE_REQUESTS_BY_MINUTE,
    RETRY_AFTER_SECONDS,
    TOO_MANY_REQUESTS_RESPONSE,
    spend_for_recipient,
    spend_global,
    spend_one,
)
from pigrocrm_api.routers.identity import proven_email
from pigrocrm_api.service_token import require_service_token
from pigrocrm_api.sessions import SenderDep, set_access_cookie

router = APIRouter(prefix="/api/tenants", tags=["tenants"], responses=PROBLEM_RESPONSES)

# What an anonymous caller may make this process do. A signup runs `CREATE DATABASE`, the
# whole Alembic history and the seeding inside one request of the API's thread pool, and
# the migrations already take turns (`tenants.service._MIGRATIONS`), so more than a couple
# in flight only parks pool threads that every other route needs. Past the cap the answer
# is 503 and the person retries; the global bucket bounds the rate whoever asks, under the
# per-client one, and `Settings.max_spaces` bounds what the registry may hold at all.
MAX_CONCURRENT_PROVISIONS = 2
GLOBAL_SIGNUPS_PER_MINUTE = 10
_provisioning = threading.BoundedSemaphore(MAX_CONCURRENT_PROVISIONS)

SIGNUP_BUSY = "Stiamo creando altri spazi in questo momento. Riprova tra un minuto."
SIGNUP_CLOSED = "Per ora non si possono aprire nuovi spazi su questa installazione."
SERVICE_UNAVAILABLE_RESPONSE: dict[str, Any] = {
    "description": (
        "The service cannot create a space now: too many creations in progress "
        "(`Retry-After` seconds) or the maximum number of spaces has been reached."
    ),
    "content": {
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {"detail": {"type": "string"}},
                "required": ["detail"],
            }
        }
    },
}


@contextmanager
def _provisioning_slot() -> Iterator[None]:
    if not _provisioning.acquire(blocking=False):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            SIGNUP_BUSY,
            headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
        )
    try:
        yield
    finally:
        _provisioning.release()


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
    `PIGROCRM_REGISTRY_TOKEN`: the rebase hub, whose admin area shows which spaces
    exist and whose they are (ORB-142). Without the token configured the route does not
    exist (404), so nothing says there is a door; with it, a missing or wrong bearer is a
    401. What comes back is the registry row and nothing about the database behind it."""
    require_service_token(settings.registry_token, authorization)
    return TenantService(registry, settings).list()


class MemberQuestion(BaseModel):
    """The address the signup asks about. In a body, never in the URL: a query string is
    written by the access log and by every proxy on the way, on both hosts."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr


class MemberAnswer(BaseModel):
    """What the signup learns about an address before the person has an account: whether
    the rebase hub knows them as a community member, and whether the registry already
    holds a space in that name, and whether the hub has a name for the person. Three
    booleans and nothing else: the address is not proven yet, so no names, no count and
    no slugs; which spaces are whose is the mail's to tell."""

    member: bool
    has_spaces: bool
    # Whether the hub also knows the person's name, so the wizard need not ask for it
    # (REB-822). A boolean like the others: the name itself is never returned.
    known_name: bool


# With the other public signup routes: no account exists yet when this is asked.
@router.post("/member", response_model=MemberAnswer)
def member(
    payload: MemberQuestion, request: Request, registry: TenantsRegistryDep, settings: SettingsDep
) -> MemberAnswer:
    """Whether an address belongs to a rebase community member, and whether it already
    owns a space here (ORB-173). No auth, like the signup itself: the person has no
    account yet, so the route is throttled per client instead. The hub is asked with
    `PIGROCRM_REGISTRY_TOKEN` at `PIGROCRM_HUB_URL` and given five seconds; unreachable,
    unconfigured or refusing, the answer is `membro: false` and the signup goes on.
    `has_spaces` comes from this installation's own registry and answers even when the
    hub does not. The hub's names for the address are never returned: nobody has proven
    the address yet."""
    spend_one(request)
    address = str(payload.email).strip().lower()
    found = lookup_member(settings, address)
    return MemberAnswer(
        member=found.member,
        has_spaces=TenantService(registry, settings).count_for_owner(address) > 0,
        known_name=bool((found.first_name or "").strip()),
    )


@router.get(
    "/{slug}/available",
    response_model=TenantAvailability,
    responses={429: TOO_MANY_REQUESTS_RESPONSE},
)
def availability(
    slug: str, request: Request, registry: TenantsRegistryDep, settings: SettingsDep
) -> TenantAvailability:
    """Whether a name can still be taken, and if not why -- reserved, malformed or in
    use -- in the words the page shows while the person is still typing. Unauthenticated
    by design, like `member` and `signup`, so it is throttled the same way (REB-228) --
    but on its own budget (`scope="available"`, `AVAILABLE_REQUESTS_BY_MINUTE`),
    since this is the one route of the three a person's own typing calls repeatedly: a
    shared bucket with `member` and `signup` would let a few hesitations while naming a
    business starve the tokens the actual `POST /` still needs to create the space."""
    spend_one(request, scope="available", per_minute=AVAILABLE_REQUESTS_BY_MINUTE)
    return TenantService(registry, settings).availability(slug.strip().lower())


@router.post(
    "/",
    response_model=TenantRead,
    status_code=status.HTTP_201_CREATED,
    responses={429: TOO_MANY_REQUESTS_RESPONSE, 503: SERVICE_UNAVAILABLE_RESPONSE},
)
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
    defaults. 409 when the name is taken, 422 when it is malformed or reserved, or when
    neither the body nor the hub gives the founder's name (`first_name`).

    Registering is entering (spec 2026-09-12 §6.4), for as long as an address nobody has
    proven deserves: the response carries the space's *access* cookie, at the space's
    path (the browser accepts it from the root's response: same host), and `Location` is
    the space's home. No refresh token: whoever typed somebody else's email works for
    `access_token_minutes` and then stops, cannot mint a personal token and cannot add a
    user (`require_verified_identity`). The welcome mail carries a link that enters:
    the first click proves the address, opens the durable session and revokes what came
    before (`MagicLinkService.enter`). Without a sender or a public origin the space is
    created all the same, and the login page's own link does the rest. Throttled per
    client like the member question: a `CREATE DATABASE` per anonymous POST. Behind that,
    a global rate, a cap on signups in flight at once and a cap on the registry's size
    answer 429 or 503 with a sentence; the welcome mail is throttled per recipient and
    simply not sent past it.

    The one exception to "the signup proves nothing" (REB-677): a browser that holds a
    live `pigrocrm_identity` cookie for the very address the signup carries has already
    proven it, by a link it spent in another space, so the new admin starts with
    `email_verified_at` set and can invite at once. No cookie, an expired or revoked
    one, or another address changes nothing: typing an address proves nothing, and
    trusting it would let anyone browse the spaces of an address they do not own."""
    spend_one(request)
    spaces = TenantService(registry, settings)
    if spaces.count() >= settings.max_spaces:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, SIGNUP_CLOSED)
    if not (data.first_name or "").strip():
        # The founder is a person, not the space (REB-822): what the person did not type is
        # what the hub knows about the address. Never trusted beyond naming the account,
        # and when the hub knows nothing `provision` refuses with a 422 on `first_name`,
        # which the wizard answers by asking.
        found = lookup_member(settings, str(data.email))
        data = data.model_copy(
            update={"first_name": found.first_name, "last_name": found.last_name}
        )
    # The global bucket guards the expensive part (the database and its migrations): a name
    # `provision` refuses at the registry row (taken or reserved: 409, 422) costs nothing
    # and goes straight there without spending it.
    if spaces.availability(data.slug).available:
        spend_global("signup", per_minute=GLOBAL_SIGNUPS_PER_MINUTE)
    with _provisioning_slot():
        tenant = spaces.provision(data)
    try:
        already_proven = proven_email(request, registry, settings) == tenant.owner_email.lower()
    except SQLAlchemyError:
        # A bonus lookup never turns a signup that already created the space into a
        # 500, which a retry would answer 409: the welcome link is the fallback.
        already_proven = False
    # A recipient past its budget gets no mail (and no link minted for it), never a failed
    # signup: the space exists, and the login page's own link does the rest.
    mailer = sender if sender is not None and spend_for_recipient(tenant.owner_email) else None
    with space_session_factory(tenant.slug, settings)() as space:
        admin = UserRepository(space).get_by_email(tenant.owner_email)
        if admin is None:  # pragma: no cover - provision just created it
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "spazio senza admin")
        if already_proven:
            admin.email_verified_at = datetime.now(UTC)
            space.commit()
        access = issue_access_token(admin.id, admin.role, settings, space=tenant.slug)
        mail = welcome(space, settings, mailer, tenant.owner_email, tenant.slug, member=data.member)
    set_access_cookie(
        response,
        access,
        settings.access_token_minutes,
        secure=settings.cookie_secure,
        path=f"/{tenant.slug}/",
    )
    response.headers["Location"] = f"/{tenant.slug}/app/"
    if mailer is not None and mail is not None:
        background.add_task(mailer.send, mail)
    return tenant
