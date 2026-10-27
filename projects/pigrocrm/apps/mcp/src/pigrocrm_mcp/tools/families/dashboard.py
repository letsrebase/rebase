"""The four dashboards."""

from collections.abc import Callable
from typing import Any

from mcp.server import MCPServer

from pigrocrm.core.dashboard.schemas import PeriodoQuery
from pigrocrm_mcp.annotations import READ_ONLY
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.tools import dashboard as dashboard_tools
from pigrocrm_mcp.tools.invoices import parse_legal_entity_id
from pigrocrm_mcp.tools.params import IsoDateStr


def register(mcp: MCPServer, context: McpContext, guard: Callable[..., Any]) -> None:
    # ---- dashboard ---------------------------------------------------------
    # No resource counterpart, deliberately (§11.1): a resource is addressed by a URI and a
    # dashboard is a question with a period.

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_commercial_dashboard(
        da: IsoDateStr = None, a: IsoDateStr = None, azienda_id: str | None = None
    ) -> dict[str, Any]:
        """Quadro commerciale: pipeline per stato, deal chiusi nel periodo con tasso di
        conversione, offerte inviate in attesa, chiusure previste nei 30 giorni successivi,
        offerte accettate il cui deal non è vinto. `da` e `a` (`YYYY-MM-DD`) vanno insieme;
        senza, mese corrente. `valore_ponderato` è una stima, non fatturato. `azienda_id` filtra
        per azienda; senza, tutte sommate.
        """
        # `IsoDateStr`, the alias this file already uses for a date parameter: the runtime
        # type stays `str | None` so a malformed date is rejected inside `PeriodoQuery`,
        # inside the guarded call, and the agent gets this project's own rendered guidance
        # instead of an SDK rejection whose wording is not ours -- while `list_tools()`
        # still advertises `format: date`. `model_validate` on a dict rather than
        # `PeriodoQuery(da=da, a=a)`: the two do the same coercion, and the keyword form
        # would be a type error to a reader (and to mypy) that says nothing true about the
        # runtime.
        return dashboard_tools.get_commercial_dashboard(
            context, PeriodoQuery.model_validate({"da": da, "a": a, "azienda_id": azienda_id})
        )

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_economic_dashboard(
        da: IsoDateStr = None, a: IsoDateStr = None, azienda_id: str | None = None
    ) -> dict[str, Any]:
        """Conto economico del periodo: ricavi, costi diretti, costo del lavoro e margine in due
        colonne, `chiusi` e `in_corso`, da non sommare; più spese generali, maturato non
        fatturato, `da_incassare` e `scaduto` (senza periodo). `da` e `a` (`YYYY-MM-DD`) vanno
        insieme; senza, mese corrente. Nessuna stima fiscale. `azienda_id` filtra per azienda;
        senza, tutte sommate.
        """
        return dashboard_tools.get_economic_dashboard(
            context, PeriodoQuery.model_validate({"da": da, "a": a, "azienda_id": azienda_id})
        )

    # Registered by Task C6, with the same reasoning. No parameters at all: §6's dashboard
    # takes no period, so there is none for an agent to get wrong. Task C7 must not
    # register a second one.

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_receivables_dashboard(azienda_id: str | None = None) -> dict[str, Any]:
        """Scadenziario incassi: fatture emesse non incassate in sei fasce di scadenza (la somma è
        `totale`, uguale a `da_incassare`), atteso per mese, esposizione per cliente, fatture
        scadute con solleciti inviati. Nessun periodo: è il dovuto di oggi. Per i solleciti c'è
        `list_payment_reminder_candidates`. `azienda_id` filtra per azienda; senza, tutte
        sommate.
        """
        return dashboard_tools.get_receivables_dashboard(context, parse_legal_entity_id(azienda_id))

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_operational_dashboard(azienda_id: str | None = None) -> dict[str, Any]:
        """Cosa c'è da fare adesso: ore giorno per giorno della settimana corrente (anche i giorni
        senza ore), arretrato da fatturare, quattro segnali di incoerenza (fatturato ma non
        vinto, vinto ma da fatturare, scaduto non incassato, concentrazione cliente) e ultime
        attività. Nessun periodo. `azienda_id` filtra per azienda; senza, tutte insieme.
        """
        return dashboard_tools.get_operational_dashboard(context, parse_legal_entity_id(azienda_id))
