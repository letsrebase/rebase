"""Hours, the timer, costs and period locks."""

from collections.abc import Callable
from typing import Any, cast
from uuid import UUID

from mcp.server import MCPServer

from pigrocrm.core.timetracking.schemas import (
    CostListQuery,
    TimeEntryListQuery,
)
from pigrocrm_mcp.annotations import ADDITIVE, DESTRUCTIVE, READ_ONLY
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.tools import (
    timetracking,
)
from pigrocrm_mcp.tools.invoices import parse_legal_entity_id
from pigrocrm_mcp.tools.params import (
    BoundedLimit,
    CostChanges,
    HoursArg,
    IsoDateStr,
    MoneyArg,
    OptionalAnno,
    OptionalFactor,
    TimeEntryChanges,
)


def register(mcp: MCPServer, context: McpContext, guard: Callable[..., Any]) -> None:
    # ---- time tracking -----------------------------------------------------
    # An agent may record and read. It may not change what already-recorded numbers
    # mean. Eleven methods are therefore deliberately absent from this module and from
    # `tools/timetracking.py` -- `recalculate_rates`, `update_user_rates`,
    # `update_deal_rate`, the four cost-category writes (`unarchive_cost_category`
    # included: slice 4 §11 listed only the other three, but bringing a category back
    # is the same decision as archiving it, taken in the other direction),
    # `bind_time_to_invoice`, `close_period`, `reopen_period`, `get_fiscal_estimate` --
    # and
    # `apps/mcp/tests/test_mcp_invoice_ban.py` fails the build if a tool for any of
    # them appears anywhere under `tools/`, or if that list changes. The defence is
    # structural rather than permission-based because residual R10 is open: a PAT has
    # no scopes and inherits its owner's full role, so an admin token would pass any
    # authorisation check. Not registering the tool is the only mechanism that holds.

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def log_time(
        deal_id: str,
        user_id: str,
        data: str,
        ore: HoursArg,
        descrizione: str,
        fatturabile: bool = True,
        tariffa_applicata: OptionalFactor = None,
        costo_applicato: OptionalFactor = None,
        note_interne: str | None = None,
        custom_fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Registra ore su un deal. `data` in formato YYYY-MM-DD, non futura. `deal_id` deve essere
        un id reale: risolvi il progetto con `search_deals`, ore fatturabili sul cliente
        sbagliato emergono solo in fattura. La tariffa si congela sulla riga: `describe_rates`
        dice quale si applica; se non ce n'è, la voce resta senza tariffa.
        """
        return timetracking.log_time(
            context,
            {
                "deal_id": UUID(deal_id),
                "user_id": UUID(user_id),
                "data": data,
                "ore": ore,
                "descrizione": descrizione,
                "fatturabile": fatturabile,
                "tariffa_applicata": tariffa_applicata,
                "costo_applicato": costo_applicato,
                "note_interne": note_interne,
                "custom_fields": custom_fields or {},
            },
        )

    @mcp.tool(annotations=DESTRUCTIVE)
    @guard
    def update_time_entry(entry_id: str, changes: TimeEntryChanges) -> dict[str, Any]:
        """Aggiorna una voce di ore. Se è già su una fattura emessa, solo `note_interne` e i campi
        personalizzati sono modificabili.
        """
        return timetracking.update_time_entry(context, entry_id, changes)

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def archive_time_entry(entry_id: str) -> dict[str, str]:
        """Archivia una voce di ore (reversibile con `restore_time_entry`). Fallisce se è legata a
        una riga di fattura.
        """
        return timetracking.archive_time_entry(context, entry_id)

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def restore_time_entry(entry_id: str) -> dict[str, Any]:
        """Ripristina una voce di ore archiviata. Fallisce se il deal è archiviato: ripristina
        prima il deal.
        """
        return timetracking.restore_time_entry(context, entry_id)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_time_entry(entry_id: str) -> dict[str, Any]:
        """Legge una voce di ore, con il valore di riga già calcolato."""
        return timetracking.get_time_entry(context, entry_id)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def list_time_entries(
        deal_id: str | None = None,
        user_id: str | None = None,
        da: IsoDateStr = None,
        a: IsoDateStr = None,
        fatturabile: bool | None = None,
        fatturato: bool | None = None,
        azienda_id: str | None = None,
        limit: BoundedLimit = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Elenca le voci di ore, dalla più recente. `fatturato = false` dice quanto resta da
        fatturare. `azienda_id` filtra per azienda.
        """
        return timetracking.list_time_entries(
            context,
            TimeEntryListQuery(
                deal_id=UUID(deal_id) if deal_id else None,
                azienda_id=parse_legal_entity_id(azienda_id),
                user_id=UUID(user_id) if user_id else None,
                da=da,  # type: ignore[arg-type]
                a=a,  # type: ignore[arg-type]
                fatturabile=fatturabile,
                fatturato=fatturato,
                limit=cast(int, limit),
                cursor=UUID(cursor) if cursor else None,
            ),
        )

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_running_timer() -> dict[str, Any] | None:
        """Il timer in corso del titolare del token, o `null`: deal, descrizione e `started_at`. Le
        ore già registrate sono in `list_time_entries`.
        """
        return timetracking.get_running_timer(context)

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def start_timer(
        deal_id: str | None = None, descrizione: str = "", fatturabile: bool = True
    ) -> dict[str, Any]:
        """Avvia il cronometro. Uno solo alla volta: se ce n'è uno in corso fallisce, fermalo
        (`stop_timer`) o scartalo (`discard_timer`). Il deal si può scegliere dopo con
        `update_timer` o allo stop, ma deve essere reale: risolvilo con `search_deals`.
        """
        return timetracking.start_timer(
            context,
            {
                "deal_id": UUID(deal_id) if deal_id else None,
                "descrizione": descrizione,
                "fatturabile": fatturabile,
            },
        )

    @mcp.tool(annotations=DESTRUCTIVE)
    @guard
    def update_timer(
        deal_id: str | None = None,
        descrizione: str | None = None,
        fatturabile: bool | None = None,
    ) -> dict[str, Any]:
        """Cambia deal, descrizione o fatturabilità del timer in corso senza fermarlo."""
        return timetracking.update_timer(
            context,
            {
                "deal_id": UUID(deal_id) if deal_id else None,
                "descrizione": descrizione,
                "fatturabile": fatturabile,
            },
        )

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def stop_timer(
        deal_id: str | None = None, descrizione: str | None = None, data: str | None = None
    ) -> dict[str, Any]:
        """Ferma il timer e registra le ore come voce, con tariffa congelata come `log_time`.
        `data` (YYYY-MM-DD) è il giorno, oggi se omessa. Restituisce la voce creata.
        """
        return timetracking.stop_timer(
            context,
            {
                "deal_id": UUID(deal_id) if deal_id else None,
                "descrizione": descrizione,
                "data": data,
            },
        )

    @mcp.tool(annotations=DESTRUCTIVE)
    @guard
    def discard_timer() -> dict[str, str]:
        """Scarta il timer in corso senza registrare niente."""
        return timetracking.discard_timer(context)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_deal_time_summary(deal_id: str) -> dict[str, Any]:
        """Ore totali, ore da fatturare, valore delle ore non fatturate, costo del lavoro e stato
        del deal. Il ricavo non è qui: è la fattura.
        """
        return timetracking.get_deal_time_summary(context, deal_id)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def describe_rates(deal_id: str, user_id: str) -> dict[str, Any]:
        """Quale tariffa e quale costo verrebbero congelati su una nuova voce e da quale livello
        (`manuale`, `deal`, `utente`, `assente`). Chiamalo prima di `log_time`.
        """
        return timetracking.describe_rates(context, deal_id, user_id)

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def create_cost(
        category_id: str,
        data: str,
        importo: MoneyArg,
        descrizione: str,
        deal_id: str | None = None,
        fornitore: str | None = None,
        document_id: str | None = None,
        custom_fields: dict[str, Any] | None = None,
        azienda_id: str | None = None,
    ) -> dict[str, Any]:
        """Registra un costo. Senza `deal_id` è una spesa generale, nel conto economico di periodo
        e non ripartita sui deal. `importo` è il totale pagato, IVA inclusa; negativo è un
        rimborso; zero è rifiutato. Le categorie sono in `list_cost_categories`. `azienda_id`
        vale solo per una spesa generale (omesso: condivisa); un costo su un deal è dell'azienda
        del deal.
        """
        return timetracking.create_cost(
            context,
            {
                "category_id": UUID(category_id),
                "data": data,
                "importo": importo,
                "descrizione": descrizione,
                "deal_id": UUID(deal_id) if deal_id else None,
                "azienda_id": parse_legal_entity_id(azienda_id),
                "fornitore": fornitore,
                "document_id": UUID(document_id) if document_id else None,
                "custom_fields": custom_fields or {},
            },
        )

    @mcp.tool(annotations=DESTRUCTIVE)
    @guard
    def update_cost(cost_id: str, changes: CostChanges) -> dict[str, Any]:
        """Aggiorna un costo."""
        return timetracking.update_cost(context, cost_id, changes)

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def archive_cost(cost_id: str) -> dict[str, str]:
        """Archivia un costo (reversibile con `restore_cost`)."""
        return timetracking.archive_cost(context, cost_id)

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def restore_cost(cost_id: str) -> dict[str, Any]:
        """Ripristina un costo archiviato."""
        return timetracking.restore_cost(context, cost_id)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_cost(cost_id: str) -> dict[str, Any]:
        """Legge un costo."""
        return timetracking.get_cost(context, cost_id)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def list_costs(
        deal_id: str | None = None,
        solo_generali: bool = False,
        category_id: str | None = None,
        da: IsoDateStr = None,
        a: IsoDateStr = None,
        azienda_id: str | None = None,
        limit: BoundedLimit = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Elenca i costi, dal più recente. `solo_generali = true` mostra solo le spese senza deal.
        `azienda_id` filtra per azienda; omesso, tutti, comprese le spese condivise senza
        azienda.
        """
        return timetracking.list_costs(
            context,
            CostListQuery(
                deal_id=UUID(deal_id) if deal_id else None,
                azienda_id=parse_legal_entity_id(azienda_id),
                solo_generali=solo_generali,
                category_id=UUID(category_id) if category_id else None,
                da=da,  # type: ignore[arg-type]
                a=a,  # type: ignore[arg-type]
                limit=cast(int, limit),
                cursor=UUID(cursor) if cursor else None,
            ),
        )

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def list_cost_categories(include_archived: bool = False) -> dict[str, Any]:
        """Elenca le categorie di costo. Si creano e si archiviano dall'app."""
        return timetracking.list_cost_categories(context, include_archived)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def list_period_locks(anno: OptionalAnno = None) -> dict[str, Any]:
        """Elenca i mesi chiusi, dal più recente. Un mese chiuso rifiuta ogni scrittura di ore e
        costi datata in quel mese: leggi qui prima di registrare voci vecchie.
        """
        return timetracking.list_period_locks(context, anno)
