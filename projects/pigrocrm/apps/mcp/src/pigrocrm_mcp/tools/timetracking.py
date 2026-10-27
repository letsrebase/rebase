"""Thin calls into `TimeEntryService`/`CostService`/`CostCategoryService`/
`AnalyticsService`, like every other tool module -- see `tools/invoices.py`'s own
docstring for the shape this one mirrors.

An agent may record and read. It may not change what an already-recorded number means,
and it may not read the owner's tax position: `recalculate_rates`, `update_user_rates`,
`update_deal_rate`, the four cost-category writes, `close_period`, `reopen_period`,
`bind_time_to_invoice` and `get_fiscal_estimate` have deliberately no call-through here
at all, and `apps/mcp/tests/test_mcp_invoice_ban.py` fails the build if any of those
methods is ever reached from anywhere under `tools/`, not only from this file. Four, not
three, since `unarchive_cost_category` joined the ban: it is `archive_cost_category` in
the other direction and settles the same question -- which categories the CRM offers.
Reading is a different act, and `list_cost_categories` and `list_period_locks` are both
here: an agent may see which categories exist and which months are closed, and may
change neither. A tool that contained business logic would be logic the web app cannot
reach -- the failure this architecture exists to prevent. Every function here builds a
core schema from caller-supplied data *inside* the guarded call, so a bad value becomes
rendered guidance instead of a raw pydantic dump (see `tools/params.py`'s own note on
`WithJsonSchema` and `_guard`).
"""

from datetime import date
from typing import Annotated, Any
from uuid import UUID

from pydantic import Field, TypeAdapter

from pigrocrm.core.analytics.schemas import BudgetQuery, CeilingSimulationQuery, PeriodPnlQuery
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.timetracking.categories import CostCategoryService
from pigrocrm.core.timetracking.costs import CostService
from pigrocrm.core.timetracking.locks import PeriodLockService
from pigrocrm.core.timetracking.schemas import (
    ANNO_MAX,
    ANNO_MIN,
    CostCreate,
    CostListQuery,
    CostUpdate,
    TimeEntryCreate,
    TimeEntryListQuery,
    TimeEntryUpdate,
    TimerStart,
    TimerStop,
    TimerUpdate,
)
from pigrocrm.core.timetracking.service import TimeEntryService
from pigrocrm.core.timetracking.timer import TimerService
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.tools.changes import refuse_empty, refuse_nothing_given, report

# `list_locks` takes no schema of its own -- `anno` goes straight into a `WHERE` -- so
# the year is validated here, inside the guarded call, against the same bounds
# `PeriodLockCreate` declares. Through pydantic rather than a bare `int(...)` for the
# reason `tools/documents.py::_NUMERO` states: `int("scorso")` would be rendered to the
# agent as "non è un identificativo valido", and a non-numeric year reaching the query
# would be a raw DataError instead of guidance.
_ANNO: TypeAdapter[int] = TypeAdapter(Annotated[int, Field(ge=ANNO_MIN, le=ANNO_MAX)])


def log_time(context: McpContext, data: dict[str, Any]) -> dict[str, Any]:
    return (
        TimeEntryService(context.session)
        .create(TimeEntryCreate(**data), context.actor)
        .model_dump(mode="json")
    )


def update_time_entry(context: McpContext, entry_id: str, data: dict[str, Any]) -> dict[str, Any]:
    service = TimeEntryService(context.session)
    identifier = UUID(entry_id)
    changes = TimeEntryUpdate(**data)
    refuse_empty(data)
    before = service.get(identifier, context.actor)
    return report(before, service.update(identifier, changes, context.actor))


def archive_time_entry(context: McpContext, entry_id: str) -> dict[str, str]:
    TimeEntryService(context.session).soft_delete(UUID(entry_id), context.actor)
    return {"status": "archiviata", "entry_id": entry_id}


def restore_time_entry(context: McpContext, entry_id: str) -> dict[str, Any]:
    return (
        TimeEntryService(context.session)
        .restore(UUID(entry_id), context.actor)
        .model_dump(mode="json")
    )


