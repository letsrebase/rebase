"""Pipeline stages, the timeline of any entity and the global search."""

from collections.abc import Callable
from typing import Any, cast
from uuid import UUID

from mcp.server import MCPServer

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.fields.schemas import EntityType
from pigrocrm.core.pipeline.service import PipelineService
from pigrocrm.core.search.schemas import PER_CLASS_LIMIT, SearchQuery
from pigrocrm_mcp.annotations import READ_ONLY
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.tools import search as search_tools
from pigrocrm_mcp.tools.invoices import parse_legal_entity_id
from pigrocrm_mcp.tools.params import BoundedLimit, SearchLimite


def register(mcp: MCPServer, context: McpContext, guard: Callable[..., Any]) -> None:
    # ---- shared ------------------------------------------------------------

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def list_pipeline_stages() -> dict[str, Any]:
        """Elenca gli stati della pipeline, con il tipo (open/won/lost)."""
        stages = PipelineService(context.session).list()
        return {"stages": [stage.model_dump(mode="json") for stage in stages]}

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_timeline(
        entity_type: EntityType, entity_id: str, limit: BoundedLimit = 50
    ) -> dict[str, Any]:
        """Cronologia di un'entità: per ogni voce chi, quando e da dove. `actor_type`: `user` dal
        web, `mcp` da un agente col token del membro, `rebase`, `system` da un'automazione (che
        nomina chi l'ha attivata in `triggered_by_name`).
        """
        entries = ActivityService(context.session).timeline(
            entity_type, UUID(entity_id), limit=cast(int, limit)
        )
        return {"entries": [entry.model_dump(mode="json") for entry in entries]}

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def search_everything(
        termine: str, limite: SearchLimite = PER_CLASS_LIMIT, azienda_id: str | None = None
    ) -> dict[str, Any]:
        """Cerca in tutto il CRM con una chiamata: clienti (ragione sociale, P.IVA, codice fiscale,
        email), persone (nome, cognome, email), deal (nome), documenti (titolo), fatture
        (causale, numero) e attività (titolo e note, anche chiuse). Accetta anche un frammento
        in mezzo a una parola («34567» trova la P.IVA 01234567890). Un numero di fattura si
        scrive «2026/7», «7/2026» o «007»: trova quella fattura, senza anno in ogni anno.

        Servono almeno 3 caratteri. Dà fino a `limite` risultati per classe e il conteggio reale;
        se `totale_e_un_minimo` è true il conteggio è un minimo e il resto sta nell'elenco della
        singola entità. `azienda_id` restringe clienti, persone (via il cliente), deal, documenti e
        fatture a quell'azienda, ma non le attività, che non ne hanno; omesso, cerca in tutto lo
        spazio. Non cerca contratti, costi, proposte né posta.
        """
        # `termine` is a bare `str` with no `Annotated` bound, following the
        # runtime-permissive / schema-only-strict convention of `tools/params.py`: the
        # length check happens
        # inside `SearchQuery`, inside the guarded call, so a two-character term produces a
        # domain error the agent can act on instead of an SDK rejection whose wording is
        # not ours.
        return search_tools.search_everything(
            context,
            SearchQuery(
                termine=termine,
                limite=cast(int, limite),
                azienda_id=parse_legal_entity_id(azienda_id),
            ),
        )
