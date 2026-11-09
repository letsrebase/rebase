"""Three of §10's four prompts. Each is several reads **plus a posture on how to read
them**, which is what makes it a prompt and not a tool.

The distinction is not stylistic. A tool is invoked by the *model*, when it decides it needs
one, and returns *data*. A prompt is invoked by the *user*, from a menu, and returns
*messages*. And the judgement sits in a different place: in a tool it is the model's, in a
prompt it is written down here. A tool that also returned "ask about the deals that have not
moved, do not summarise the ones that have" would be putting instructions inside a data
result -- the shape of an injection -- and the model would have to guess it should call it,
whereas here the human picks it and can see what was attached.

The context travels as **Markdown text inside the message**. It is a resource block only
where the resource already exists, which in this slice means `customer://{id}` and therefore
only `prompts/customer.py`. §11.1 adds no new resource, and inventing
`dashboard://commercial?from=...` in order to have a URI to embed would be adding one without
saying so.

**Every figure rendered here is a field of a dashboard response, printed.** Nothing in this
module computes anything -- the same rule as `core/dashboard/`, for the same reason. If a
figure is missing from the response, the fix is on the owning service.

**And every list is capped by a named constant.** That is the other half of §10, and the
half that is easy to lose: a prompt's cost is paid in the model's context window on *every*
render, so a section that grows with the register is a tax the user pays for rows nobody
asked about. Where a cap bites, the prompt says how many rows it did not show rather than
truncating silently -- a briefing that quietly omits its tail is worse than one that admits
it, because the reader cannot tell the difference from a short register.
"""

from datetime import date
from decimal import Decimal
from typing import Any

from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.dashboard.schemas import PeriodQuery
from pigrocrm.core.dashboard.service import DashboardService
from pigrocrm.core.db import month_bounds
from pigrocrm.core.deals.service import DealService
from pigrocrm.core.errors import ModuleOff
from pigrocrm.core.modules.registry import MODULES_BY_KEY
from pigrocrm_mcp.context import McpContext

# The caps. Each is the number of rows that is worth a place in the model's context window
# on every render of that section, and each is named so `test_mcp_prompts.py` can assert the
# bound against a corpus far larger than it -- which is the only way a cap is testable at
# all. A cap asserted only against a corpus smaller than itself is a comment.
PENDING_OFFERS_SHOWN = 10
RECENT_DEALS_SHOWN = 8


def _user(text: str) -> dict[str, Any]:
    return {"role": "user", "content": {"type": "text", "text": text}}


def _euro(value: Decimal) -> str:
    """The string the service produced, with a comma.

    `str(value)`, never a format spec: `Decimal` already carries its own scale, and
    `f"{value:.2f}"` would re-round a figure the owning service has already rounded --
    §7's second source of truth with a friendly tone. `float(value)` would be worse still,
    putting a binary rounding error into a briefing.
    """
    return f"{str(value).replace('.', ',')} €"


def _percent(value: Decimal | None) -> str:
    """A dash, never "0%".

    Zero per cent means "I lost everything"; `None` means nothing closed, or nothing was
    earned. Two different facts, and a briefing that conflates them is worse than one that
    omits the line -- the same rule `PnlTotals.percentage_margin` and
    `ClosedInPeriod.conversion_rate` are both typed `Decimal | None` to express.
    """
    return "—" if value is None else f"{str(value).replace('.', ',')}%"


def _and_the_rest(shown: int, total: int) -> list[str]:
    """The line that admits a truncation, or nothing at all.

    `total` is the count the owning service reported, not `len()` of a list this module
    already truncated: the point of the line is to name rows that are *not* here.
    """
    if total <= shown:
        return []
    return ["", f"_{total - shown} more rows not shown._"]