def get_time_entry(context: McpContext, entry_id: str) -> dict[str, Any]:
    return (
        TimeEntryService(context.session).get(UUID(entry_id), context.actor).model_dump(mode="json")
    )


def list_time_entries(context: McpContext, query: TimeEntryListQuery) -> dict[str, Any]:
    page = TimeEntryService(context.session).list(query, context.actor)
    return {
        "items": [item.model_dump(mode="json") for item in page.items],
        "next_cursor": str(page.next_cursor) if page.next_cursor else None,
    }


def get_running_timer(context: McpContext) -> dict[str, Any] | None:
    timer = TimerService(context.session).current(context.actor)
    return timer.model_dump(mode="json") if timer is not None else None


def start_timer(context: McpContext, data: dict[str, Any]) -> dict[str, Any]:
    return (
        TimerService(context.session)
        .start(TimerStart(**data), context.actor)
        .model_dump(mode="json")
    )


def update_timer(context: McpContext, data: dict[str, Any]) -> dict[str, Any]:
    # `None` means «leave it» on this tool, so a call that names nothing has nothing to say.
    refuse_nothing_given(**data)
    service = TimerService(context.session)
    changes = TimerUpdate(**data)
    running = service.current(context.actor)
    # With no timer running the service's own refusal is the answer, and it comes before
    # there is anything to compare.
    return report(running, service.update(changes, context.actor))


def stop_timer(context: McpContext, data: dict[str, Any]) -> dict[str, Any]:
    return (
        TimerService(context.session).stop(TimerStop(**data), context.actor).model_dump(mode="json")
    )


def discard_timer(context: McpContext) -> dict[str, str]:
    TimerService(context.session).discard(context.actor)
    return {"status": "scartato"}


def get_deal_time_summary(context: McpContext, deal_id: str) -> dict[str, Any]:
    return (
        TimeEntryService(context.session)
        .deal_summary(UUID(deal_id), context.actor)
        .model_dump(mode="json")
    )


def describe_rates(context: McpContext, deal_id: str, user_id: str) -> dict[str, Any]:
    return (
        TimeEntryService(context.session)
        .describe_rates(UUID(deal_id), UUID(user_id), context.actor)
        .model_dump(mode="json")
    )


def create_cost(context: McpContext, data: dict[str, Any]) -> dict[str, Any]:
    return (
        CostService(context.session)
        .create(CostCreate(**data), context.actor)
        .model_dump(mode="json")
    )


def update_cost(context: McpContext, cost_id: str, data: dict[str, Any]) -> dict[str, Any]:
    service = CostService(context.session)
    identifier = UUID(cost_id)
    changes = CostUpdate(**data)
    refuse_empty(data)
    before = service.get(identifier, context.actor)
    return report(before, service.update(identifier, changes, context.actor))


def archive_cost(context: McpContext, cost_id: str) -> dict[str, str]:
    CostService(context.session).soft_delete(UUID(cost_id), context.actor)
    return {"status": "archiviato", "cost_id": cost_id}


def restore_cost(context: McpContext, cost_id: str) -> dict[str, Any]:
    return (
        CostService(context.session).restore(UUID(cost_id), context.actor).model_dump(mode="json")
    )


def get_cost(context: McpContext, cost_id: str) -> dict[str, Any]:
    return CostService(context.session).get(UUID(cost_id), context.actor).model_dump(mode="json")


def list_costs(context: McpContext, query: CostListQuery) -> dict[str, Any]:
    page = CostService(context.session).list(query, context.actor)
    return {
        "items": [item.model_dump(mode="json") for item in page.items],
        "next_cursor": str(page.next_cursor) if page.next_cursor else None,
    }


def list_period_locks(context: McpContext, anno: int | str | None) -> dict[str, Any]:
    """The months already closed. A read, and the answer to the refusal `log_time`
    raises: that `Conflict` names the one month it hit, which leaves an agent that has a
    week of entries to write guessing at the rest. `close_period`/`reopen_period` stay
    absent from this module -- seeing which months are closed is not deciding it."""
    locks = PeriodLockService(context.session).list_locks(
        anno=_ANNO.validate_python(anno) if anno is not None else None
    )
    return {"locks": [lock.model_dump(mode="json") for lock in locks]}


