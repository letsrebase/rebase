"""Tasks (commitments) and the calendar."""

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
    # -- Tasks and calendar (slice 10) -------------------------------------
    #
    # On the default surface, all of it: nothing here consumes a number, touches the
    # fiscal register or rewrites a rate, so none of it is one of the forbidden. The one
    # rule worth knowing is that closing a commitment is two different operations --
    # `complete_task` says it was done, `cancel_task` says it stopped mattering
    # -- and that neither deletes the row, because six months later the question is
    # *why*, not "where did it go".

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
        """Create a commitment with a title and, if there is one, a due date. Leave the due date
        empty
        when there is none: do not invent it. A task without a due date is not late and in the
        calendar it sits under «without due date».

        It attaches to only one of customer, person, deal and invoice, or to none. `assigned_to` is
        a person's id: find it in `list_members`. If you omit it, the task is assigned to whoever
        owns the token; pass a colleague's id to assign it to them, or `"none"` to deliberately
        leave it unassigned.
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
        """Change a task: `title`, `note`, `due_date`, `assigned_to`, reference, custom
        fields. The status cannot be changed from here: use `complete_task`,
        `cancel_task` or `reopen_task`.

        Here `null` does not clear: to remove the due date use `due_date_to_remove: true`; to
        detach it from customer, deal, person or invoice use `reference_to_remove: true`.
        `assigned_to` is a person's id: find it in `list_members`.
        """
        return calendar_tools.update(context, task_id, changes)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_task(task_id: str) -> dict[str, Any]:
        """Read a task: title, notes, due date, status, reference."""
        return calendar_tools.get(context, task_id)

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def complete_task(task_id: str) -> dict[str, Any]:
        """Mark a task as done, with today's date. Idempotent: completing it again does not
        move the date. A cancelled task must be reopened first.
        """
        return calendar_tools.complete(context, task_id)

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def cancel_task(task_id: str) -> dict[str, Any]:
        """Close a task that is no longer needed. It is not «done» and it does not delete the row.
        Reversible with `reopen_task`.
        """
        return calendar_tools.cancel(context, task_id)

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def reopen_task(task_id: str) -> dict[str, Any]:
        """Return a completed or cancelled task to the open state and remove the completion
        date.
        """
        return calendar_tools.reopen(context, task_id)

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def archive_task(task_id: str) -> dict[str, str]:
        """Archive a task (reversible with `restore_task`). For a task created by mistake, not
        for a change of plan: that is `cancel_task`.
        """
        return calendar_tools.archive(context, task_id)

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def restore_task(task_id: str) -> dict[str, Any]:
        """Restore an archived task."""
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
        """List commitments. `status`: `open`, `completed` or `cancelled`; without it, all are
        returned.
        `due_within` is inclusive and excludes tasks without a due date: for those use
        `without_due_date: true`. `sort`: `created_at` or `due_date`.
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
        """What is due today, in four sections: `overdue`, `today_due`,
        `next_seven_days` and `without_due_date`. Open tasks only. Each section has
        `total` (how many there really are) and `items` (the first `limit`). Without `assigned_to`
        it answers for the whole space; for «mine» pass the id of whoever owns the token.
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
        """A month in one read: hours per day with detail per deal, tasks falling due,
        contract days with status (`worked_without_approval` if the required approval is missing)
        and issued invoices not yet collected that fall due. `month` is `YYYY-MM`. Only days with
        something are returned; tasks without a due date are in `task_without_due_date`.

        Hours are those of whoever owns the token; `all: true` reads the whole space.
        `legal_entity_id` filters hours and invoices, not tasks.
        """
        return calendar_tools.month(context, month, all, parse_legal_entity_id(legal_entity_id))
