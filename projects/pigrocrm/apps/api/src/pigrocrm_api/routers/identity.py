"""Root-scoped identity endpoints: the layer proving who someone is before any
space's database opens (design 2026-09-23, REB-345/376/377). Registered plainly, the
same "root-only by convention rather than by a second registration" shape
`routers/tenants.py` already uses -- see that module's own docstring.

`POST /api/identity/logout` is REB-376's own scope. The chooser -- `GET
/api/identity/spaces` and `POST /api/identity/enter/{slug}` -- is REB-377, §3: the
first is a live, bounded scan across every tenant's own database (§7 decision B1,
widened from `_owned_slugs`'s `Tenant.owner_email` to each space's own `users.email`
so an invited member is listed too, not only a space's creator); the second opens
exactly the one space chosen, with no second proof, and never creates a row.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError

from pigrocrm.core.auth.refresh_service import RefreshTokenService
from pigrocrm.core.auth.repository import UserRepository
from pigrocrm.core.auth.schemas import UserRead
from pigrocrm.core.auth.tokens import decode_token, issue_access_token
from pigrocrm.core.config import Settings
from pigrocrm.core.db.session import session_factory
from pigrocrm.core.errors import DomainError, NotFound
from pigrocrm.core.identity.service import IdentityService
from pigrocrm.core.tenants import TenantService
from pigrocrm.core.tenants.database import tenant_database_name, tenant_database_url
from pigrocrm_api.deps import (
    ACCESS_COOKIE,
    IDENTITY_COOKIE,
    REFRESH_COOKIE,
    SettingsDep,
    TenantsRegistryDep,
    _tenant_session_factory,
)
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.sessions import set_session_cookie
from pigrocrm_api.tenancy import first_cookie

router = APIRouter(prefix="/api/identity", tags=["identity"], responses=PROBLEM_RESPONSES)

# The scan below opens one connection per tenant, unlike a single-space open -- a
# network partition to one space's database must not hold the whole chooser response
# hostage for however long libpq's own default connect timeout is. Short and fixed,
# per design §7 decision B1's own "a short, fixed per-connection timeout."
SPACE_SCAN_CONNECT_TIMEOUT_SECONDS = 2


def _get_identity_email(
    request: Request, registry: TenantsRegistryDep, settings: SettingsDep
) -> str:
    """The proven email behind a live `pigrocrm_identity` cookie, or a 401: no
    cookie, a malformed/expired/wrong-type token, or a token whose `IdentitySession`
    is unknown, revoked or past its own `expires_at` (`IdentityService.resolve`, the
    "checked-on-every-read" liveness §2 describes). §3's own gate: "the chooser is
    never shown to an unproven visitor." No `ActorDep` here at all -- this cookie is
    not a space actor, there is no role to check, and the routes below are
    `PUBLIC_ROUTES`-exempt in the role-matrix sweep for exactly that reason."""
    token = first_cookie(request, IDENTITY_COOKIE)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Nessuna identità provata")
    try:
        payload = decode_token(token, settings, expected_type="identity")
    except DomainError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sessione scaduta") from exc
    email = IdentityService(registry, settings).resolve(payload.sub, payload.jti)
    if email is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sessione scaduta")
    return email


IdentityEmailDep = Annotated[str, Depends(_get_identity_email)]


class IdentitySpace(BaseModel):
    """One row of the chooser (§3): a space this identity's own email may enter, and
    the role it holds there -- never more, and never the slugs of spaces it cannot
    reach (§0's own "an unproven email learns a number, never a list"; this route
    requires a proven one and lists exactly the spaces it proved)."""

    slug: str
    ruolo: str


def _space_role_for(settings: Settings, slug: str, email: str) -> str | None:
    """This identity's own role in the space `slug`, or `None`: no matching row in
    that space's `users`, a deactivated one, or a space this bounded connection could
    not reach at all -- caught and skipped exactly like `_space_link`
    (`routers/auth.py`)'s own "must not turn the request into a 500 for the one
    address that owns it," widened with a connect timeout because this runs once per
    tenant rather than once per request (design §7 decision B1)."""
    engine = create_engine(
        tenant_database_url(settings, tenant_database_name(slug)),
        future=True,
        connect_args={"connect_timeout": SPACE_SCAN_CONNECT_TIMEOUT_SECONDS},
    )
    try:
        with session_factory(engine)() as space:
            user = UserRepository(space).get_by_email(email)
            return user.ruolo if user is not None and user.attivo else None
    except SQLAlchemyError:
        return None
    finally:
        engine.dispose()


@router.get("/spaces", response_model=list[IdentitySpace])
def spaces(
    email: IdentityEmailDep, registry: TenantsRegistryDep, settings: SettingsDep
) -> list[IdentitySpace]:
    """Every space this identity may enter, per §3's live scan (§7 decision B1): one
    bounded connection per tenant, so one unreachable space is silently absent from
    this one response rather than failing it -- the next visit tries again."""
    slugs = [tenant.slug for tenant in TenantService(registry, settings).list()]
    result: list[IdentitySpace] = []
    for slug in slugs:
        ruolo = _space_role_for(settings, slug, email)
        if ruolo is not None:
            result.append(IdentitySpace(slug=slug, ruolo=ruolo))
    return result


@router.post("/enter/{slug}", response_model=UserRead)
def enter(
    slug: str, response: Response, email: IdentityEmailDep, settings: SettingsDep
) -> UserRead:
    """Opens the space `slug` with no second proof -- the identity cookie already is
    one (§3). `deps._tenant_session_factory`, called directly rather than through a
    request's own prefix, already answers `NotFound` (-> 404) for a slug nobody
    registered, the same way a stranger's cookie meets a refusal on a space it does
    not belong to (`tenancy.cookie_path`'s own docstring); a slug that exists but has
    no active row for this identity's own email answers the same 404 -- `enter` only
    ever reads `users`, it never creates one. A live, active row mints the ordinary
    access+refresh pair scoped `path=/<slug>/`, exactly as `login` does today
    (`routers/auth.py`), and answers `UserRead` so the SPA can navigate the same way
    `homeAfterEntry()` already does after any other entry point."""
    factory = _tenant_session_factory(slug, settings)
    with factory() as space:
        user = UserRepository(space).get_by_email(email)
        if user is None or not user.attivo:
            raise NotFound("user", email)
        path = f"/{slug}/"
        set_session_cookie(
            response,
            ACCESS_COOKIE,
            issue_access_token(user.id, user.ruolo, settings),
            settings.access_token_minutes * 60,
            secure=settings.cookie_secure,
            path=path,
        )
        set_session_cookie(
            response,
            REFRESH_COOKIE,
            RefreshTokenService(space).issue(user.id, settings),
            settings.refresh_token_days * 86400,
            secure=settings.cookie_secure,
            path=path,
        )
        return UserRead.model_validate(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request, response: Response, registry: TenantsRegistryDep, settings: SettingsDep
) -> None:
    """Revokes every live `IdentitySession` for this identity (§2's own "signs out of
    the identity everywhere it was used"), not only the one this browser presents,
    and clears the cookie at `path=/` in this browser. Idempotent, the same
    goal-state discipline the space-scoped `logout` already follows
    (`routers/auth.py`): an absent, malformed or already-expired cookie has nothing
    left to revoke, so that case answers 204 too, never a 401 -- this route has no
    actor dependency to refuse with one."""
    token = first_cookie(request, IDENTITY_COOKIE)
    if token:
        try:
            payload = decode_token(token, settings, expected_type="identity")
        except DomainError:
            payload = None
        if payload is not None:
            IdentityService(registry, settings).revoke_all(payload.sub)
    response.delete_cookie(IDENTITY_COOKIE, path="/")
