"""Deals and their pipeline stage."""

from collections.abc import Callable
from typing import Any, cast
from uuid import UUID

from mcp.server import MCPServer

from pigrocrm.core.db import SortDirection
from pigrocrm.core.deals.schemas import DealListQuery
from pigrocrm_mcp.annotations import ADDITIVE, DESTRUCTIVE, READ_ONLY
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.tools import (
    deals,
)
from pigrocrm_mcp.tools.invoices import parse_legal_entity_id
from pigrocrm_mcp.tools.params import (
    BoundedLimit,
    DealChanges,
    IsoDateStr,
    OptionalMoney,
    OptionalProbabilita,
)


def register(mcp: MCPServer, context: McpContext, guard: Callable[..., Any]) -> None:
    # ---- deals -----------------------------------------------------------

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def create_deal(
        nome: str,
        customer_id: str,
        valore_previsto: OptionalMoney = None,
        probabilita: OptionalProbabilita = None,
        data_chiusura_prevista: IsoDateStr = None,
        note: str | None = None,
        ore_preventivate: OptionalMoney = None,
        valore_preventivato: OptionalMoney = None,
        custom_fields: dict[str, Any] | None = None,
        owner_id: str | None = None,
    ) -> dict[str, Any]:
        """Crea un deal per un cliente, nel primo stato della pipeline. `data_chiusura_prevista` in
        formato YYYY-MM-DD. `owner_id` è l'utente attivo che segue il deal e riceve i
        promemoria: omesso, è chi lo crea.
        """
        return deals.create(
            context,
            {
                "nome": nome,
                "customer_id": UUID(customer_id),
                "valore_previsto": valore_previsto,
                "probabilita": probabilita,
                "data_chiusura_prevista": data_chiusura_prevista,
                "note": note,
                "ore_preventivate": ore_preventivate,
                "valore_preventivato": valore_preventivato,
                "custom_fields": custom_fields or {},
                "owner_id": UUID(owner_id) if owner_id else None,
            },
        )

    @mcp.tool(annotations=DESTRUCTIVE)
    @guard
    def update_deal(deal_id: str, changes: DealChanges) -> dict[str, Any]:
        """Aggiorna un deal. Per cambiare stato usa `move_deal`. `owner_id` cambia chi segue il
        deal (utente attivo); `null` lo toglie.
        """
        return deals.update(context, deal_id, changes)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_deal(deal_id: str) -> dict[str, Any]:
        """Legge un deal."""
        return deals.get(context, deal_id)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def search_deals(
        search: str | None = None,
        customer_id: str | None = None,
        stage_id: str | None = None,
        custom: dict[str, Any] | None = None,
        azienda_id: str | None = None,
        limit: BoundedLimit = 50,
        cursor: str | None = None,
        sort: str | None = None,
        # `str`, not a Literal -- see `search_customers`.
        dir: str = "asc",
    ) -> dict[str, Any]:
        """Cerca deal per nome, cliente o stato di pipeline. `sort`: `created_at`, `updated_at`,
        `nome`. `azienda_id` filtra per azienda.
        """
        return deals.search(
            context,
            DealListQuery(
                search=search,
                customer_id=UUID(customer_id) if customer_id else None,
                stage_id=UUID(stage_id) if stage_id else None,
                custom=custom,
                azienda_id=parse_legal_entity_id(azienda_id),
                # cast: limit is `int | str` at runtime for the SDK-bypass reason
                # documented on `BoundedLimit` in `tools/params.py`; the *ListQuery schema this
                # feeds is what actually enforces (and coerces) "must be an int".
                limit=cast(int, limit),
                # An opaque string since slice 6 -- see `search_customers`.
                cursor=cursor,
                sort=sort,
                dir=cast(SortDirection, dir),
            ),
        )

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def move_deal(
        deal_id: str, stage_id: str | None = None, pipeline_stage_id: str | None = None
    ) -> dict[str, Any]:
        """Sposta un deal in un altro stato (`stage_id` o `pipeline_stage_id`, ne basta uno; elenco
        in `list_pipeline_stages`). Uno stato vinto/perso fissa la probabilità a 100/0.
        """
        return deals.move(context, deal_id, stage_id, pipeline_stage_id)

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def archive_deal(deal_id: str) -> dict[str, str]:
        """Archivia un deal (reversibile con `restore_deal`). Fallisce se ha ore registrate:
        fatturale o archiviale prima.
        """
        return deals.archive(context, deal_id)

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def restore_deal(deal_id: str) -> dict[str, Any]:
        """Ripristina un deal archiviato."""
        return deals.restore(context, deal_id)
