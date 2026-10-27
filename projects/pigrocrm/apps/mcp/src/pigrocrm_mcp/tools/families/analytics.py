"""Profit and loss, budget, ceilings and the year overview. Reads only."""

from collections.abc import Callable
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from mcp.server import MCPServer

from pigrocrm.core.analytics.schemas import BudgetQuery, CeilingSimulationQuery, PeriodPnlQuery
from pigrocrm_mcp.annotations import READ_ONLY
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.tools import (
    timetracking,
)
from pigrocrm_mcp.tools.invoices import parse_legal_entity_id
from pigrocrm_mcp.tools.params import Anno, BoundedLimit, IsoDateStr, OptionalFactor, iso_date


def register(mcp: MCPServer, context: McpContext, guard: Callable[..., Any]) -> None:
    # ---- analytics ---------------------------------------------------------
    # Reads only. `bind_time_to_invoice` has no tool because binding hours to a draft
    # is the step that determines their freezing at issue, and choosing *which* hours
    # to invoice is a commercial decision -- slice 3 §11 withdrew `issue_invoice` with
    # the same reasoning and this is the rung below it. `get_fiscal_estimate` has no
    # tool for a different reason: taxable income, contributions and estimated net for
    # a real person are the most sensitive data this product holds, and residual R10
    # leaves a PAT indistinguishable from full account access.

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_deal_pnl(deal_id: str) -> dict[str, Any]:
        """Conto economico di un deal: ricavi fatturati, costi diretti, costo del lavoro, margine e
        stato. `margine_percentuale` è `null` se i ricavi sono zero: non è 0%, vuol dire che non
        c'è ancora incasso. `valore_maturato` è una stima, non un ricavo.
        """
        return timetracking.get_deal_pnl(context, deal_id)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_period_pnl(
        da: str, a: str, customer_id: str | None = None, base: str = "emissione"
    ) -> dict[str, Any]:
        """Conto economico di periodo in due colonne: deal chiusi e deal in corso; il numero
        riportabile è il primo. `periodo_chiuso` e `voci_scritte_in_ritardo` dicono se la cifra
        può ancora cambiare. Le spese generali sono in una riga a parte, non ripartite.

        `base` sceglie la data che colloca il ricavo di una fattura nel periodo: `"emissione"`
        (predefinita, come il fisco) o `"competenza"` (`competenza_da`, o l'emissione se manca; per
        le fatture emesse in ritardo). Costi e ore restano alla propria data; una proforma non è
        mai ricavo.
        """
        return timetracking.get_period_pnl(
            context,
            PeriodPnlQuery(
                da=da,  # type: ignore[arg-type]
                a=a,  # type: ignore[arg-type]
                customer_id=UUID(customer_id) if customer_id else None,
                base=base,  # type: ignore[arg-type]
            ),
        )

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_unbilled_backlog() -> dict[str, Any]:
        """Le ore fatturabili non ancora su una fattura emessa, in totale e senza periodo: ore,
        valore maturato (ore × tariffa) e voci senza tariffa. Le voci senza tariffa contano
        nelle ore ma valgono zero (assente ≠ zero). Le ore su una bozza contano ancora. Il
        valore maturato non è un ricavo e non entra in nessun margine. Include le giornate di
        contratto non fatturate, solo se in stato approvato o successivo.
        """
        return timetracking.get_unbilled_backlog(context)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_budget_vs_actual(
        da: str,
        a: str,
        customer_id: str | None = None,
        limit: BoundedLimit = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Preventivo contro consuntivo per deal. Lo scostamento di valore è contro il preventivo
        pro-rata (preventivo × avanzamento ore). Una riga con `non_preventivato = true` non ha
        preventivo (non è zero) ed è esclusa dagli aggregati.
        """
        return timetracking.get_budget_vs_actual(
            context,
            BudgetQuery(
                da=da,  # type: ignore[arg-type]
                a=a,  # type: ignore[arg-type]
                customer_id=UUID(customer_id) if customer_id else None,
                limit=cast(int, limit),
                cursor=UUID(cursor) if cursor else None,
            ),
        )

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_ceiling_headroom(anno: Anno, azienda_id: str | None = None) -> dict[str, Any]:
        """Quanto spazio resta prima di ciascuna soglia attiva del pacchetto fiscale, sui ricavi
        incassati e reali dell'anno: `residuo` è soglia meno ricavi. Non richiede `admin`. Di
        una sola azienda: senza `azienda_id` è l'unica dello spazio, con più aziende va
        indicata. Un'azienda estera non ha soglie.
        """
        return timetracking.get_ceiling_headroom(context, anno, parse_legal_entity_id(azienda_id))

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_days_analysis(
        da: str | None = None, a: str | None = None, azienda_id: str | None = None
    ) -> dict[str, Any]:
        """Le giornate di un periodo: per mese e stato, tariffa reale contro listino, per cliente e
        contratto, e quante giornate sono finite in «lavorato senza approvazione» mese per mese.
        `da`/`a` (YYYY-MM-DD) vanno dati entrambi o nessuno (allora è il mese corrente).
        `azienda_id` filtra per azienda; senza, tutte. Non richiede `admin`.
        """
        return timetracking.get_days_analysis(context, da, a, parse_legal_entity_id(azienda_id))

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_year_overview(anno: Anno, azienda_id: str | None = None) -> dict[str, Any]:
        """L'anno di un'azienda come lo legge il commercialista: ricavi per regime e per
        trattamento fiscale, con riferimento normativo, e registro delle fatture emesse con
        bollo e rivalsa INPS a parte. `totali.imponibile` è il ricavo dell'anno. Non richiede
        `admin`. Senza `azienda_id` è l'unica dello spazio, con più aziende va indicata. Non
        restituisce file.
        """
        return timetracking.get_year_overview(context, anno, parse_legal_entity_id(azienda_id))

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def simulate_ceiling(
        anno: Anno,
        ore_preventivate: OptionalFactor = None,
        valore_preventivato: OptionalFactor = None,
        tariffa_oraria: OptionalFactor = None,
        azienda_id: str | None = None,
        contract_id: str | None = None,
        data_prevista: IsoDateStr = None,
    ) -> dict[str, Any]:
        """Il simulatore «ci sta?»: somma la stima di un deal non vinto ai ricavi reali dell'anno e
        rivaluta ogni soglia attiva, senza salvare. Serve `valore_preventivato`, oppure
        `ore_preventivate` con `tariffa_oraria`. `azienda_id` come in `get_ceiling_headroom`.
        Con `contract_id` la stima è letta anche contro il tetto sulla quota del cliente
        (`tetto_quota_cliente`) nell'anno di anniversario del contratto che contiene
        `data_prevista` (YYYY-MM-DD, default oggi): `tetto_contratto` dà quota prima e dopo e se
        `rientra`.
        """
        return timetracking.simulate_ceiling(
            context,
            anno,
            CeilingSimulationQuery(
                ore_preventivate=cast(Decimal, ore_preventivate)
                if ore_preventivate is not None
                else None,
                valore_preventivato=cast(Decimal, valore_preventivato)
                if valore_preventivato is not None
                else None,
                tariffa_oraria=cast(Decimal, tariffa_oraria)
                if tariffa_oraria is not None
                else None,
                contract_id=UUID(contract_id) if contract_id else None,
                data_prevista=iso_date(data_prevista),
            ),
            parse_legal_entity_id(azienda_id),
        )
