import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session, sessionmaker

from pigrocrm.core.actor import Actor, Role
from pigrocrm.core.auth.pat_service import PAT_PREFIX, PatService
from pigrocrm.core.auth.repository import UserRepository
from pigrocrm.core.auth.tokens import decode_token
from pigrocrm.core.config import Settings, get_settings
from pigrocrm.core.errors import DomainError
from pigrocrm.core.space_settings import apply_overrides
from pigrocrm.core.storage import DocumentStorage, LocalFileStorage, storage_from_settings
from pigrocrm.core.tenants import SpaceRegistry, space_base_settings
from pigrocrm_api.tenancy import first_cookie, tenant_slug

ACCESS_COOKIE = "pigrocrm_access"
REFRESH_COOKIE = "pigrocrm_refresh"
# Root-scoped, at path=/, unconditionally (design 2026-09-23 §2, REB-376) -- the one
# cookie in this codebase that deliberately breaks `tenancy.cookie_path`'s
# "cookies live under a space's prefix" rule, because the entire point of this
# cookie is to survive moving between slugs.
IDENTITY_COOKIE = "pigrocrm_identity"

# One registry per process (spec 2026-09-08, lifted to core in ORB-170): the root's
# engine, every space's engine and the tenants registry, all built on first use from the
# first caller's settings. Callers with a request pass the settings dependency through,
# so a test's override of `get_settings` decides the server; callers without one
# (`_fresh_session`) get the process settings, which in production are the same object.
_registry: SpaceRegistry | None = None
_registry_lock = threading.Lock()

_storage: DocumentStorage | None = None
# The same guarantee as `_registry_lock`, for the same reason and with the same shape:
# one storage per process even when several requests reach a cold start at once. It
# matters more here than for the engine, because the object being cached holds a token
# cache -- two instances mean two `GoogleTokenClient`s and an extra OAuth round-trip
# per request that lost the race.
_storage_lock = threading.Lock()


def _space_registry(settings: Settings | None = None) -> SpaceRegistry:
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:  # a concurrent caller may have just finished building it
                _registry = SpaceRegistry(settings or get_settings())
    return _registry


def _get_session_factory(settings: Settings | None = None) -> sessionmaker[Session]:
    return _space_registry(settings).session_factory(None)


def reset_session_factories() -> None:
    """Forgets the root's engine along with every space's and the registry, for a test
    that points the whole process at another server through `get_settings`."""
    global _registry
    with _registry_lock:
        if _registry is not None:
            _registry.dispose()
        _registry = None


