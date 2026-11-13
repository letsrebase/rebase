"""`python -m rebase_mcp`: the server over stdio, as one admin.

Reads a personal token from `REBASE_MCP_TOKEN` and resolves it before the first message:
a process nobody has a token for does not start (REB-213). The token is resolved again
every minute while the process runs (`CachedAdmin`), so a revoked or expired token, or a
demoted owner, stops writing without a restart (REB-803). The HTTP transport is
`rebase_mcp.http`, served by its own `mcp` compose service and proxied at `/api/hub/mcp`
on the host (`apps/api` may not import `apps/mcp`); this one is for a developer's client
and for a shell on the host.
"""

import os
import sys
from contextlib import AbstractContextManager, closing

from sqlalchemy.orm import Session

from rebase_core.config import get_settings
from rebase_core.db import create_engine_from_settings, session_factory
from rebase_core.dependencies import build_dependencies
from rebase_core.errors import DomainError
from rebase_core.http import urllib_call
from rebase_mcp.actor import CachedAdmin
from rebase_mcp.server import build_server

TOKEN_VARIABLE = "REBASE_MCP_TOKEN"


def main() -> int:
    settings = get_settings()
    factory = session_factory(create_engine_from_settings(settings))

    def open_session() -> AbstractContextManager[Session]:
        return closing(factory())

    admin = CachedAdmin(open_session, os.environ.get(TOKEN_VARIABLE))
    try:
        admin.refresh()
    except DomainError:
        print(
            f"An admin's personal token is needed in {TOKEN_VARIABLE}: "
            "create one from the admin area («Agenti») or with `rebase createtoken`.",
            file=sys.stderr,
        )
        return 1
    deps = build_dependencies(settings, open_session)
    build_server(
        factory,
        admin,
        settings=settings,
        http=urllib_call,
        card_llm=deps.card_llm,
        sender=deps.sender,
        crm_leads=deps.crm_leads,
    ).run("stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
