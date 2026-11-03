"""**Criterion 9**, completed: an agent can read the economics and cannot change what
already-recorded numbers mean, and the two most sensitive operations have no tool at all.

`bind_time_to_invoice` is absent because binding hours to a draft is the step that
determines their freezing at issue, and choosing *which* hours to invoice is a commercial
decision. `get_fiscal_estimate` is absent for a different reason: taxable income,
contributions and estimated net for a real person are the most sensitive figures this
product holds, and residual R10 leaves a PAT inheriting its owner's full role. Both
absences are structural -- `apps/mcp/tests/test_mcp_invoice_ban.py` fails the build if
either name is registered or either method is called from anywhere under `tools/` -- and
this module checks the same guarantee from the client's own side, where an agent actually
looks for a tool.
"""

from decimal import Decimal

import pytest
from mcp import Client
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.db.clock import today_local
from pigrocrm.core.deals.models import DealValueLine
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.timetracking.schemas import TimeEntryCreate
from pigrocrm.core.timetracking.service import TimeEntryService

ADMIN = Actor(id=None, type="mcp", role="admin")

# Never a literal year: a window written as 2026 is a suite that starts failing on the
# first of January (task 4B-8's own ruling).
TODAY = today_local()
DA = TODAY.replace(month=1, day=1).isoformat()
A = TODAY.replace(month=12, day=31).isoformat()


@pytest.fixture
def deal_with_hours(mcp_session: Session, seeded_deal_id, seeded_user_id):
    """Four hours sold at 100 EUR and costing 25 EUR, and no invoice at all: revenue is
    the invoice, so every figure below distinguishes a real zero from an estimate."""
    TimeEntryService(mcp_session).create(
        TimeEntryCreate(
            deal_id=seeded_deal_id,
            user_id=seeded_user_id,
            date=TODAY,
            hours=Decimal("4.00"),
            description="Analisi",
            applied_rate=Decimal("100.000000"),
            applied_cost=Decimal("25.000000"),
        ),
        ADMIN,
    )
    return seeded_deal_id


async def test_the_three_read_tools_exist_and_the_two_excluded_do_not(server) -> None:
    async with Client(server) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
    assert {"get_deal_pnl", "get_period_pnl", "get_budget_vs_actual"} <= names
    assert "bind_time_to_invoice" not in names
    assert "get_fiscal_estimate" not in names


async def test_get_deal_pnl_returns_the_service_figures_verbatim(server, deal_with_hours) -> None:
    """No arithmetic in the adapter: the tool returns what the service computed, including
    the `null` margin percentage, which an agent must read as "nothing has been earned"
    rather than as "everything went out in costs"."""
    async with Client(server) as client:
        result = await client.call_tool("get_deal_pnl", {"deal_id": str(deal_with_hours)})
    pnl = result.structured_content
    assert pnl["revenue"] == "0.00"
    assert pnl["percentage_margin"] is None
    assert pnl["work_cost"] == "100.00"
    assert pnl["accrued_value"] == "400.00"
    assert pnl["status"] == "in_progress"


async def test_get_period_pnl_answers_in_two_columns_and_no_total(server, deal_with_hours) -> None:
    async with Client(server) as client:
        result = await client.call_tool("get_period_pnl", {"date_from": DA, "date_to": A})
    body = result.structured_content
    assert "total" not in body
    assert body["in_progress"]["deal"] == 1
    assert body["in_progress"]["work_cost"] == "100.00"
    assert body["closed"]["deal"] == 0
    assert body["general_expenses"] == "0.00"


async def test_get_budget_vs_actual_marks_the_unestimated_deal_rather_than_scoring_it(
    server, deal_with_hours
) -> None:
    """An absent estimate is not an estimate of zero, and is never counted as a 100%
    overrun: the row says so and the aggregates leave it out."""
    async with Client(server) as client:
        result = await client.call_tool("get_budget_vs_actual", {"date_from": DA, "date_to": A})
    page = result.structured_content
    assert [row["deal_id"] for row in page["items"]] == [str(deal_with_hours)]
    assert page["items"][0]["actuals_hours"] == "4.00"
    assert page["items"][0]["unbudgeted"] is True
    assert page["unbudgeted_deals"] == 1
    assert page["budgeted_deals"] == 0


async def test_an_inverted_window_comes_back_as_guidance_not_a_pydantic_dump(server) -> None:
    """Spec §8.2: the query schema is built *inside* the guarded call, so a refused window
    is rendered guidance and not a raw English pydantic dump with a link in it."""
    async with Client(server) as client:
        result = await client.call_tool("get_period_pnl", {"date_from": A, "date_to": DA})
    assert result.is_error
    # The service's own reason, in Italian, not "tool not found" and not a raw dump:
    # without this line the test would pass just as happily against a tool that was
    # never registered at all.
    assert "intervallo invertito" in result.content[0].text
    assert "errors.pydantic.dev" not in result.content[0].text


async def test_the_deal_resource_now_carries_revenue_and_margin(server, deal_with_hours) -> None:
    async with Client(server) as client:
        rendered = (await client.read_resource(f"deal://{deal_with_hours}")).contents[0].text
    assert "## Economics" in rendered
    assert "- Invoiced revenue: 0.00 EUR (0 invoices issued)" in rendered
    assert "- Labor cost: 100.00 EUR" in rendered
    # An open deal's margin is provisional and the resource says so, rather than handing
    # an agent a figure it will quote as final.
    assert "provisional" in rendered
    assert "- Margin %: not computable, no revenue invoiced" in rendered


