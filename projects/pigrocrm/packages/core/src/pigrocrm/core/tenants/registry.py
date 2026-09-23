"""One engine per space per process, and the settings each space sees.

Lifted out of `apps/api/deps.py` (ORB-170) because the MCP server over HTTP needs the
same three answers the API needs on every request: which database a slug opens, what
the space's base settings are (the root's Google only when the root lends it, a scoped
public URL, documents on disk), and which `space_settings` rows lay over them. Both
adapters hold one instance of this class per process and ask it; neither reimplements
the caching.
"""

import threading
import time

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from pigrocrm.core.config import Settings
from pigrocrm.core.db import create_engine_from_settings, session_factory
from pigrocrm.core.space_settings import SpaceSettingsService, apply_overrides
from pigrocrm.core.tenants.database import ensure_tenants_database, tenant_database_url
from pigrocrm.core.tenants.google import oauth_state_prefix, space_token_key
from pigrocrm.core.tenants.service import TenantService

OVERRIDES_TTL_SECONDS = 10.0
_ROOT = ""


def space_base_settings(settings: Settings, slug: str | None) -> Settings:
    """The environment's settings as a space may see them, before its database has its
    say. Its public URL is the root's plus the slug, and documents default to disk under
    the space's own folder. The root sees the environment untouched, and gets the very
    same object.

    Google depends on `google_shared_client`. Off, a space does not inherit the root's
    Google: the client, its secret and the token key are blanked, so a space either
    configures its own (Impostazioni → Spazio) or has no Gmail and no Drive. On, the
    space borrows the root's client and secret and whether the root declared it
    unverified, seals its refresh tokens with a key derived for it alone, and sends
    Google the root's callback with its slug in the `state` (`tenants/google.py`). A
    space that configures a client of its own still gets its own, since the rows of
    `space_settings` lay over this (`apply_overrides`)."""
    if slug is None:
        return settings
    public_url = f"{settings.public_url.rstrip('/')}/{slug}" if settings.public_url else ""
    if settings.google_shared_client and settings.google_client_id:
        google: dict[str, object] = {
            "google_token_key": space_token_key(settings.google_token_key, slug),
            "google_callback_base_url": settings.public_url.rstrip("/"),
            "google_oauth_state_prefix": oauth_state_prefix(slug),
        }
    else:
        google = {
            "google_client_id": "",
            "google_client_secret": "",
            "google_token_key": "",
            "google_app_unverified": False,
            "google_callback_base_url": "",
            "google_oauth_state_prefix": "",
        }
    return settings.model_copy(
        update={
            **google,
            "public_url": public_url,
            "storage_backend": "local",
        }
    )


class SpaceRegistry:
    """Engines built on first use and kept; the tenants registry opened the same way.

    The lock is re-entrant, and it has to be: building a space's engine holds it while
    it asks the registry, and the registry's own first build takes the same lock. A
    plain `Lock` deadlocks the first request a space ever receives (found the hard
    way in `deps.py`, with a test suite that never finished).

    Overrides are cached for `overrides_ttl` seconds per slug: every request asks for
    its settings, and a read of a one-row table on each of them is cheap but not free.
    `invalidate` after a write, so the page that just saved sees what it saved.
    """

    def __init__(self, settings: Settings, *, overrides_ttl: float = OVERRIDES_TTL_SECONDS) -> None:
        self.settings = settings
        self._overrides_ttl = overrides_ttl
        self._lock = threading.RLock()
        self._root: sessionmaker[Session] | None = None
        self._registry: sessionmaker[Session] | None = None
        self._spaces: dict[str, sessionmaker[Session]] = {}
        self._overrides: dict[str, tuple[float, dict[str, str]]] = {}

    def registry_factory(self) -> sessionmaker[Session]:
        if self._registry is None:
            with self._lock:
                if self._registry is None:
                    self._registry = session_factory(ensure_tenants_database(self.settings))
        return self._registry

    def session_factory(self, slug: str | None) -> sessionmaker[Session]:
        if slug is None:
            if self._root is None:
                with self._lock:
                    if self._root is None:
                        self._root = session_factory(create_engine_from_settings(self.settings))
            return self._root
        factory = self._spaces.get(slug)
        if factory is None:
            with self._lock:
                factory = self._spaces.get(slug)
                if factory is None:
                    registry = self.registry_factory()()
                    try:
                        # `NotFound` for a slug nobody registered: a wrong address, not a
                        # server fault. Callers turn it into their own 404.
                        tenant = TenantService(registry, self.settings).get(slug)
                    finally:
                        registry.close()
                    engine = create_engine(
                        tenant_database_url(self.settings, tenant.db_name),
                        pool_pre_ping=True,
                        future=True,
                    )
                    factory = session_factory(engine)
                    self._spaces[slug] = factory
        return factory

    def overrides(self, slug: str | None, session: Session) -> dict[str, str]:
        key = slug or _ROOT
        cached = self._overrides.get(key)
        now = time.monotonic()
        if cached is not None and now - cached[0] < self._overrides_ttl:
            return cached[1]
        rows = SpaceSettingsService(session, space_base_settings(self.settings, slug)).overrides()
        self._overrides[key] = (now, rows)
        return rows

    def effective_settings(self, slug: str | None, session: Session) -> Settings:
        return apply_overrides(
            space_base_settings(self.settings, slug), self.overrides(slug, session)
        )

    def invalidate(self, slug: str | None) -> None:
        self._overrides.pop(slug or _ROOT, None)

    def dispose(self) -> None:
        with self._lock:
            for factory in [self._root, self._registry, *self._spaces.values()]:
                if factory is None:
                    continue
                bind = factory.kw.get("bind")
                if isinstance(bind, Engine):
                    bind.dispose()
            self._root = None
            self._registry = None
            self._spaces.clear()
            self._overrides.clear()
