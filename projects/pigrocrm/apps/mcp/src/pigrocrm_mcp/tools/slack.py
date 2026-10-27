"""What an agent may do with the space's Slack connection: read how it stands (REB-739).

One tool, `describe_slack`, and it is the whole surface, for the reason `tools/drive.py`
gives for its own: diagnosing a connection costs Slack nothing and exercises no consent,
and an agent that cannot see whether the workspace is connected, revoked, or which users
are matched either treats a dead connection as live or cannot tell a person what to fix.
It reads the CRM's own record of Slack, never Slack itself, and **never the bot token**:
the answer is `SlackHealth`, which has no field a token could be in, in any form.

The writes are refused the way their Google twins are (`test_mcp_surface_coverage.py`):
installing the app is a consent given from a browser, and who in a third-party workspace a
user is, which channel the space's reminders are posted to and disconnecting are a
person's decisions on a settings screen.
"""

from collections.abc import Callable
from typing import Any

from mcp.server import MCPServer

from pigrocrm.core.config import Settings
from pigrocrm.core.slack.client import slack_client_for
from pigrocrm.core.slack.service import SlackService
from pigrocrm_mcp.context import McpContext


def register(
    mcp: MCPServer, context: McpContext, guard: Callable[..., Any], settings: Settings
) -> None:
    """Registered only when Slack is configured (`slack_configured`, `server.py`): not
    registered means not listed and not callable, absent and not broken."""

    @mcp.tool()
    @guard
    def describe_slack() -> dict[str, Any]:
        """Stato del collegamento Slack dello spazio: il workspace, se funziona, il
        canale dei promemoria dello spazio e, per ogni utente, se è stato riconosciuto in
        Slack (`matched` dall'email, `manual` a mano, `unmatched` se non c'è: non riceve
        nulla su Slack).

        Solo per un amministratore dell'intero spazio. `workspace.status` è `active`,
        `revoked` (Slack ha revocato l'accesso: `last_error` dice come rimediare) o
        `disconnected`. Nessun workspace collegato non è un errore. Collegare Slack, correggere
        un abbinamento e scegliere il canale li fa una persona, da Impostazioni → Slack."""
        service = SlackService(
            context.session, settings=settings, client=slack_client_for(settings)
        )
        return service.health(context.actor).model_dump(mode="json")
