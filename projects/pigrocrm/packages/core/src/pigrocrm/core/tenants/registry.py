"""The engines a process holds, one per space in use, and the settings each space sees.

Lifted out of `apps/api/deps.py` (ORB-170) because the MCP server over HTTP needs the
same three answers the API needs on every request: which database a slug opens, what
the space's base settings are (the root's Google only when the root lends it, a scoped
public URL, documents on disk), and which `space_settings` rows lay over them. Both
adapters hold one instance of this class per process and ask it; neither reimplements
the caching.
"""

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

from pigrocrm.core.config import Settings
from pigrocrm.core.db import create_engine_from_settings, session_factory
from pigrocrm.core.db.scope import SPACE_SLUG_KEY
from pigrocrm.core.space_settings import SpaceSettingsService, apply_overrides
from pigrocrm.core.tenants.database import tenant_database_url, tenants_database_url
from pigrocrm.core.tenants.google import (
    oauth_state_prefix,
    space_mailbox_token_key,
    space_slack_token_key,
    space_token_key,
)
from pigrocrm.core.tenants.service import TenantService

OVERRIDES_TTL_SECONDS = 10.0
_ROOT = ""
# What one space may hold open in one process, and for how long (B-17). A space has a
# database of its own, so engines scale with the spaces in use, not with the traffic: a
# small pool each, the least recently used engine disposed past `MAX_OPEN_SPACES` and any
# that has gone `IDLE_SECONDS` unused. A request may hold two connections at once (the
# dashboards' snapshot session opens beside the request's own), so the pool is four with
# four more on overflow. The API and the MCP server each hold a registry: the connections
# kept open at rest are at most `2 * MAX_OPEN_SPACES * POOL_SIZE` (64), and the burst
# ceiling `2 * MAX_OPEN_SPACES * (POOL_SIZE + POOL_OVERFLOW)` (128) needs every open
# space at four concurrent requests; the root's and the registry's pools come on top.
MAX_OPEN_SPACES = 8
IDLE_SECONDS = 600.0
POOL_SIZE = 4
POOL_OVERFLOW = 4
POOL_RECYCLE_SECONDS = 1800


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
    `space_settings` lay over this (`apply_overrides`).

    Slack (REB-739) follows the same borrowing without the switch: a space gets the root's
    Slack app and a token key derived for it alone, and sends Slack the root's callback with
    its slug in the `state`, whenever the root has a Slack client id and a public URL; with
    either missing the space reads as Slack not configured."""
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
    # Slack is always the root's app, installed once per space, and never configured by
    # the space itself: a space either borrows it or has no Slack. Unlike Google there is
    # no switch for lending it, since there is no per-space client to prefer (REB-739).
    slack: dict[str, object]
    if settings.slack_client_id and settings.public_url:
        slack = {
            "slack_token_key": space_slack_token_key(settings.slack_token_key, slug),
            "slack_callback_base_url": settings.public_url.rstrip("/"),
            "slack_oauth_state_prefix": oauth_state_prefix(slug),
        }
    else:
        slack = {
            "slack_client_id": "",
            "slack_client_secret": "",
            "slack_token_key": "",
            "slack_callback_base_url": "",
            "slack_oauth_state_prefix": "",
        }
    return settings.model_copy(
        update={
            **google,
            **slack,
            # Mailboxes are core and always on: a space is sealed with a key derived for it
            # alone, and has none when the root has no valid key.
            "mailbox_token_key": space_mailbox_token_key(settings.mailbox_token_key, slug),
            "public_url": public_url,
            "storage_backend": "local",
        }
    )


@dataclass
class _Effective:
    """The settings one space last resolved: from which root `Settings`, when its rows were
    read, the rows themselves and what they made."""

    source: Settings
    checked_at: float
    overrides: dict[str, str]
    settings: Settings


class SpaceRegistry:
    """Engines built on first use, a bounded number kept; the tenants registry opened the
    same way.

    The lock is re-entrant, and it has to be: building a space's engine holds it while
    it asks the registry, and the registry's own first build takes the same lock. A
    plain `Lock` deadlocks the first request a space ever receives (found the hard
    way in `deps.py`, with a test suite that never finished).

    **A space's effective settings are resolved once per `overrides_ttl`** (PAPI-04):
    every request asks for them, and the rows of `space_settings` are a read of a one-row
    table, but laying them over the environment re-validates a whole `Settings`
    (about 4 ms of CPU). The answer is cached per slug, and while the rows read again are
    the rows it was built from it stays the very same object, so a caller may compare two
    answers with `is` to ask whether anything changed (the MCP server rebuilds a space's
    tools on that). `invalidate` after a write, so the page that just saved sees what it
    saved.

    **Engines are bounded** (B-17): see `MAX_OPEN_SPACES`. The registry never creates a
    database or a table: that is the boot's (`ensure_tenants_database`), and a request
    path that ran DDL would need the owner's credentials in every process that serves.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        overrides_ttl: float = OVERRIDES_TTL_SECONDS,
        max_open_spaces: int = MAX_OPEN_SPACES,
        idle_seconds: float = IDLE_SECONDS,
    ) -> None:
        self.settings = settings
        self._overrides_ttl = overrides_ttl
        self._max_open_spaces = max_open_spaces
        self._idle_seconds = idle_seconds
        self._lock = threading.RLock()
        self._root: sessionmaker[Session] | None = None
        self._registry: sessionmaker[Session] | None = None
        # slug -> (factory, last used), least recently used first.
        self._spaces: OrderedDict[str, tuple[sessionmaker[Session], float]] = OrderedDict()
        self._effective: dict[str, _Effective] = {}

    def registry_factory(self) -> sessionmaker[Session]:
        if self._registry is None:
            with self._lock:
                if self._registry is None:
                    self._registry = session_factory(
                        _small_engine(tenants_database_url(self.settings))
                    )
        return self._registry

    def session_factory(self, slug: str | None) -> sessionmaker[Session]:
        if slug is None:
            if self._root is None:
                with self._lock:
                    if self._root is None:
                        self._root = session_factory(create_engine_from_settings(self.settings))
                        self._root.configure(info={SPACE_SLUG_KEY: None})
            return self._root
        with self._lock:
            now = time.monotonic()
            self._evict_idle(now)
            entry = self._spaces.get(slug)
            if entry is not None:
                self._spaces[slug] = (entry[0], now)
                self._spaces.move_to_end(slug)
                return entry[0]
            registry = self.registry_factory()()
            try:
                # `NotFound` for a slug nobody registered: a wrong address, not a
                # server fault. Callers turn it into their own 404.
                tenant = TenantService(registry, self.settings).get(slug)
            finally:
                registry.close()
            factory = session_factory(
                _small_engine(tenant_database_url(self.settings, tenant.db_name))
            )
            factory.configure(info={SPACE_SLUG_KEY: slug})
            self._spaces[slug] = (factory, now)
            while len(self._spaces) > self._max_open_spaces:
                self._dispose(self._spaces.popitem(last=False)[1][0])
            return factory

    def _evict_idle(self, now: float) -> None:
        for slug in [s for s, (_, used) in self._spaces.items() if now - used > self._idle_seconds]:
            self._dispose(self._spaces.pop(slug)[0])

    @staticmethod
    def _dispose(factory: sessionmaker[Session] | None) -> None:
        """Closes the idle connections of an engine nobody holds on to any more. One a
        request still uses goes on working (its pool is rebuilt) and is collected with it."""
        if factory is None:
            return
        bind = factory.kw.get("bind")
        if isinstance(bind, Engine):
            bind.dispose()

    def effective_settings(
        self, slug: str | None, session: Session, root: Settings | None = None
    ) -> Settings:
        """What a request to `slug` reads: the environment as this space may see it
        (`space_base_settings`) with the rows of its `space_settings` laid over it.

        `root` is the process's settings as the caller holds them, the registry's own by
        default: a test that overrides `get_settings` per request passes its own and never
        reads another test's answer. `session` is on the space's database and is touched
        only when the cached answer is older than the TTL."""
        base = root or self.settings
        key = slug or _ROOT
        now = time.monotonic()
        entry = self._effective.get(key)
        if entry is not None and entry.source is not base:
            entry = None
        if entry is not None and now - entry.checked_at < self._overrides_ttl:
            return entry.settings
        overrides = SpaceSettingsService(session, space_base_settings(base, slug)).overrides()
        if entry is not None and entry.overrides == overrides:
            entry.checked_at = now
            return entry.settings
        resolved = apply_overrides(space_base_settings(base, slug), overrides)
        self._effective[key] = _Effective(base, now, overrides, resolved)
        return resolved

    def invalidate(self, slug: str | None) -> None:
        self._effective.pop(slug or _ROOT, None)

    def dispose(self) -> None:
        with self._lock:
            for factory in [self._root, self._registry, *(f for f, _ in self._spaces.values())]:
                self._dispose(factory)
            self._root = None
            self._registry = None
            self._spaces.clear()
            self._effective.clear()


def _small_engine(url: URL) -> Engine:
    return create_engine(
        url,
        pool_size=POOL_SIZE,
        max_overflow=POOL_OVERFLOW,
        pool_recycle=POOL_RECYCLE_SECONDS,
        pool_pre_ping=True,
        future=True,
    )
