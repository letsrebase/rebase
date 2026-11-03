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
        """«Modelli AI», solo lettura: per ogni fornitore se la chiave c'è («configurata,
        finisce con …abcd», mai la chiave) e se è acceso; per ogni compito (`card`,
        `team_proposal`) fornitore, modello, livello di ragionamento, massimo di token,
        tetto giornaliero in euro, quanto ha speso oggi e perché non può partire, se non
        può; i prezzi per milione di token e la spesa del mese per compito e fornitore.
        `trail` sono le ultime 50 voci del registro della configurazione, dalla più
        recente, ognuna con l'amministratore: le chiavi, gli interruttori, i compiti e i
        prezzi cambiati, e ogni rigenerazione delle schede, quando è stata avviata (`source`
        `web` o `cli`, quante schede, se col Batch API) e quando è finita (quante scritte,
        non riuscite, scartate, il costo in euro e per ogni scheda non scritta il motivo,
        mai un nome o il testo di una scheda). Le chiavi si cambiano solo dalla pagina:
        nessun tool le scrive."""

        def read(s: Session) -> dict[str, Any]:
            service = AiConfigService(s, deps.team_settings())
            return {
                **service.read().model_dump(mode="json"),
                "trail": [entry.model_dump(mode="json") for entry in service.trail(50)],
            }

        return deps.call(read)

    @mcp.tool()
    def list_pigro_spaces() -> dict[str, Any]:
        """Gli spazi di PigroCRM come li mostra «Istanze Pigro» nell'area admin: slug,
        email di chi lo ha aperto, quando, l'indirizzo dello spazio e il membro dell'hub
        dietro quell'email quando ne ha una scheda. Letti dall'API del CRM con il token
        di registro, mai dal suo database; `total` li conta. Solo lettura."""
        if deps.settings is None or deps.http is None or not deps.settings.pigro_registry_token:
            raise not_configured("Il registro di Pigro", "REBASE_PIGRO_REGISTRY_TOKEN")
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
        """Quanti hanno scaricato la guida ai primi passi da freelance: `total` i
        download, `members` le persone diverse dietro, `total_members` quante potevano,
        `last_7_days` i download dell'ultima settimana e `recent` gli ultimi con
        nome ed email. Solo lettura."""
        return deps.run(lambda s: PerkService(s).guide_stats())

    @mcp.tool()
    def login_stats() -> dict[str, Any]:
        """Chi è entrato nella sua area e quando: `total` gli accessi con il link via
        email, `members` le persone diverse dietro, `total_members` quante hanno una
        scheda, `last_7_days` gli accessi dell'ultima settimana e `recent` gli
        ultimi con nome ed email. Ogni accesso in `recent` dice anche da dove è partito:
        `utm_source`, `utm_medium`, `utm_campaign`, `utm_content`, `utm_term`, `utm_id` e
        `origin`, letti dall'indirizzo della pagina di accesso (il link di una mail di
        contatto li porta), `null` quando la pagina non ne aveva. Ogni scheda letta da
        `get_talent` e da `get_freelancer` porta anche `logins` e `last_access`."""
        return deps.run(lambda s: LoginService(s).stats())
