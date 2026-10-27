"""What an agent may do with Google Drive today: diagnose it, and nothing else.

This module is one tool, `describe_drive_account`, and it is the whole surface for a
deliberate reason -- 9B ships the credential and the folder configuration, not a single
byte read from Drive. There is nothing yet for an agent to fetch, so there is nothing
yet to gate behind `mcp_full_access`; 9C is where reading a document arrives, and where
that question gets asked again.

**Why this one tool is not privileged.** One Google OAuth client issues both the Gmail
grant and this one (`gmail_configured` gates both, and the tool is registered right
after `gmail_tools.register` in the same block for exactly that reason), and diagnosing
a credential's state costs no quota and exercises no consent -- the same reasoning
`describe_gmail_account` already rests on in `tools/gmail.py`. An agent that cannot see
whether Drive is connected, expiring or revoked either treats a dead credential as live
or, worse, has no way to tell a person what needs fixing. Reading that state is not a
capability over Drive; it is a capability over the CRM's own record of Drive, exactly as
`describe_gmail_account` is a capability over the CRM's record of Gmail and not over the
mailbox itself.

**Why nothing else is here yet.** `GoogleDriveOAuthService.start/complete/disconnect`
and `GoogleDriveAccountService.set_roots` are refused for the same reasons their Gmail
twins are (see `test_mcp_surface_coverage.py`'s exclusions): consenting to and
configuring a third-party grant are a person's decisions, taken from a browser redirect
or a settings screen, not an agent's. `usable` and `mark_revoked` are internal gates and
system-only bookkeeping, never called with an actor performing an operation. None of
that changes here -- this module adds the one read `health()` already made possible and
touches nothing else.
"""

from collections.abc import Callable
from typing import Any

from mcp.server import MCPServer

from pigrocrm.core.config import Settings
from pigrocrm.core.drive.account import GoogleDriveAccountService
from pigrocrm_mcp.annotations import READ_ONLY
from pigrocrm_mcp.context import McpContext


def register(
    mcp: MCPServer, context: McpContext, guard: Callable[..., Any], settings: Settings
) -> None:
    """Registered only when `gmail_configured(settings)` -- see `server.py`, right after
    `gmail_tools.register`. Not registered means not listed and not callable: absent,
    not broken, exactly as `tools/gmail.py`'s own docstring puts it."""

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def describe_drive_account() -> dict[str, Any]:
        """Stato della credenziale Google Drive collegata. `banner` distingue revocato, in
        scadenza, scaduto, autorizzazione mancante: nessuno si risolve riprovando, ricollegare
        Drive lo fa una persona da Impostazioni → Drive. Nessun account collegato non è un
        errore.
        """
        health = GoogleDriveAccountService(context.session, settings=settings).health(context.actor)
        return health.model_dump(mode="json")
