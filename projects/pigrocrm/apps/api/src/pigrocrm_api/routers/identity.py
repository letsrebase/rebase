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

The root installation is not a registry row (design §1: optional, grandfathered),
so the scan alone never names it -- and the day a registry space carried its admin's
own name, the chooser replaced the login form with that space alone and the root had
no way in from the login page (REB-583, 2026-09-28). When the root wears
a name (`PIGROCRM_ROOT_SLUG`), both routes treat that name as the root's own: listed
first when the identity's email has an active row in the root's `users`, entered
with the root's own jar at `path=/` (decision 2026-09-09, `tenancy.cookie_path`).
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from pigrocrm.core.auth.invitations import InvitationService
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
    _get_session_factory,
    _tenant_session_factory,
)
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.sessions import set_session_cookie
from pigrocrm_api.tenancy import first_cookie, is_root_name

router = APIRouter(prefix="/api/identity", tags=["identity"], responses=PROBLEM_RESPONSES)

# The scan below opens one connection per tenant, unlike a single-space open -- a
# network partition to one space's database must not hold the whole chooser response
# hostage for however long libpq's own default connect timeout is. Short and fixed,
# per design §7 decision B1's own "a short, fixed per-connection timeout."
SPACE_SCAN_CONNECT_TIMEOUT_SECONDS = 2

# What the invitation door answers for every way there is of having none (REB-678): said
# once, in the invitee's language, and never with the address or the slug in it.
NO_INVITATION = (
    "Questo invito non c'è più: è scaduto, è stato revocato o lo hai già accettato. "
    "Chiedi a chi ti ha invitato di rimandarlo."
)


def proven_email(request: Request, registry: Session, settings: Settings) -> str | None:
    """The proven email behind a live `pigrocrm_identity` cookie, or `None`: no cookie, a
    malformed/expired/wrong-type token, or a token whose `IdentitySession` is unknown,
    revoked or past its own `expires_at` (`IdentityService.resolve`, the
    "checked-on-every-read" liveness §2 describes). The one read of the cookie, shared
    by the chooser's gate below and by the signup, which trusts an address this browser
    already proved (REB-677)."""
    token = first_cookie(request, IDENTITY_COOKIE)
    if not token:
        return None
    try:
        payload = decode_token(token, settings, expected_type="identity")
    except DomainError:
        return None
    return IdentityService(registry, settings).resolve(payload.sub, payload.jti)


def _get_identity_email(
    request: Request, registry: TenantsRegistryDep, settings: SettingsDep
) -> str:
    """The proven email behind a live `pigrocrm_identity` cookie, or a 401 (`proven_email`
    says what counts as live). §3's own gate: "the chooser is never shown to an unproven
    visitor." No `ActorDep` here at all -- this cookie is not a space actor, there is no
    role to check, and the routes below are `PUBLIC_ROUTES`-exempt in the role-matrix
    sweep for exactly that reason."""
    if not first_cookie(request, IDENTITY_COOKIE):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Nessuna identità provata")
    email = proven_email(request, registry, settings)
    if email is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sessione scaduta")
    return email


IdentityEmailDep = Annotated[str, Depends(_get_identity_email)]


class IdentitySpace(BaseModel):
    """One row of the chooser (§3): a space this identity's own email may enter, and
    the role it holds there -- never more, and never the slugs of spaces it cannot
    reach (§0's own "an unproven email learns a number, never a list"; this route
    requires a proven one and lists exactly the spaces it proved).

    `invito` is on the rows of a different kind (REB-678), and on no other: a space where
    this address holds an open invitation and no account yet, so a mail that never
    arrived does not leave the invitation unfindable. `ruolo` is then the role it
    carries, `invitato_da` who sent it and `nome` the name it holds (absent when the
    acceptance page will ask). The route leaves out whatever is still its default."""

    slug: str
    ruolo: str
    invito: Literal[True] | None = None
    invitato_da: str | None = None
    nome: str | None = None


class InvitationDoor(BaseModel):
    """Where an open invitation is accepted: the space's own acceptance page, with a
    token minted for the person who proved the address (REB-678). A path and not a
    URL, because the chooser is already on the origin it is served from."""

    url: str


def _space_entry_for(settings: Settings, slug: str, email: str) -> IdentitySpace | None:
    """This identity's own row in the space `slug`, or `None`: its active user there,
    else the invitation it holds there (REB-678), else nothing, which is also what a
    deactivated row or a space this bounded connection could not reach at all answers --
    caught and skipped exactly like `_space_link` (`routers/auth.py`)'s own "must not
    turn the request into a 500 for the one address that owns it," widened with a
    connect timeout because this runs once per tenant rather than once per request
    (design §7 decision B1)."""
    engine = create_engine(
        tenant_database_url(settings, tenant_database_name(slug)),
        future=True,
        connect_args={"connect_timeout": SPACE_SCAN_CONNECT_TIMEOUT_SECONDS},
    )
    try:
        with session_factory(engine)() as space:
            user = UserRepository(space).get_by_email(email)
            if user is not None and user.attivo:
                return IdentitySpace(slug=slug, ruolo=user.ruolo)
            offer = InvitationService(space).offer_for(email)
            if offer is None:
                return None
            return IdentitySpace(
                slug=slug,
                ruolo=offer.ruolo,
                invito=True,
                invitato_da=offer.invitato_da,
                nome=offer.nome,
            )
    except SQLAlchemyError:
        return None
    finally:
        engine.dispose()


