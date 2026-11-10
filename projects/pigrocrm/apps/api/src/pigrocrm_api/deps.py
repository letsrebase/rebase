import threading
from collections.abc import Iterator
from typing import Annotated, Any, Literal

from fastapi import Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session, sessionmaker

from pigrocrm.core.actor import Actor
from pigrocrm.core.ai.transport import HttpModelCall, ModelCall
from pigrocrm.core.auth.pat_service import PAT_PREFIX, PatService
from pigrocrm.core.auth.refresh_service import RefreshTokenService
from pigrocrm.core.auth.repository import UserRepository
from pigrocrm.core.auth.scope import actor_for
from pigrocrm.core.auth.tokens import ROOT_AUDIENCE, decode_token
from pigrocrm.core.config import Settings, get_settings
from pigrocrm.core.db.scope import bind_scope
from pigrocrm.core.errors import DomainError
from pigrocrm.core.modules import ModuleKey, require_modules
from pigrocrm.core.storage import DocumentStorage, storage_from_settings
from pigrocrm.core.storage.factory import space_storage_from_settings
from pigrocrm.core.tenants import SpaceRegistry, space_base_settings
from pigrocrm_api.ratelimit import (
    FREE_BEARER_FAILURES,
    bearer_key,
    log_unauthorized,
    record_failure,
    refuse_if_locked,
)
from pigrocrm_api.tenancy import first_cookie, tenant_slug

ACCESS_COOKIE = "pigrocrm_access"
REFRESH_COOKIE = "pigrocrm_refresh"
# Root-scoped, at path=/, unconditionally (REB-376): the one cookie that deliberately
# breaks `tenancy.cookie_path`'s "cookies live under a space's prefix" rule, because it
# exists to survive moving between slugs.
IDENTITY_COOKIE = "pigrocrm_identity"

# One registry per process (ORB-170): the root's engine, every space's engine and the
# tenants registry, built on first use from the first caller's settings. Callers with a
# request pass the settings dependency through, so a test's override of `get_settings`
# decides the server; callers without one get the process settings.
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
    session = registry_session_factory(settings)()
    try:
        yield session
    finally:
        session.close()


TenantsRegistryDep = Annotated[Session, Depends(get_tenants_registry_session)]


def space_session_factory(
    slug: str | None, settings: Settings | None = None
) -> sessionmaker[Session]:
    """The pooled session factory of the space `slug`, or of the root installation for
    `None`. For code that opens a space other than the request's own (the chooser, a
    signup, a link mail): never build an engine per call. A slug nobody registered raises
    `NotFound`."""
    return _space_registry(settings).session_factory(slug)


def registry_session_factory(settings: Settings | None = None) -> sessionmaker[Session]:
    """The pooled session factory of the registry database, for code that has no request
    dependency to ask for it."""
    return _space_registry(settings).registry_factory()


def _factory_for(request: Request, settings: Settings) -> sessionmaker[Session]:
    return space_session_factory(tenant_slug(request), settings)


def get_session(
    request: Request, settings: Annotated[Settings, Depends(get_settings)]
) -> Iterator[Session]:
    session = _factory_for(request, settings)()
    try:
        yield session
    finally:
        session.close()


SessionDep = Annotated[Session, Depends(get_session)]


def request_base_settings(
    request: Request, settings: Annotated[Settings, Depends(get_settings)]
) -> Settings:
    """The environment's settings as *this request* may see it, before the database has
    its say: `space_base_settings` decides what a space inherits from the root (Google
    only while the root lends its client, REB-394; its public URL; its storage folder).
    The root sees the environment untouched. Chained on `get_settings` so a test's
    override of that one dependency still reaches every route."""
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
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[Session, Depends(get_session)],
) -> Settings:
    """The settings every route reads: the environment as this request may see it
    (`request_base_settings`), with the rows of this database's `space_settings` laid over
    it. Resolved once per TTL and cached by the registry (`SpaceRegistry.effective_settings`),
    not rebuilt per request. The session is the request's own (FastAPI caches the
    dependency), so a test's override of `get_session` is where the rows come from too, and
    it is only touched when the cached answer has expired."""
    return _space_registry(settings).effective_settings(tenant_slug(request), session, settings)


SettingsDep = Annotated[Settings, Depends(get_request_settings)]


