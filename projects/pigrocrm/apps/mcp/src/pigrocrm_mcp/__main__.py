import os
import sys

from pigrocrm.core.auth.pat_service import PatService
from pigrocrm.core.config import get_settings
from pigrocrm.core.db import create_engine_from_settings, session_factory
from pigrocrm.core.errors import DomainError
from pigrocrm_mcp import analytics
from pigrocrm_mcp.context import ScopedSessionProvider, TokenActorProvider
from pigrocrm_mcp.server import build_server

PAT_ENV_VAR = "PIGROCRM_TOKEN"


def main() -> int:
    token = os.environ.get(PAT_ENV_VAR)
    if not token:
        print(
            f"{PAT_ENV_VAR} non impostato. Genera un token dalla UI in Impostazioni → Token.",
            file=sys.stderr,
        )
        return 1

    engine = create_engine_from_settings(get_settings())
    provider = ScopedSessionProvider(session_factory(engine))

    # The PAT is resolved at start-up, so a bad token is one line on stderr and no
    # server, and again on every call: the role, the active flag and, since REB-634,
    # the azienda scope travel on the actor, and a process that kept the actor it
    # started with would keep a scope its person no longer has, for as long as the
    # client stays connected (CodeRabbit on PR #513). The HTTP transport already
    # resolves per request. One read of two small tables per call, inside the call's
    # own session, which the guard binds before the tool body runs.
    with provider.scope() as bootstrap:
        try:
            PatService(bootstrap).resolve(token)
            bootstrap.commit()
        except DomainError as exc:
            print(f"Token non valido: {exc.message}", file=sys.stderr)
            return 1

    # `finally`, so the last tool call's event leaves before the process does: the
    # adapter schedules captures on the server's loop, and `run` returning tears that
    # loop down. Without a key there is no client and `shutdown` does nothing.
    try:
        build_server(provider, TokenActorProvider(provider, token)).run("stdio")
    finally:
        analytics.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
