"""Attività (commitments) and the calendar."""

from collections.abc import Callable
from typing import Any, cast
from uuid import UUID

from mcp.server import MCPServer

from pigrocrm_mcp.annotations import ADDITIVE, DESTRUCTIVE, READ_ONLY
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.tools import calendario as calendar_tools
from pigrocrm_mcp.tools.invoices import parse_legal_entity_id
from pigrocrm_mcp.tools.params import AttivitaChanges, BoundedLimit, IsoDateStr


def register(mcp: MCPServer, context: McpContext, guard: Callable[..., Any]) -> None:
    # -- Attività e calendario (slice 10) -------------------------------------
    #
    # On the default surface, all of it: nothing here consumes a number, touches the
    # fiscal register or rewrites a rate, so none of it is one of the forbidden. The one
    # rule worth knowing is that closing a commitment is two different operations --
    # `complete_attivita` says it was done, `cancel_attivita` says it stopped mattering
    # -- and that neither deletes the row, because six months later the question is
    # *why*, not «where did it go».

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def create_attivita(
        titolo: str,
        scadenza: IsoDateStr = None,
        note: str | None = None,
        customer_id: str | None = None,
        person_id: str | None = None,
        deal_id: str | None = None,
        invoice_id: str | None = None,
        assegnata_a: str | None = None,
        custom_fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Crea un impegno con titolo e, se c'è, una scadenza. Lascia la scadenza vuota quando non
        c'è: non inventarla. Un'attività senza scadenza non è in ritardo e nel calendario sta in
        «senza scadenza».

        Si aggancia a un solo fra cliente, persona, deal e fattura, o a nessuno. `assegnata_a` è
        l'id di una persona: lo trovi in `list_members`.
        """
        return calendar_tools.create(
            context,
            {
                "titolo": titolo,
                "scadenza": scadenza,
                "note": note,
                "customer_id": UUID(customer_id) if customer_id else None,
                "person_id": UUID(person_id) if person_id else None,
                "deal_id": UUID(deal_id) if deal_id else None,
                "invoice_id": UUID(invoice_id) if invoice_id else None,
                "assegnata_a": UUID(assegnata_a) if assegnata_a else None,
                "custom_fields": custom_fields or {},
            },
        )

    @mcp.tool(annotations=DESTRUCTIVE)
    @guard
    def update_attivita(attivita_id: str, changes: AttivitaChanges) -> dict[str, Any]:
        """Cambia un'attività: `titolo`, `note`, `scadenza`, `assegnata_a`, riferimento, campi
        personalizzati. Lo stato non si cambia da qui: usa `complete_attivita`,
        `cancel_attivita` o `reopen_attivita`.

        Qui `null` non svuota: per togliere la scadenza serve `scadenza_da_rimuovere: true`; per
        staccarla da cliente, deal, persona o fattura serve `riferimento_da_rimuovere: true`.
        `assegnata_a` è l'id di una persona: lo trovi in `list_members`.
        """
        return calendar_tools.update(context, attivita_id, changes)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_attivita(attivita_id: str) -> dict[str, Any]:
        """Legge un'attività: titolo, note, scadenza, stato, riferimento."""
        return calendar_tools.get(context, attivita_id)

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def complete_attivita(attivita_id: str) -> dict[str, Any]:
        """Segna un'attività come fatta, con la data di oggi. Idempotente: completarla di nuovo non
        sposta la data. Un'attività annullata va prima riaperta.
        """
        return calendar_tools.complete(context, attivita_id)

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def cancel_attivita(attivita_id: str) -> dict[str, Any]:
        """Chiude un'attività che non serve più. Non è «fatta» e non cancella la riga. Reversibile
        con `reopen_attivita`.
        """
        return calendar_tools.cancel(context, attivita_id)

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def reopen_attivita(attivita_id: str) -> dict[str, Any]:
        """Riporta un'attività completata o annullata allo stato aperto e toglie la data di
        completamento.
        """
        return calendar_tools.reopen(context, attivita_id)

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def archive_attivita(attivita_id: str) -> dict[str, str]:
        """Archivia un'attività (reversibile con `restore_attivita`). Per l'attività sbagliata, non
        per il cambio di programma: quello è `cancel_attivita`.
        """
        return calendar_tools.archive(context, attivita_id)

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def restore_attivita(attivita_id: str) -> dict[str, Any]:
        """Ripristina un'attività archiviata."""
        return calendar_tools.restore(context, attivita_id)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def list_attivita(
        stato: str | None = None,
        assegnata_a: str | None = None,
        customer_id: str | None = None,
        person_id: str | None = None,
        deal_id: str | None = None,
        invoice_id: str | None = None,
        scade_entro: IsoDateStr = None,
        senza_scadenza: bool | None = None,
        limit: BoundedLimit = 50,
        cursor: str | None = None,
        sort: str | None = None,
        dir: str = "asc",
    ) -> dict[str, Any]:
        """Elenca gli impegni. `stato`: `aperta`, `completata` o `annullata`; senza, tornano tutti.
        `scade_entro` è inclusivo e esclude le attività senza scadenza: per quelle usa
        `senza_scadenza: true`. `sort`: `created_at` o `scadenza`.
        """
        return calendar_tools.search(
            context,
            {
                "stato": stato,
                "assegnata_a": UUID(assegnata_a) if assegnata_a else None,
                "customer_id": UUID(customer_id) if customer_id else None,
                "person_id": UUID(person_id) if person_id else None,
                "deal_id": UUID(deal_id) if deal_id else None,
                "invoice_id": UUID(invoice_id) if invoice_id else None,
                "scade_entro": scade_entro,
                "senza_scadenza": senza_scadenza,
                "limit": cast(int, limit),
                "cursor": cursor,
                "sort": sort,
                "dir": dir,
            },
        )

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_attivita_oggi(
        assegnata_a: str | None = None, limit: BoundedLimit = 50
    ) -> dict[str, Any]:
        """Cosa scade oggi, in quattro sezioni: `in_ritardo`, `scadono_oggi`,
        `prossimi_sette_giorni` e `senza_scadenza`. Solo attività aperte. Ogni sezione ha
        `totale` (quante sono davvero) e `items` (le prime `limit`). Senza `assegnata_a`
        risponde per tutto lo spazio; per «le mie» passa l'id di chi possiede il token.
        """
        return calendar_tools.today(
            context,
            {"assegnata_a": UUID(assegnata_a) if assegnata_a else None, "limit": limit},
        )

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_calendar_month(
        mese: str, tutti: bool = False, azienda_id: str | None = None
    ) -> dict[str, Any]:
        """Un mese in una lettura: ore per giorno con dettaglio per deal, attività in scadenza,
        giornate di contratto con stato (`lavorato_senza_approvazione` se manca l'approvazione
        richiesta) e fatture emesse non incassate che scadono. `mese` è `AAAA-MM`. Tornano solo
        i giorni con qualcosa; le attività senza scadenza stanno in `attivita_senza_scadenza`.

        Le ore sono di chi possiede il token; `tutti: true` legge tutto lo spazio. `azienda_id`
        filtra ore e fatture, non le attività.
        """
        return calendar_tools.month(context, mese, tutti, parse_legal_entity_id(azienda_id))