def list_cost_categories(context: McpContext, include_archived: bool) -> dict[str, Any]:
    categories = CostCategoryService(context.session).list_cost_categories(
        include_archived=include_archived
    )
    return {"categories": [c.model_dump(mode="json") for c in categories]}


# ---- analytics ------------------------------------------------------------------
# Seven reads, and only seven -- REB-373 added the fifth and sixth (ceiling headroom and
# its simulator) and REB-757 the seventh (the year register), the reason the comment
# now says "seven" rather than "four": a count
# that is never updated is worse than no count. `bind_time_to_invoice` and
# `get_fiscal_estimate` are absent by decision, not by omission -- see
# `tools/families/analytics.py`'s own note for the two (different) reasons, and
# `apps/mcp/tests/test_mcp_invoice_ban.py`, which scans every file in this package
# and fails the build if either method is ever called from one of them, whatever
# the tool that reaches it is called. Ceiling headroom and its simulator are absent
# from that ban on purpose: they are a revenue-versus-threshold figure, the same
# kind `get_period_pnl`/`get_budget_vs_actual` already expose, not the owner's tax
# position.


def get_deal_pnl(context: McpContext, deal_id: str) -> dict[str, Any]:
    return (
        AnalyticsService(context.session)
        .deal_pnl(UUID(deal_id), context.actor)
        .model_dump(mode="json")
    )


def get_period_pnl(context: McpContext, query: PeriodPnlQuery) -> dict[str, Any]:
    return (
        AnalyticsService(context.session).period_pnl(query, context.actor).model_dump(mode="json")
    )


def get_budget_vs_actual(context: McpContext, query: BudgetQuery) -> dict[str, Any]:
    return (
        AnalyticsService(context.session)
        .budget_vs_actual(query, context.actor)
        .model_dump(mode="json")
    )


def get_unbilled_backlog(context: McpContext) -> dict[str, Any]:
    return AnalyticsService(context.session).unbilled_backlog(context.actor).model_dump(mode="json")


def get_ceiling_headroom(
    context: McpContext, anno: int | str, azienda_id: UUID | None = None
) -> dict[str, Any]:
    """The headroom under every active ceiling of the configured pack (REB-352 §1.4),
    on the year's real paid revenue. Of one azienda (REB-631): the only one when none
    is named, required by name otherwise."""
    return (
        AnalyticsService(context.session)
        .ceiling_headroom(_ANNO.validate_python(anno), context.actor, azienda_id)
        .model_dump(mode="json")
    )


def simulate_ceiling(
    context: McpContext,
    anno: int | str,
    query: CeilingSimulationQuery,
    azienda_id: UUID | None = None,
) -> dict[str, Any]:
    """The "would this fit?" simulator (REB-352 §1.4): the synthetic addition `query`
    carries, re-evaluated against every active ceiling without saving anything. The
    azienda resolves as in `get_ceiling_headroom` (REB-631)."""
    return (
        AnalyticsService(context.session)
        .simulate_ceiling(_ANNO.validate_python(anno), query, context.actor, azienda_id)
        .model_dump(mode="json")
    )


def get_year_overview(
    context: McpContext, anno: int | str, azienda_id: UUID | None = None
) -> dict[str, Any]:
    """The year of one azienda as the accountant reads it (REB-757): revenue by regime
    and tax treatment, and the register of issued invoices with the stamp duty and the
    rivalsa apart. The file is the web app's: an MCP tool returns no bytes."""
    return (
        AnalyticsService(context.session)
        .year_overview(_ANNO.validate_python(anno), context.actor, azienda_id)
        .model_dump(mode="json")
    )


_DATE: TypeAdapter[date | None] = TypeAdapter(date | None)


def get_days_analysis(
    context: McpContext,
    da: str | None = None,
    a: str | None = None,
    azienda_id: UUID | None = None,
) -> dict[str, Any]:
    """The days of a period by month and state, the realised day rate and the trend of
    days worked without approval (REB-756)."""
    return (
        AnalyticsService(context.session)
        .days_analysis(
            context.actor, _DATE.validate_python(da), _DATE.validate_python(a), azienda_id
        )
        .model_dump(mode="json")
    )
