"""Attività (commitments) and the calendar."""

from collections.abc import Callable
from typing import Any, cast
from uuid import UUID

from mcp.server import MCPServer

from pigrocrm_mcp.annotations import ADDITIVE, DESTRUCTIVE, READ_ONLY
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.tools import calendar as calendar_tools
from pigrocrm_mcp.tools.params import (
    BoundedLimit,
    IsoDateStr,
    TaskChanges,
    parse_legal_entity_id,
)


def register(mcp: MCPServer, context: McpContext, guard: Callable[..., Any]) -> None:
    # -- Attività e calendario (slice 10) -------------------------------------
    #
    # On the default surface, all of it: nothing here consumes a number, touches the
    # fiscal register or rewrites a rate, so none of it is one of the forbidden. The one
    # rule worth knowing is that closing a commitment is two different operations --
    # `complete_task` says it was done, `cancel_task` says it stopped mattering
    # -- and that neither deletes the row, because six months later the question is
    # *why*, not «where did it go».

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def create_task(
        title: str,
        due_date: IsoDateStr = None,
        note: str | None = None,
        customer_id: str | None = None,
        person_id: str | None = None,
        deal_id: str | None = None,
        invoice_id: str | None = None,
        assigned_to: str | None = None,
        custom_fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Crea un impegno con titolo e, se c'è, una scadenza. Lascia la scadenza vuota quando non
        c'è: non inventarla. Un'attività senza scadenza non è in ritardo e nel calendario sta in
        «senza scadenza».

        Si aggancia a un solo fra cliente, persona, deal e fattura, o a nessuno. `assigned_to` è
        l'id di una persona: lo trovi in `list_members`. Se lo ometti, l'attività è assegnata a chi
        possiede il token; passa l'id di un collega per assegnarla a lui, o `"none"` per
        lasciarla senza assegnatario di proposito.
        """
        return calendar_tools.create(
            context,
            {
                "title": title,
                "due_date": due_date,
                "note": note,
                "customer_id": UUID(customer_id) if customer_id else None,
                "person_id": UUID(person_id) if person_id else None,
                "deal_id": UUID(deal_id) if deal_id else None,
                "invoice_id": UUID(invoice_id) if invoice_id else None,
                "assigned_to": calendar_tools.assignee_of_default(context, assigned_to),
                "custom_fields": custom_fields or {},
            },
        )

    @mcp.tool(annotations=DESTRUCTIVE)
    @guard
    def update_task(task_id: str, changes: TaskChanges) -> dict[str, Any]:
        """Cambia un'attività: `title`, `note`, `due_date`, `assigned_to`, riferimento, campi
        personalizzati. Lo stato non si cambia da qui: usa `complete_task`,
        `cancel_task` o `reopen_task`.

        Qui `null` non svuota: per togliere la scadenza serve `scadenza_da_rimuovere: true`; per
        staccarla da cliente, deal, persona o fattura serve `riferimento_da_rimuovere: true`.
        `assigned_to` è l'id di una persona: lo trovi in `list_members`.
        """
        return calendar_tools.update(context, task_id, changes)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_task(task_id: str) -> dict[str, Any]:
        """Legge un'attività: titolo, note, scadenza, stato, riferimento."""
        return calendar_tools.get(context, task_id)

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def complete_task(task_id: str) -> dict[str, Any]:
        """Segna un'attività come fatta, con la data di oggi. Idempotente: completarla di nuovo non
        sposta la data. Un'attività annullata va prima riaperta.
        """
        return calendar_tools.complete(context, task_id)

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def cancel_task(task_id: str) -> dict[str, Any]:
        """Chiude un'attività che non serve più. Non è «fatta» e non cancella la riga. Reversibile
        con `reopen_task`.
        """
        return calendar_tools.cancel(context, task_id)

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def reopen_task(task_id: str) -> dict[str, Any]:
        """Riporta un'attività completata o annullata allo stato aperto e toglie la data di
        completamento.
        """
        return calendar_tools.reopen(context, task_id)

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def archive_task(task_id: str) -> dict[str, str]:
        """Archivia un'attività (reversibile con `restore_task`). Per l'attività sbagliata, non
        per il cambio di programma: quello è `cancel_task`.
        """
        return calendar_tools.archive(context, task_id)

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def restore_task(task_id: str) -> dict[str, Any]:
        """Ripristina un'attività archiviata."""
        return calendar_tools.restore(context, task_id)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def list_tasks(
        status: str | None = None,
        assigned_to: str | None = None,
        customer_id: str | None = None,
        person_id: str | None = None,
        deal_id: str | None = None,
        invoice_id: str | None = None,
        due_within: IsoDateStr = None,
        without_due_date: bool | None = None,
        limit: BoundedLimit = 50,
        cursor: str | None = None,
        sort: str | None = None,
        dir: str = "asc",
    ) -> dict[str, Any]:
        """Elenca gli impegni. `status`: `open`, `completed` o `cancelled`; senza, tornano tutti.
        `due_within` è inclusivo e esclude le attività senza scadenza: per quelle usa
        `senza_scadenza: true`. `sort`: `created_at` o `due_date`.
        """
        return calendar_tools.search(
            context,
            {
                "status": status,
                "assigned_to": UUID(assigned_to) if assigned_to else None,
                "customer_id": UUID(customer_id) if customer_id else None,
                "person_id": UUID(person_id) if person_id else None,
                "deal_id": UUID(deal_id) if deal_id else None,
                "invoice_id": UUID(invoice_id) if invoice_id else None,
                "due_within": due_within,
                "without_due_date": without_due_date,
                "limit": cast(int, limit),
                "cursor": cursor,
                "sort": sort,
                "dir": dir,
            },
        )

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_tasks_today(assigned_to: str | None = None, limit: BoundedLimit = 50) -> dict[str, Any]:
        """Cosa scade oggi, in quattro sezioni: `overdue`, `today_due`,
        `next_seven_days` e `without_due_date`. Solo attività aperte. Ogni sezione ha
        `total` (quante sono davvero) e `items` (le prime `limit`). Senza `assigned_to`
        risponde per tutto lo spazio; per «le mie» passa l'id di chi possiede il token.
        """
        return calendar_tools.today(
            context,
            {"assigned_to": UUID(assigned_to) if assigned_to else None, "limit": limit},
        )

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_calendar_month(
        month: str, all: bool = False, legal_entity_id: str | None = None
    ) -> dict[str, Any]:
        """Un mese in una lettura: ore per giorno con dettaglio per deal, attività in scadenza,
        giornate di contratto con stato (`worked_without_approval` se manca l'approvazione
        richiesta) e fatture emesse non incassate che scadono. `month` è `AAAA-MM`. Tornano solo
        i giorni con qualcosa; le attività senza scadenza stanno in `task_without_due_date`.

        Le ore sono di chi possiede il token; `tutti: true` legge tutto lo spazio. `legal_entity_id`
        filtra ore e fatture, non le attività.
        """
        return calendar_tools.month(context, month, all, parse_legal_entity_id(legal_entity_id))
