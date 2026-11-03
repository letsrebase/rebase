"""The tools that read the hub as a whole: the «Modelli AI» configuration (never a key), the
Pigro spaces, and the guide's and the logins' statistics."""

from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy.orm import Session

from rebase_core.ai_config import AiConfigService
from rebase_core.logins import LoginService
from rebase_core.perks import PerkService
from rebase_core.pigro import PigroRegistry
from rebase_core.pigro_door import PigroUnavailable
from rebase_mcp.tools_common import (
    Deps,
    not_configured,
)


def register(mcp: MCPServer, deps: Deps) -> None:
    """Declares this family's tools on `mcp`."""

    @mcp.tool()
    def list_ai_config() -> dict[str, Any]:
        """«Modelli AI», read only: for each provider whether the key is there («configurata,
        finisce con …abcd», never the key) and whether it is on; for each task (`card`,
        `team_proposal`) provider, model, reasoning level, maximum tokens,
        daily cap in euros, how much it spent today and why it cannot start, if it
        cannot; the prices per million tokens and the month's spend per task and provider.
        `trail` is the last 50 entries of the configuration log, most recent first, each
        with the administrator: the keys, switches, tasks and prices changed, and every
        regeneration of the cards, when it was started (`source` `web` or `cli`, how many
        cards, whether with the Batch API) and when it finished (how many written, failed,
        discarded, the cost in euros and for each card not written the reason, never a name
        or the text of a card). Keys are changed only from the page: no tool writes them."""

        def read(s: Session) -> dict[str, Any]:
            service = AiConfigService(s, deps.team_settings())
            return {
                **service.read().model_dump(mode="json"),
                "trail": [entry.model_dump(mode="json") for entry in service.trail(50)],
            }

        return deps.call(read)

    @mcp.tool()
    def list_pigro_spaces() -> dict[str, Any]:
        """The PigroCRM spaces as «Istanze Pigro» shows them in the admin area: slug,
        email of who opened it, when, the space's address and the hub member behind that
        email when they have a card. Read from the CRM's API with the registry token,
        never from its database; `total` counts them. Read only."""
        if deps.settings is None or deps.http is None or not deps.settings.pigro_registry_token:
            raise not_configured("The Pigro registry", "REBASE_PIGRO_REGISTRY_TOKEN")
        registry = PigroRegistry(deps.settings, deps.http)
        session = deps.factory()
        try:
            return registry.list_spaces(session).model_dump(mode="json")
        except PigroUnavailable as exc:
            raise ToolError(str(exc)) from exc
        finally:
            session.close()

    @mcp.tool()
    def guide_stats() -> dict[str, Any]:
        """How many downloaded the first-steps guide for freelancers: `total` the
        downloads, `members` the distinct people behind them, `total_members` how many could
        have, `last_7_days` the downloads of the last week and `recent` the latest with
        name and email. Read only."""
        return deps.run(lambda s: PerkService(s).guide_stats())

    @mcp.tool()
    def login_stats() -> dict[str, Any]:
        """Who entered their area and when: `total` the logins with the link via
        email, `members` the distinct people behind them, `total_members` how many have a
        card, `last_7_days` the logins of the last week and `recent` the
        latest with name and email. Every login in `recent` also says where it started:
        `utm_source`, `utm_medium`, `utm_campaign`, `utm_content`, `utm_term`, `utm_id` and
        `origin`, read from the address of the login page (the link in an outreach email
        carries them), `null` when the page had none. Every card read by
        `get_talent` and `get_freelancer` also carries `logins` and `last_access`."""
        return deps.run(lambda s: LoginService(s).stats())
