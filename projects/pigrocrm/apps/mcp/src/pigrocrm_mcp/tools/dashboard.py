"""The thin half. Everything that matters happened in `DashboardService`.

Shaped exactly like `tools/search.py` and `tools/automations.py`: resolve the service on
the context's session, call it, `model_dump(mode="json")`.

**The session this receives must have no transaction in progress.** `DashboardService`
opens one `REPEATABLE READ` transaction so that every figure is read at one instant, and
Postgres refuses to set the isolation level once a transaction has begun -- so the service
raises rather than degrading silently to `READ COMMITTED`, where a card and its
drill-through can disagree. In production that is satisfied for free: `ScopedSessionProvider`
opens one `Session` per logical MCP call and `__main__.py` resolves the PAT once at
start-up, in its own short-lived session, so nothing has touched this one before the tool
body runs. A caller wiring `build_server` with a plain `lambda: shared_session` -- which is
what this package's own `server` fixture does -- gets the loud `RuntimeError` instead of a
wrong number.

Registered by Task B8 rather than by Task B12, which owns 6B's surface, for the reason
`tools/__init__.py` gives beside `describe_automations`: the moment the service exists,
both coverage tests demand that each of its public methods be a tool or a named exclusion,
and deferring one to a later task is exactly the placeholder Task A11 cleared out of the
taxonomy.
"""

from typing import Any

from pigrocrm.core.dashboard.schemas import PeriodoQuery
from pigrocrm.core.dashboard.service import DashboardService
from pigrocrm_mcp.context import McpContext


def get_commercial_dashboard(context: McpContext, query: PeriodoQuery) -> dict[str, Any]:
    return (
        DashboardService(context.session)
        .get_commercial_dashboard(query, context.actor)
        .model_dump(mode="json")
    )


def get_economic_dashboard(context: McpContext, query: PeriodoQuery) -> dict[str, Any]:
    """Registered by Task C4 rather than by Task C7, which owns 6C's surface, for the same
    reason Task B8 registered `get_commercial_dashboard` above: the moment the service
    method exists, both coverage tests demand it be a tool or a named exclusion, and "a
    later task decides" is the placeholder Task A11 spent a whole task deleting. **Task C7
    must not register a second `get_economic_dashboard`** -- it still owns the REST route
    and the adapter tests.
    """
    return (
        DashboardService(context.session)
        .get_economic_dashboard(query, context.actor)
        .model_dump(mode="json")
    )


def get_operational_dashboard(context: McpContext) -> dict[str, Any]:
    """No query object, because §6's dashboard takes no period: its figures are the current
    week and a backlog, which are the two things that make no sense in the past.

    Registered by Task C6 for the reason recorded on `get_economic_dashboard` above. **Task
    C7 must not register a second `get_operational_dashboard`.**

    `DashboardService(context.session)` with no second argument reads the *environment's*
    `concentrazione_soglia_preferita` for the fourth signal (REB-371), not a space's own
    `space_settings` override -- `McpContext` carries no `Settings` at all, so every other
    settings-dependent service reached through this transport already has the same gap
    (`InvoiceService(context.session, context.storage)` a few files over reads its
    `solleciti_*` thresholds from the environment for the identical reason). Not this
    ticket's gap to close: giving the MCP transport a space's effective `Settings` is a
    change to `McpContext` itself, for every tool that reads one, not one signal's tool.
    """
    return (
        DashboardService(context.session)
        .get_operational_dashboard(context.actor)
        .model_dump(mode="json")
    )


def get_receivables_dashboard(context: McpContext) -> dict[str, Any]:
    """No period, like the operational one: a receivable is owed today whatever window the
    reader has in mind (slice 8 §2, REB-329)."""
    return (
        DashboardService(context.session)
        .get_receivables_dashboard(context.actor)
        .model_dump(mode="json")
    )