def review_pipeline(
    context: McpContext, date_from: str | None = None, date_to: str | None = None
) -> list[dict[str, Any]]:
    """The weekly review. Posture: ask about the deals that have not moved.

    The pending offers come from `board.offers_pending` rather than from a second call to
    `DocumentRepository.pending_offers`: the dashboard already read them inside its one
    `REPEATABLE READ` transaction, and a second read here would be a second instant -- a
    briefing whose list and whose count could disagree with each other by construction.
    """
    query = PeriodQuery(
        date_from=date.fromisoformat(date_from) if date_from else None,
        date_to=date.fromisoformat(date_to) if date_to else None,
    )
    board = DashboardService(context.session).get_commercial_dashboard(query, context.actor)

    lines = [
        f"# Pipeline review — {board.period.date_from} → {board.period.date_to}",
        "",
        "## Pipeline by stage",
        "",
        "| Stage | Deals | Value | Without value | Weighted value (estimate) |",
        "|---|---|---|---|---|",
    ]
    for row in board.pipeline:
        lines.append(
            f"| {row.stage_name} | {row.number} | {_euro(row.total_value)} "
            f"| {row.without_value} | {_euro(row.weighted_value)} |"
        )
    lines += [
        "",
        "The weighted value is an **estimate** (expected value × probability): it is not "
        "invoiced and must not be added to revenue. Deals with no expected value are counted "
        "separately and are not worth zero.",
        "",
        "The closed stages (won, lost) count the deals that exist **today**, not the ones "
        "closed in the period: those are in the next section.",
        "",
        "## Closings in the period",
        "",
        f"- Won: {board.closings.won}",
        f"- Lost: {board.closings.lost}",
        f"- Conversion rate: {_percent(board.closings.conversion_rate)}",
        f"- Won value (declared by the deals, **not** invoiced): {_euro(board.closings.won_value)}",
        f"- Closings expected in the 30 days after the period: {board.expected_closings_30_days}",
    ]
    if board.closings_not_attributable:
        lines.append(
            f"- {board.closings_not_attributable} deals closed before this measure was "
            "introduced cannot be attributed to a period and are not in the numbers above."
        )
    lines += ["", "## Offers sent awaiting a reply", ""]
    offers = board.offers_pending[:PENDING_OFFERS_SHOWN]
    if not offers:
        lines.append("No offers pending.")
    else:
        for offer in offers:
            # `None` days is not zero days: an offer whose `status_since` was never recorded is
            # of unknown age, and "stalled for 0 days" would read as "sent today".
            age = "unknown date" if offer.days is None else f"{offer.days} days"
            lines.append(f"- {offer.title} — stalled for {age}")
        lines += _and_the_rest(len(offers), board.offers_pending_total)
    lines += [
        "",
        f"Signal: {board.accepted_offers_deal_not_won} accepted offers whose deal is not won.",
        "",
        "---",
        "",
        "Do the weekly review on this data. Ask me about the **stalled** deals — those "
        "in the same stage for too long and the offers pending for more than two weeks "
        "— and do not summarize the ones that are moving: I already see those. If the "
        "conversion rate is null say so, do not treat it as zero. Do not propose automatic "
        "actions: list the questions to ask the customers.",
    ]
    return [_user("\n".join(lines))]


def closing_month(context: McpContext, year: int, month: int) -> list[dict[str, Any]]:
    """The list of things to do before closing a month. **No fiscal figure** (§10.1).

    `month_bounds` refuses a month outside 1-12 with a `ValidationFailed` naming `month`,
    which the server's guard renders as guidance. Validating it here as well would be a
    second rule that agrees with the first until one of them is edited.
    """
    date_from, date_to = month_bounds(year, month)
    service = DashboardService(context.session)
    economic = service.get_economic_dashboard(
        PeriodQuery(date_from=date_from, date_to=date_to), context.actor, context.modules
    )
    # Without the `time` module the report carries no labour and no hours (`pnl.without_labour`)
    # and the prompt leaves those lines out rather than printing a dash for them.
    hours_on = context.modules is None or "time" in context.modules

    pnl = economic.pnl
    lines = [
        f"# Month close — {year}-{month:02d}",
        "",
        "## Profit and loss of the period",
        "",
        "| Item | Closed deals | In-progress deals |",
        "|---|---|---|",
        f"| Revenue (taxable amount, issued) | {_euro(pnl.closed.revenue)} "
        f"| {_euro(pnl.in_progress.revenue)} |",
        f"| Direct costs | {_euro(pnl.closed.direct_costs)} "
        f"| {_euro(pnl.in_progress.direct_costs)} |",
    ]
    if pnl.closed.work_cost is not None and pnl.in_progress.work_cost is not None:
        lines.append(
            f"| Labor cost | {_euro(pnl.closed.work_cost)} | {_euro(pnl.in_progress.work_cost)} |"
        )
    lines += [
        f"| Gross margin | {_euro(pnl.closed.gross_margin)} "
        f"| {_euro(pnl.in_progress.gross_margin)} |",
        f"| Margin % | {_percent(pnl.closed.percentage_margin)} "
        f"| {_percent(pnl.in_progress.percentage_margin)} |",
        "",
        "The reportable figure is the **closed deals** column. The two columns are not added "
        "together: the margin of finished work and that of work in progress are not the "
        "same thing.",
        "",
        f"General expenses of the period: {_euro(pnl.general_expenses)} — **not allocated** "
        "to any deal.",
        "",
        "## To settle before closing",
        "",
    ]
    backlog = AnalyticsService(context.session).unbilled_backlog(
        context.actor, modules=context.modules
    )
    if hours_on:
        lines += [
            f"- Billable uninvoiced hours **in the period**: "
            f"{pnl.billable_uninvoiced_hours} hours, accrued value "
            f"{_euro(pnl.accrued_value)}",
            f"- Entries without a rate in the period: {pnl.hours_without_rate}",
            f"- Backlog **in total** (no period): "
            f"{backlog.billable_uninvoiced_hours} hours, "
            f"{_euro(backlog.accrued_value)}, of which {backlog.entries_without_rate} entries "
            "without a rate",
        ]
    else:
        # No hours without `time`; what is left of the arrears is the approved contract days.
        lines += [
            f"- Approved contract days not yet invoiced **in the period**: "
            f"{_euro(pnl.accrued_value)}",
            f"- Backlog **in total** (no period): {_euro(backlog.accrued_value)}, of which "
            f"{backlog.entries_without_rate} days without a rate",
        ]
    lines += [
        f"- Invoices issued in the period: {economic.issued_invoices}",
        f"- To collect (total incl. VAT, no period): {_euro(economic.unpaid)}",
        f"- Of which **overdue**: {_euro(economic.expired)}",
        "",
        f"Period closed: {'yes' if pnl.closed_period else 'no'}."
        + (f" Entries written late: {pnl.written_entries_overdue}." if hours_on else ""),
        "",
        "---",
        "",
        "Prepare the to-do list before closing this month. Flag only the deviations that "
        "matter: "
        + (
            "unbilled hours, entries without a rate, "
            if hours_on
            else "approved days not invoiced, days without a rate, "
        )
        + "overdue invoices. «To collect» is "
        "a receivable, not revenue: do not add it to revenue and do not use it to compute a "
        "margin. If the period is not closed"
        + (" and there are entries written late" if hours_on else "")
        + ", say that "
        "the numbers may still move. Do not compute any tax or contribution: it is not data "
        "you have.",
    ]
    return [_user("\n".join(lines))]