def _root_role_for(settings: Settings, email: str) -> str | None:
    """This identity's own role in the root installation, or `None`: no matching row
    in the root's own `users`, a deactivated one, or a root whose database cannot be
    reached right now -- skipped like a space the scan cannot open, never a 500. The
    session comes from the root's own pooled engine, the one `deps` holds for every
    unprefixed request, so no engine is built for it; it has no connect timeout of
    its own either, unlike `_space_entry_for`, because that pool is the process's own
    root connection, pre-pinged, not one more remote database per row."""
    try:
        with _get_session_factory(settings)() as root:
            user = UserRepository(root).get_by_email(email)
            return user.ruolo if user is not None and user.attivo else None
    except SQLAlchemyError:
        return None


# `exclude_defaults`: a row for an account is `{slug, ruolo}` exactly as it was before
# invitations joined the list, and only an `invito` row carries the three keys it adds.
@router.get("/spaces", response_model=list[IdentitySpace], response_model_exclude_defaults=True)
def spaces(
    email: IdentityEmailDep, registry: TenantsRegistryDep, settings: SettingsDep
) -> list[IdentitySpace]:
    """Every space this identity may enter, per §3's live scan (§7 decision B1): one
    bounded connection per tenant, so one unreachable space is silently absent from
    this one response rather than failing it -- the next visit tries again. A space
    where the address holds only an open invitation is listed too, as an `invito` row
    (REB-678).

    The root comes first, under its own name, when it has one and this email has an
    active row there (REB-583): it is nobody's registry row, so the scan below would
    never find it. A registry row that happens to carry the root's name is skipped:
    `split_tenant_prefix` already routes that name to the root, never to a space."""
    result: list[IdentitySpace] = []
    if settings.root_slug:
        root_role = _root_role_for(settings, email)
        if root_role is not None:
            result.append(IdentitySpace(slug=settings.root_slug, ruolo=root_role))
    slugs = [tenant.slug for tenant in TenantService(registry, settings).list()]
    invitations: list[IdentitySpace] = []
    for slug in slugs:
        if is_root_name(settings.root_slug, slug):
            continue
        entry = _space_entry_for(settings, slug, email)
        if entry is not None:
            # Spaces first, in the order they were always listed; the invitations after
            # them, so a row that is not a seat yet never sits above one that is.
            (invitations if entry.invito else result).append(entry)
    return [*result, *invitations]


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
    `homeAfterEntry()` already does after any other entry point.

    The root's own name (`PIGROCRM_ROOT_SLUG`) opens the root (REB-583): the same
    lookup in the root's own `users`, and the pair at the root's one jar, `path=/`
    (`tenancy.cookie_path`: the root logs in on the bare page and works under its
    name, and only `/` serves both), with the stale pair a browser may still hold at
    `/<root_slug>/` cleared the way `login` clears it, so it cannot shadow the fresh
    one on every request under the alias."""
    is_root = is_root_name(settings.root_slug, slug)
    factory = _get_session_factory(settings) if is_root else _tenant_session_factory(slug, settings)
    with factory() as space:
        user = UserRepository(space).get_by_email(email)
        if user is None or not user.attivo:
            raise NotFound("user", email)
        path = "/" if is_root else f"/{slug}/"
        if is_root:
            response.delete_cookie(ACCESS_COOKIE, path=f"/{slug}/")
            response.delete_cookie(REFRESH_COOKIE, path=f"/{slug}/")
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


@router.post("/invite/{slug}", response_model=InvitationDoor)
def open_invitation(slug: str, email: IdentityEmailDep, settings: SettingsDep) -> InvitationDoor:
    """The way into the invitation this identity holds in the space `slug` (REB-678),
    for the one whose mail never came: the chooser lists it, and this answers the
    acceptance page with a fresh token minted for the proven address, so the name
    prompt and the dead states are that page's own and nothing is accepted here. The
    token in the original mail dies with it, as in a resend, and the invitation does not
    run longer for having been looked at. 404 for a slug nobody registered, for the
    root (an installation's own name is not a space an invitation is held in) and for
    a space that holds none for this address, or one that has run out."""
    try:
        if is_root_name(settings.root_slug, slug):
            raise NotFound("invitation", slug)
        with _tenant_session_factory(slug, settings)() as space:
            raw = InvitationService(space).open_for(email)
    except NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_INVITATION) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Questo spazio non risponde in questo momento. Riprova tra poco.",
        ) from exc
    return InvitationDoor(url=f"/{slug}/app/invite?t={raw}")


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
