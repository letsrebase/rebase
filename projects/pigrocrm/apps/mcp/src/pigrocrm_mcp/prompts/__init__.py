"""Registration for §10's four prompts.

`MCPServer.prompt()` infers the arguments from the signature exactly as `tool()` does --
verified against the installed `mcp==2.2.0` (`prompts/base.py::Prompt.from_function` runs
the same `func_metadata` the tool manager runs), not assumed from the documentation -- and
the function may receive the `Context`, so a prompt can read the database like a tool. None
of these four needs one.

Two consequences of that shared machinery are load-bearing here:

  * `functools.wraps` inside the guard's one body (`_guarded` in `server.py`) is what makes
    the inferred schema the *guarded* function's, not the wrapper's bare
    `(*args, **kwargs)`. It is already there for the tools and `_protocol_guard` shares that
    body, so the prompts inherit it -- and `test_mcp_prompts.py` pins the inferred
    arguments so a future guard that lost `wraps` breaks here as loudly as it would there;
  * `Prompt.render` re-raises `MCPError` untouched and replaces everything else with
    `ValueError(f"Error rendering prompt {name}")`, with no text after the name since mcp
    2.2 (REB-451). That is why the guard passed here is `_protocol_guard` and not the tools'
    `_guard`: it raises a domain error as an `MCPError` whose message is
    `to_agent_message`'s diagnosis and whose code is `-32602` for a missing record and
    `DOMAIN_REFUSAL` otherwise, so the assistant reads the domain's own sentence, and a
    crash keeps its text on the server. It is also why these prompts raise domain errors and
    do not render an apology in Italian: the guard already turns one into guidance, and a
    second rendering would be a second vocabulary for the same failure.
"""

from collections.abc import Callable
from typing import Any

from mcp.server import MCPServer

from pigrocrm.core.config import Settings
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.modules import prompt_available
from pigrocrm_mcp.prompts import dashboards as dashboard_prompts
from pigrocrm_mcp.prompts import organization as organization_prompts


def register_prompts(
    mcp: MCPServer, context: McpContext, guard: Callable[..., Any], settings: Settings
) -> None:
    """`guard` is typed `Callable[..., Any]`, exactly as `register_entity_tools` types it.

    A single `[T: Callable[..., Any]]` on this function would bind one `T` for all four
    registrations, so the second signature it met would be an error -- and the honest
    alternative, a `Protocol` with a generic `__call__`, would describe `_guard` no better
    than this does while adding a type nobody else in this package needs.

    A prompt of a module the space has switched off is not registered (`modules.py`).
    """

    def prompt(name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        if not prompt_available(name, settings):
            return lambda fn: fn
        decorator: Callable[[Callable[..., Any]], Callable[..., Any]] = mcp.prompt(name=name)
        return decorator

    @prompt("pipeline-review")
    @guard
    def review_pipeline(
        date_from: str | None = None, date_to: str | None = None
    ) -> list[dict[str, Any]]:
        """The weekly review of the sales pipeline, with stalled deals and pending offers.
        `date_from` and `date_to` are optional ISO dates: without them, the current month."""
        return dashboard_prompts.review_pipeline(context, date_from, date_to)

    @prompt("month-close")
    @guard
    def closing_month(year: int, month: int) -> list[dict[str, Any]]:
        """The to-do list before closing a month: the period's profit and loss, unbilled
        hours, overdue invoices, and whether the period is already closed."""
        return dashboard_prompts.closing_month(context, year, month)

    @prompt("hours-to-log")
    @guard
    def hours_to_log(week: str | None = None) -> list[dict[str, Any]]:
        """The days of the current week with no hours logged, with the deals worked on
        recently."""
        return dashboard_prompts.hours_to_log(context, week)

    @prompt("organization-status")
    @guard
    def status_organization(customer_id: str) -> list[dict[str, Any]]:
        """The briefing before a phone call: unpaid invoices, and the customer's full record
        attached as a resource."""
        return organization_prompts.status_organization(context, customer_id)
