import contextvars
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from pigrocrm.core.actor import Actor
from pigrocrm.core.storage import DocumentStorage

SessionProvider = Callable[[], Session]
ActorProvider = Callable[[], Actor]


@dataclass(frozen=True)
class McpContext:
    """Injected rather than imported, so tests drive the server in-process with a
    rolled-back transaction instead of spawning a subprocess.

    `storage` is a plain value, not a provider like `session_provider`/
    `actor_provider`: one backend per process is exactly what the API's own
    `get_storage` dependency already establishes (`pigrocrm_api.deps`). Unlike the
    session, there is no equivalent reason for storage to be re-resolved per call --
    see `ScopedSessionProvider` below for why the session, unlike storage, now needs
    one instance per logical call (Task 4A-1, residual R1).
    """

    session_provider: SessionProvider
    actor_provider: ActorProvider
    storage: DocumentStorage

    @property
    def session(self) -> Session:
        return self.session_provider()

    @property
    def actor(self) -> Actor:
        return self.actor_provider()


_CURRENT_SESSION: contextvars.ContextVar[Session] = contextvars.ContextVar("mcp_session")


class ScopedSessionProvider:
    """One `Session` per logical MCP call, not one per process.

    Closes residual R1: the process previously built a single `Session` and passed
    `lambda: session` to `build_server`, shared by every tool call for the whole
    process lifetime. The SDK dispatches sync tool calls concurrently (a thread
    pool) and coroutine tools on an event loop, and ten concurrent writes against
    that one shared `Session` were measured, at the time, as zero successes and
    zero rows written -- unusable for `log_time` (slice 4's `log_time` is a write
    tool and the centre of its agentic surface).

    `contextvars`, not `threading.local`: a `ContextVar` is the only primitive that
    is correct for both dispatch styles above -- each task and each thread sees its
    own binding, and `Context.run` copying does not leak one call's session into
    another's.

    `__call__` refuses outside a scope instead of quietly opening a session. A
    session nobody closes is the failure this class exists to remove, and returning
    one from an unscoped read would reintroduce exactly that leak.
    """

    def __init__(self, factory: sessionmaker[Session]) -> None:
        self._factory = factory

    def __call__(self) -> Session:
        try:
            return _CURRENT_SESSION.get()
        except LookupError as exc:
            raise RuntimeError(
                "no MCP session scope is active: every tool and resource must run "
                "inside ScopedSessionProvider.scope()"
            ) from exc

    def new_session(self) -> Session:
        """A session the *caller* owns and closes, outside any scope.

        Not a second way to get the call's session -- the opposite. The document storage
        (`LazyUserDriveStorage`, when the titolare's own Drive is the document store)
        resolves its account by opening a session, reading one row and closing it again,
        at each operation; it must therefore be given something that opens a session it
        may close, which `__call__` deliberately is not. Handing it the call's own
        session would have the storage close the session the tool is still running in.

        A method here rather than the `sessionmaker` passed around separately, because
        `build_server` already knows this object and nothing else in the adapter should
        need to learn what a `sessionmaker` is. `server.py` reaches it the same way it
        reaches `scope` -- with `getattr` -- so a plain callable provider (this package's
        own test fixture) stays a plain callable.
        """
        return self._factory()

    @contextmanager
    def scope(self) -> Iterator[Session]:
        session = self._factory()
        token = _CURRENT_SESSION.set(session)
        try:
            yield session
        finally:
            _CURRENT_SESSION.reset(token)
            session.close()


class TokenActorProvider:
    """The actor a personal access token names, resolved again on every call (REB-634).

    The stdio server used to resolve its token once and hand the same actor to every
    call for the life of the process: a scope narrowed, a role lowered or a token
    revoked meanwhile never reached a client that stayed connected. The HTTP transport
    resolves per request; this is the stdio equivalent. Inside a tool's scope it reads
    on that call's own session, so the actor and the rows it then sees are one
    transaction's; outside one, which is where the analytics' identity callback runs
    (`analytics.identity_for`), it opens a session of its own and closes it, rather
    than refusing. `resolve` stamps `last_used_at`, hence the commit.
    """

    def __init__(self, sessions: ScopedSessionProvider, token: str) -> None:
        self._sessions = sessions
        self._token = token

    def __call__(self) -> Actor:
        from pigrocrm.core.auth.pat_service import PatService

        try:
            session = self._sessions()
        except RuntimeError:
            with self._sessions.scope() as own:
                actor = PatService(own).resolve(self._token)
                own.commit()
                return actor
        actor = PatService(session).resolve(self._token)
        session.commit()
        return actor