def _fresh_session() -> Session:
    """A session the caller owns and closes, deliberately not the request's: the storage
    that receives this callable outlives every request, opens a session only when it
    resolves an account (so a process with `gdrive` and no Drive connected still
    starts), and `LazyUserDriveStorage` commits and closes what it opens, which must not
    reach into a route's transaction."""
    return space_session_factory(None)()


_tenant_storages: dict[str, DocumentStorage] = {}


def get_storage(request: Request, settings: SettingsDep) -> DocumentStorage:
    """One backend per process, chosen from settings and built once behind a lock.

    Cached because the Drive backends hold a token cache that only pays across requests
    (`GoogleTokenClient` is one per process): building per request would turn every
    document read into an OAuth round-trip. It reads the settings of the first request to
    ask, so a settings change is a restart. Still a FastAPI dependency so the tests can
    replace it with a `LocalFileStorage` under `tmp_path`; they clear the cache through
    `reset_storage_cache()`.

    The first build holds `_storage_lock` across `verify_root_accessible`, a Drive
    round-trip, when a service account is configured. Kept: it happens once per process
    and every waiter needs the same answer, while building outside the lock would pay a
    second verification and discard one build's token cache."""
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
                    # Resolved per call: the registry may have disposed this space's engine
                    # since, and a captured factory would keep the old one.
                    storage = space_storage_from_settings(
                        settings,
                        slug,
                        session_factory=lambda: space_session_factory(slug, settings)(),
                    )
                    _tenant_storages[slug] = storage
        return storage

    global _storage
    if _storage is None:
        with _storage_lock:
            if _storage is None:  # a concurrent caller may have just finished building it
                _storage = storage_from_settings(settings, session_factory=_fresh_session)
    return _storage


StorageDep = Annotated[DocumentStorage, Depends(get_storage)]


def get_model_call() -> ModelCall:
    """The network the model's calls go through (design 2026-11-09, incoming documents inbox,
    § 3.2). A dependency so the tests replace it with a fake transport: a test never holds a
    key, and a call with no key configured never reaches it."""
    return HttpModelCall()


ModelCallDep = Annotated[ModelCall, Depends(get_model_call)]


def reset_storage_cache() -> None:
    """Forgets the process's storage, so the next `get_storage` builds a new one. For the
    tests that change the backend; under `_storage_lock`, like the build itself, so a
    build in flight cannot survive the reset."""
    global _storage
    with _storage_lock:
        _storage = None


def _bearer_pat(request: Request) -> str | None:
    """The PAT an `Authorization: Bearer pgc_...` header names, or None for any other
    header or none at all. One place, so `get_actor` and `callback_actor` cannot drift
    on which requests are an agent's."""
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer ") and header[7:].startswith(PAT_PREFIX):
        return header[7:]
    return None


def get_actor(request: Request, session: SessionDep, settings: SettingsDep) -> Actor:
    """Two credentials, one actor: the browser presents a JWT cookie, an agent a PAT.

    A header `Authorization: Bearer pgc_...` always wins over any cookie; if
    `PatService.resolve` fails the answer is 401 and the cookie is never consulted. Any
    other `Authorization` header is ignored and the `ACCESS_COOKIE` is read. The access
    token must have been minted for this request's space (`aud`): the root and every
    space share one signing secret, and the audience is what keeps a token from being
    carried from one to another. A token without an `aud`, minted before the claim
    existed, is refused like an expired one: it lives fifteen minutes and the SPA renews
    it from the refresh cookie on the 401 it just got, so nobody is signed out.

    Failed bearers are throttled per client address (`ratelimit.refuse_if_locked`); a token
    that resolves is never counted or refused by it."""
    pat = _bearer_pat(request)
    if pat is not None:
        key = bearer_key(request)
        try:
            # `settings`, so a space's own `mcp_full_access` is what stamps
            # `Actor.full_access`, not the process environment's.
            actor = PatService(session, settings=settings).resolve(pat)
        except DomainError as exc:
            # The lock is only for a client that keeps presenting tokens that do not
            # resolve: a valid token is never refused because of what else shares its
            # address, so the check comes after the resolution failed.
            refuse_if_locked(request, key)
            record_failure(key, free=FREE_BEARER_FAILURES)
            log_unauthorized(request, "bearer")
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token non valido") from exc
        # The scope the token's owner has, on the session every read of this request
        # runs on (REB-634): the database answers the rows this person may see.
        bind_scope(session, actor)
        return actor

    token = first_cookie(request, ACCESS_COOKIE)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Autenticazione richiesta")
    try:
        payload = decode_token(
            token,
            settings,
            expected_type="access",
            audience=tenant_slug(request) or ROOT_AUDIENCE,
        )
    except DomainError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sessione scaduta") from exc

    try:
        user = UserRepository(session).get_active(payload.sub)
    except DomainError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Utente non attivo") from exc

    actor = actor_for(user, "user")
    bind_scope(session, actor)
    return actor