async def test_get_period_pnl_offers_the_accrual_reading_and_refuses_an_unknown_base(
    server, deal_with_hours
) -> None:
    """ORB-61: `base` is a second reading of the same revenue, not a second report. The
    two readings' arithmetic is proven in core; here the tool accepts the name, keeps the
    default, and renders an unknown base as guidance rather than a pydantic dump."""
    async with Client(server) as client:
        default = await client.call_tool("get_period_pnl", {"date_from": DA, "date_to": A})
        accrual = await client.call_tool(
            "get_period_pnl", {"date_from": DA, "date_to": A, "base": "accrual"}
        )
        unknown = await client.call_tool(
            "get_period_pnl", {"date_from": DA, "date_to": A, "base": "pension_fund"}
        )
    assert not default.is_error and not accrual.is_error
    # No invoice in the fixture, so the hours are the same under both readings: costs and
    # hours never move with the base.
    assert accrual.structured_content["in_progress"] == default.structured_content["in_progress"]
    assert default.structured_content["base"] == "issue"
    assert accrual.structured_content["base"] == "accrual"
    assert unknown.is_error
    assert "errors.pydantic.dev" not in unknown.content[0].text


# --- ceiling headroom and the "would this fit?" simulator (REB-373) -----------------


def _configure_profile(session: Session) -> None:
    FiscalProfileService(session).upsert(FiscalProfileUpsert(regime_code="RF19"), ADMIN)


async def test_get_ceiling_headroom_reports_the_configured_packs_thresholds(
    server, mcp_session: Session
) -> None:
    _configure_profile(mcp_session)
    async with Client(server) as client:
        result = await client.call_tool("get_ceiling_headroom", {"year": TODAY.year})
    body = result.structured_content
    assert body["pack_id"] == "it-flat-rate"
    revenue_threshold = next(s for s in body["thresholds"] if s["id"] == "revenue_ceiling")
    assert revenue_threshold["threshold"] == "85000.00"
    assert revenue_threshold["remaining"] == "85000.00"


async def test_get_ceiling_headroom_without_a_fiscal_profile_is_guidance_not_a_dump(
    server,
) -> None:
    """The same `NotFound` `get_fiscal_estimate` would raise, rendered as guidance."""
    async with Client(server) as client:
        result = await client.call_tool("get_ceiling_headroom", {"year": TODAY.year})
    assert result.is_error
    assert "errors.pydantic.dev" not in result.content[0].text


async def test_simulate_ceiling_adds_the_typed_value(server, mcp_session: Session) -> None:
    _configure_profile(mcp_session)
    async with Client(server) as client:
        result = await client.call_tool(
            "simulate_ceiling", {"year": TODAY.year, "budgeted_value": "20000.00"}
        )
    body = result.structured_content
    assert body["brief_addition"] == "20000.00"
    revenue_threshold = next(s for s in body["thresholds"] if s["id"] == "revenue_ceiling")
    assert revenue_threshold["falls_within"] is True


async def test_simulate_ceiling_derives_the_addition_from_hours_and_rate(
    server, mcp_session: Session
) -> None:
    _configure_profile(mcp_session)
    async with Client(server) as client:
        result = await client.call_tool(
            "simulate_ceiling",
            {"year": TODAY.year, "budgeted_hours": "100.00", "hourly_rate": "250.000000"},
        )
    assert result.structured_content["brief_addition"] == "25000.00"


async def test_simulate_ceiling_reads_a_saved_deal_by_its_value_lines(
    server, mcp_session: Session, seeded_deal_id
) -> None:
    """A fee of 1.000 a month from 1 October adds the three payments of the year."""
    _configure_profile(mcp_session)
    mcp_session.add(
        DealValueLine(
            deal_id=seeded_deal_id,
            position=0,
            unit="flat",
            delivery_period="monthly",
            unit_price=Decimal("1000.00"),
            quantity=Decimal(1),
            start=TODAY.replace(month=10, day=1),
        )
    )
    mcp_session.flush()
    async with Client(server) as client:
        result = await client.call_tool(
            "simulate_ceiling", {"year": TODAY.year, "deal_id": str(seeded_deal_id)}
        )
        refused = await client.call_tool(
            "simulate_ceiling",
            {"year": TODAY.year, "deal_id": str(seeded_deal_id), "budgeted_value": "1.00"},
        )
    assert result.structured_content["brief_addition"] == "3000.00"
    assert refused.is_error
    assert "deal_id" in refused.content[0].text


async def test_simulate_ceiling_without_any_estimate_is_guidance_not_a_dump(
    server, mcp_session: Session
) -> None:
    _configure_profile(mcp_session)
    async with Client(server) as client:
        result = await client.call_tool("simulate_ceiling", {"year": TODAY.year})
    assert result.is_error
    assert "errors.pydantic.dev" not in result.content[0].text


async def test_get_year_overview_reads_the_register_of_the_year(
    server, mcp_session: Session
) -> None:
    _configure_profile(mcp_session)
    async with Client(server) as client:
        result = await client.call_tool("get_year_overview", {"year": TODAY.year})
    body = result.structured_content
    assert body["year"] == TODAY.year
    assert body["register"] == []
    assert body["total"]["taxable_amount"] == "0.00"


async def test_get_days_analysis_defaults_to_the_current_month(server) -> None:
    async with Client(server) as client:
        result = await client.call_tool("get_days_analysis", {})
    body = result.structured_content
    assert [m["month"] for m in body["months"]] == [TODAY.strftime("%Y-%m")]
    assert body["days_in_period"] == "0.00"
    assert body["actual_rate"] is None


async def test_get_days_analysis_with_half_a_period_is_guidance_not_a_dump(server) -> None:
    async with Client(server) as client:
        result = await client.call_tool("get_days_analysis", {"date_from": DA})
    assert result.is_error
    assert "errors.pydantic.dev" not in result.content[0].text