def get_tenants_registry_session(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Iterator[Session]:
    """A session on the registry database -- the list of spaces, never a space."""
    session = _space_registry(settings).registry_factory()()
    try:
        yield session
    finally:
        session.close()


TenantsRegistryDep = Annotated[Session, Depends(get_tenants_registry_session)]


def _tenant_session_factory(slug: str, settings: Settings) -> sessionmaker[Session]:
    return _space_registry(settings).session_factory(slug)


def _factory_for(request: Request, settings: Settings) -> sessionmaker[Session]:
    slug = tenant_slug(request)
    return _tenant_session_factory(slug, settings) if slug else _get_session_factory(settings)


def get_session(
    request: Request, settings: Annotated[Settings, Depends(get_settings)]
) -> Iterator[Session]:
    session = _factory_for(request, settings)()
    try:
        yield session
    finally:
        session.close()


SessionDep = Annotated[Session, Depends(get_session)]


def get_snapshot_session(
    request: Request, settings: Annotated[Settings, Depends(get_settings)]
) -> Iterator[Session]:
    """A second session per request, untouched by anything else in the request.

    Only the dashboards use it, and they need it. A dashboard is one transaction in
    `REPEATABLE READ` so that every figure on the page was true at one instant (spec
    §7.1), Postgres refuses to change the isolation level once a transaction has begun,
    and `DashboardService._open_snapshot` raises rather than silently degrading to
    `READ COMMITTED` -- where a card and its own drill-through can disagree and nothing
    about re-reading the code would say so.

    `get_actor` resolves the cookie by reading `users` **on `SessionDep`**, and that read
    autobegins a transaction. So a dashboard route taking `SessionDep` would raise on
    every single request: not a race, not a load-dependent bug, every request. Two
    sessions is the fix, and it is the same one the MCP adapter has used since Task 4A-1 --
    `__main__.py` resolves the PAT in its own short-lived session so the tool's session is
    untouched when the tool body runs.

    A distinct callable, therefore a distinct key in FastAPI's per-request dependency
    cache: a route asking for both gets two sessions, deliberately. The cost is one extra
    pooled connection for the life of the request, paid only by the routes that ask.
    """
    session = _factory_for(request, settings)()
    try:
        yield session
    finally:
        session.close()


SnapshotSessionDep = Annotated[Session, Depends(get_snapshot_session)]


def request_base_settings(
    request: Request, settings: Annotated[Settings, Depends(get_settings)]
) -> Settings:
    """The environment's settings as *this request* may see them, before the database
    has its say.

    A space does not inherit the root's Google: the client, its secret and the token key
    are blanked, so a space either configures its own (Impostazioni → Spazio) or has no
    Gmail and no Drive -- `gmail_configured` false, sections hidden, endpoints 409. Its
    public URL is the root's plus the slug, which is where Google will redirect to, and
    documents default to disk under the space's own folder. The root sees the
    environment untouched. Chained on `get_settings` so a test's override of that one
    dependency still reaches every route.
    """
    return space_base_settings(settings, tenant_slug(request))


BaseSettingsDep = Annotated[Settings, Depends(request_base_settings)]


def invalidate_space_settings(slug: str | None, settings: Settings | None = None) -> None:
    """After a write to `space_settings`: forget the cached rows and the storage built
    from them, for this database only.

    `settings` only decides which server the registry is built against if this call is
    the one that builds it, so a caller with a request's settings at hand passes them:
    in a test that overrides `get_settings`, the invalidation then reaches the same
    registry the request read from. Without them the process settings are used, which
    in production are the same object anyway."""
    _space_registry(settings).invalidate(slug)
    if slug is None:
        reset_storage_cache()
    else:
        with _storage_lock:
            _tenant_storages.pop(slug, None)


def get_request_settings(
    request: Request,
    base: BaseSettingsDep,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[Session, Depends(get_session)],
) -> Settings:
    """The settings every route reads: the environment as this request may see it,
    with the rows of this database's `space_settings` laid over it. The session is the
    request's own (FastAPI caches the dependency), so a test's override of
    `get_session` is where the rows come from too."""
    return apply_overrides(base, _space_registry(settings).overrides(tenant_slug(request), session))


SettingsDep = Annotated[Settings, Depends(get_request_settings)]


def _fresh_session() -> Session:
    """A session the caller owns and closes -- deliberately *not* the request's.

    This is what `get_storage` hands to `storage_from_settings`, and it is a callable
    rather than a `Session` for two reasons. The storage it belongs to outlives every
    request (one per process), so it cannot hold a session that must not; and it opens
    one only when it actually resolves an account, which is why a process with
    `PIGROCRM_STORAGE_BACKEND=gdrive` and nobody's Drive connected yet still starts.

    The request's own `SessionDep` would be wrong even where the lifetimes happened to
    line up: `LazyUserDriveStorage` closes what it opens, and it records a revoked grant
    by committing on its own behalf -- both of which would reach into the transaction
    the route is in the middle of.
    """
    return _get_session_factory()()


_tenant_storages: dict[str, DocumentStorage] = {}


def get_storage(request: Request, settings: SettingsDep) -> DocumentStorage:
    """One backend per process, chosen from settings and built once behind a lock.

    Once, and cached: the two Drive backends hold a token cache that only earns its keep
    across requests (`GoogleTokenClient` is documented as one per process), so building
    per request would turn every document read into an OAuth round-trip. `settings` is
    `get_settings`'s own `lru_cache`d singleton, so there is only ever one answer to
    cache -- and it therefore reads the settings of the *first* request to ask, which is
    also true of `_get_session_factory`'s engine and is what makes a settings change a
    restart rather than a surprise mid-process.

    Still a FastAPI dependency, and that is what makes it overridable: the tests replace
    it with a `LocalFileStorage` under `tmp_path` (`conftest.py`) rather than letting
    uploads write into the repository's own working tree. The cache is cleared through
    `reset_storage_cache()` below, which is what the tests that change the backend call.

    **What the lock is held across, on one configuration.** With a service account
    configured, `storage_from_settings` verifies the root folder before it returns
    (`GDriveStorage.verify_root_accessible`), which is a Drive round-trip with a retry
    budget -- so the first request to ask for a document backend holds `_storage_lock`
    across a network call, and any other request that arrives in that window waits for
    it. Kept deliberately: it happens once per process, every waiter needs that same
    answer before it can do anything with a document, and the alternative -- build
    outside the lock and swap the result in -- would pay a second verification round-trip
    and hand one of the two builds' token cache straight to the garbage collector, which
    is the exact waste this cache exists to prevent. The other two backends make no call
    here at all: `local` touches a `Path`, and the titolare's-own-Drive route reads
    nothing until its first operation (`LazyUserDriveStorage`).
    """
    slug = tenant_slug(request)
    if slug is not None:
        # A space's documents live on disk beside the root's, in their own folder,
        # unless the space configured Google and chose Drive (Impostazioni → Spazio):
        # then its own Drive account, resolved from its own database. Never the root's
        # storage: that Drive account is the root's, and `_fresh_session` opens the
        # root's database.
        storage = _tenant_storages.get(slug)
        if storage is None:
            with _storage_lock:
                storage = _tenant_storages.get(slug)
                if storage is None:
                    if settings.storage_backend == "gdrive":
                        factory = _tenant_session_factory(slug, settings)
                        storage = storage_from_settings(settings, session_factory=lambda: factory())
                    else:
                        root = Path(settings.storage_local_root) / "tenants" / slug
                        storage = LocalFileStorage(root)
                    _tenant_storages[slug] = storage
        return storage

    global _storage
    if _storage is None:
        with _storage_lock:
            if _storage is None:  # a concurrent caller may have just finished building it
                _storage = storage_from_settings(settings, session_factory=_fresh_session)
    return _storage


StorageDep = Annotated[DocumentStorage, Depends(get_storage)]


def reset_storage_cache() -> None:
    """Forgets the process's storage, so the next `get_storage` builds a new one.

    Exists for the tests, and named so that they no longer have to assign this module's
    private `_storage` to say what they mean. Two of them do say it -- the one that
    drives a whole Drive installation over HTTP, and the one that asserts the cache
    itself -- and a test that pokes a private global is a test that silently stops
    working the day the global is renamed or a second one joins it. This function is
    that knowledge, kept next to the cache it clears.

    Under `_storage_lock`, for the same reason `get_storage` builds under it: clearing
    the cache while another thread is between the check and the assignment would let
    that thread's build survive the reset. Nothing is rebuilt here -- the backend is
    resolved lazily by the next caller, which is also what makes this safe to call
    before a test has settled which settings the process should read.
    """
    global _storage
    with _storage_lock:
        _storage = None


def get_actor(request: Request, session: SessionDep, settings: SettingsDep) -> Actor:
    """Two credentials, one actor: the browser presents a JWT cookie, an agent presents
    a PAT. Everything downstream is identical.

    Precedence, in order -- deterministic, not a race between the two credentials:

    1. `Authorization: Bearer pgc_...` -- a header that looks like a PAT always wins
       over any cookie, even a valid one. If `PatService.resolve` fails (unknown,
       revoked, or the owning user deactivated), this raises 401 immediately; the
       cookie is never consulted, even when one is present and would otherwise work.
    2. Any other `Authorization` header -- absent, not `Bearer `, or a `Bearer` value
       that does not start with `pgc_` -- is ignored outright, and resolution falls
       back to the `ACCESS_COOKIE` cookie.
    """
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer ") and header[7:].startswith(PAT_PREFIX):
        try:
            # `settings`, so a space's own `mcp_full_access` (Impostazioni → Spazio) is
            # what stamps `Actor.full_access`, not the process environment's.
            return PatService(session, settings=settings).resolve(header[7:])
        except DomainError as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token non valido") from exc

    token = first_cookie(request, ACCESS_COOKIE)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Autenticazione richiesta")
    try:
        payload = decode_token(token, settings, expected_type="access")
    except DomainError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sessione scaduta") from exc

    try:
        user = UserRepository(session).get_active(payload.sub)
    except DomainError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Utente non attivo") from exc

    role: Role = user.ruolo  # type: ignore[assignment]
    return Actor(id=user.id, type="user", role=role)


ActorDep = Annotated[Actor, Depends(get_actor)]
# Same dependency, named for what it does at the call site: a router that depends on
# it is declaring "this endpoint requires an authenticated actor."
require_actor = get_actor