def callback_actor(request: Request, session: Session, settings: Settings) -> Actor | None:
    """The actor a browser brings back from Google's consent screens, or None when it
    holds no live session here (REB-446).

    The callbacks are top-level navigations, so the SPA cannot renew the session first,
    and a person who spends a minute on Google's consent screen may return after the
    access cookie has run out. So: `get_actor`, and when it refuses the cookie, the
    refresh cookie of the same prefix, verified and not rotated (signed, of the refresh
    type, live in *this* database, user still active; `RefreshTokenService.is_live`).
    Nothing is issued: minting here would skip rotation's replay check, and the SPA the
    callback lands on renews the pair itself on its first 401. A session opened by a
    signup alone has no refresh cookie and is not rescued. A state is still redeemed
    against the id of the user returned here, in the database of the request's prefix, and
    a PAT that fails is still a 401: a header that names one is never answered from a
    cookie."""
    try:
        return get_actor(request, session, settings)
    except HTTPException as exc:
        if exc.status_code != status.HTTP_401_UNAUTHORIZED or _bearer_pat(request) is not None:
            raise
    token = first_cookie(request, REFRESH_COOKIE)
    if not token:
        return None
    try:
        payload = decode_token(token, settings, expected_type="refresh")
    except DomainError:
        return None
    if payload.jti is None or not RefreshTokenService(session).is_live(payload.jti, payload.sub):
        return None
    try:
        user = UserRepository(session).get_active(payload.sub)
    except DomainError:
        return None
    actor = actor_for(user, "user")
    bind_scope(session, actor)
    return actor


def get_snapshot_session(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    actor: Annotated[Actor, Depends(get_actor)],
) -> Iterator[Session]:
    """A second session per request, untouched by anything else in the request.

    Only the dashboards use it: a dashboard is one `REPEATABLE READ` transaction, Postgres
    refuses to change the isolation level once a transaction has begun, and `get_actor`
    reads `users` on `SessionDep`, which autobegins one. A distinct callable is a distinct
    key in FastAPI's dependency cache, so a route asking for both gets two sessions, at
    the cost of one extra pooled connection for the routes that ask. The actor's scope is
    bound here as on the request's own session (REB-634)."""
    session = _factory_for(request, settings)()
    bind_scope(session, actor)
    try:
        yield session
    finally:
        session.close()


SnapshotSessionDep = Annotated[Session, Depends(get_snapshot_session)]


ActorDep = Annotated[Actor, Depends(get_actor)]


def refuse_off_kind(settings: Settings, kind: str | None) -> None:
    """A route that takes the kind of record it is about (`entity_type`) refuses the kind of a
    module that is off with the same sentence the module's own routes answer."""
    if kind == "deal":
        require_modules(settings, "deals")


def _off_entity_type(
    settings: SettingsDep,
    entity_type: Annotated[Literal["organization", "person", "deal"] | None, Query()] = None,
) -> None:
    refuse_off_kind(settings, entity_type)


# For a route that reads `entity_type` from the query string.
off_entity_type_guard: Any = Depends(_off_entity_type)


def module_guard(*needed: ModuleKey) -> Any:
    """The one guard a route of a module carries (spec 2026-11-09, A2 and B2): a router
    lists it beside `get_actor`, a route that spans modules lists the ones it needs, and a
    route whose modules are not all on answers 404 with the sentence naming the first that
    is off. It reads the settings every request already reads, so a switch reaches it
    within `OVERRIDES_TTL_SECONDS` and no handler asks the question itself. Listed after
    `get_actor`, so a caller with no credential still gets 401 and learns nothing about
    which modules the space has on."""

    def check(settings: SettingsDep) -> None:
        require_modules(settings, *needed)

    return Depends(check)