def hours_to_log(context: McpContext, week: str | None = None) -> list[dict[str, Any]]:
    """The most useful prompt in the product (§10): it attacks "I never entered Tuesday".

    `week` is accepted and refused for anything but the current week rather than
    silently ignored: `get_operational_dashboard` is the current week by construction (§6),
    and an argument the prompt advertised and answered with a different week's figures would
    be worse than one it does not have -- the caller would believe it worked.

    `days_without_hours` is the field, not a set difference computed here. The service already
    knows which days have no *entry*, which is not the same statement as "these hours sum to
    zero", and re-deriving it from `days` would silently pick the second reading.
    """
    board = DashboardService(context.session).get_operational_dashboard(
        context.actor, modules=context.modules
    )
    current = board.week
    if (
        current is None
    ):  # `time` is off; the prompt is not registered then, a direct call lands here
        raise ModuleOff("time", MODULES_BY_KEY["time"].label)
    if week is not None and week != current.date_from.isoformat():
        return [
            _user(
                f"The operational dashboard covers only the current week "
                f"({current.date_from} → {current.date_to}); the requested week ({week}) is "
                "not available. For the hours of a past week use the hours report."
            )
        ]

    lines = [
        f"# Hours to log — week {current.date_from} → {current.date_to}",
        "",
        "| Day | Hours |",
        "|---|---|",
    ]
    for day in current.days:
        lines.append(f"| {day.day} | {day.hours} |")
    lines += ["", f"Week total: {current.total_hours} hours.", ""]
    if current.days_without_hours:
        lines.append("**Days with no hours logged:**")
        lines += [f"- {day}" for day in current.days_without_hours]
    else:
        lines.append("No uncovered days: the week is complete.")

    lines += ["", "## Deals worked on recently", ""]
    # Names, not identifiers: a list of UUIDs is a section nobody can answer from. The feed
    # carries fifty rows and can name the same deal many times, so the names are resolved in
    # one batch, deduplicated in feed order and capped.
    feed = [a.entity_id for a in board.recent_activity if a.entity_type == "deal"]
    deals = DealService(context.session).get_many(set(feed), context.actor)
    seen: list[str] = []
    for deal_id in feed:
        deal = deals.get(deal_id)
        if deal is None or deal.name in seen:
            continue
        seen.append(deal.name)
        if len(seen) == RECENT_DEALS_SHOWN:
            break
    if not seen:
        lines.append("No recent activity on a deal.")
    else:
        lines += [f"- {name}" for name in seen]
    lines += [
        "",
        "---",
        "",
        "Help me recover the missing hours. For each day with no hours, ask me what I "
        "did — one day at a time, not all together — and propose the most likely deal "
        "among those above. When I answer, log the hours with `log_time`. Do not "
        "invent hours or deals: if you do not know what to charge a day to, ask. A day "
        "on which I did not work must be left empty, not logged as zero.",
    ]
    return [_user("\n".join(lines))]
