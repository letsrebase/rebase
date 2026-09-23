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
from pigrocrm.core.clock import oggi_in_italia
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.timetracking.schemas import TimeEntryCreate
from pigrocrm.core.timetracking.service import TimeEntryService

ADMIN = Actor(id=None, type="mcp", role="admin")

# Never a literal year: a window written as 2026 is a suite that starts failing on the
# first of January (task 4B-8's own ruling).
OGGI = oggi_in_italia()
DA = OGGI.replace(month=1, day=1).isoformat()
A = OGGI.replace(month=12, day=31).isoformat()


@pytest.fixture
def deal_with_hours(mcp_session: Session, seeded_deal_id, seeded_user_id):
    """Four hours sold at 100 EUR and costing 25 EUR, and no invoice at all: revenue is
    the invoice, so every figure below distinguishes a real zero from an estimate."""
    TimeEntryService(mcp_session).create(
        TimeEntryCreate(
            deal_id=seeded_deal_id,
            user_id=seeded_user_id,
            data=OGGI,
            ore=Decimal("4.00"),
            descrizione="Analisi",
            tariffa_applicata=Decimal("100.000000"),
            costo_applicato=Decimal("25.000000"),
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
    assert pnl["ricavi"] == "0.00"
    assert pnl["margine_percentuale"] is None
    assert pnl["costo_lavoro"] == "100.00"
    assert pnl["valore_maturato"] == "400.00"
    assert pnl["stato"] == "in corso"


async def test_get_period_pnl_answers_in_two_columns_and_no_total(server, deal_with_hours) -> None:
    async with Client(server) as client:
        result = await client.call_tool("get_period_pnl", {"da": DA, "a": A})
    body = result.structured_content
    assert "totale" not in body
    assert body["in_corso"]["deal"] == 1
    assert body["in_corso"]["costo_lavoro"] == "100.00"
    assert body["chiusi"]["deal"] == 0
    assert body["spese_generali"] == "0.00"


async def test_get_budget_vs_actual_marks_the_unestimated_deal_rather_than_scoring_it(
    server, deal_with_hours
) -> None:
    """An absent estimate is not an estimate of zero, and is never counted as a 100%
    overrun: the row says so and the aggregates leave it out."""
    async with Client(server) as client:
        result = await client.call_tool("get_budget_vs_actual", {"da": DA, "a": A})
    page = result.structured_content
    assert [row["deal_id"] for row in page["items"]] == [str(deal_with_hours)]
    assert page["items"][0]["ore_consuntivate"] == "4.00"
    assert page["items"][0]["non_preventivato"] is True
    assert page["deal_non_preventivati"] == 1
    assert page["deal_preventivati"] == 0


async def test_an_inverted_window_comes_back_as_guidance_not_a_pydantic_dump(server) -> None:
    """Spec §8.2: the query schema is built *inside* the guarded call, so a refused window
    is rendered guidance and not a raw English pydantic dump with a link in it."""
    async with Client(server) as client:
        result = await client.call_tool("get_period_pnl", {"da": A, "a": DA})
    assert result.is_error
    # The service's own reason, in Italian, not "tool not found" and not a raw dump:
    # without this line the test would pass just as happily against a tool that was
    # never registered at all.
    assert "intervallo invertito" in result.content[0].text
    assert "errors.pydantic.dev" not in result.content[0].text


async def test_the_deal_resource_now_carries_revenue_and_margin(server, deal_with_hours) -> None:
    async with Client(server) as client:
        rendered = (await client.read_resource(f"deal://{deal_with_hours}")).contents[0].text
    assert "## Economia" in rendered
    assert "- Ricavi fatturati: 0.00 EUR (0 fatture emesse)" in rendered
    assert "- Costo del lavoro: 100.00 EUR" in rendered
    # An open deal's margin is provisional and the resource says so, rather than handing
    # an agent a figure it will quote as final.
    assert "provvisorio" in rendered
    assert "- Margine %: non calcolabile, nessun ricavo fatturato" in rendered


async def test_get_period_pnl_offers_the_accrual_reading_and_refuses_an_unknown_base(
    server, deal_with_hours
) -> None:
    """ORB-61: `base` is a second reading of the same revenue, not a second report. The
    two readings' arithmetic is proven in core; here the tool accepts the name, keeps the
    default, and renders an unknown base as guidance rather than a pydantic dump."""
    async with Client(server) as client:
        default = await client.call_tool("get_period_pnl", {"da": DA, "a": A})
        accrual = await client.call_tool("get_period_pnl", {"da": DA, "a": A, "base": "competenza"})
        unknown = await client.call_tool("get_period_pnl", {"da": DA, "a": A, "base": "cassa"})
    assert not default.is_error and not accrual.is_error
    # No invoice in the fixture, so the hours are the same under both readings: costs and
    # hours never move with the base.
    assert accrual.structured_content["in_corso"] == default.structured_content["in_corso"]
    assert default.structured_content["base"] == "emissione"
    assert accrual.structured_content["base"] == "competenza"
    assert unknown.is_error
    assert "errors.pydantic.dev" not in unknown.content[0].text


# --- ceiling headroom and the "would this fit?" simulator (REB-373) -----------------


def _configure_profile(session: Session) -> None:
    FiscalProfileService(session).upsert(FiscalProfileUpsert(codice_regime="RF19"), ADMIN)


async def test_get_ceiling_headroom_reports_the_configured_packs_thresholds(
    server, mcp_session: Session
) -> None:
    _configure_profile(mcp_session)
    async with Client(server) as client:
        result = await client.call_tool("get_ceiling_headroom", {"anno": OGGI.year})
    body = result.structured_content
    assert body["pack_id"] == "it-flat-rate"
    ricavi_soglia = next(s for s in body["soglie"] if s["id"] == "soglia_ricavi")
    assert ricavi_soglia["soglia"] == "85000.00"
    assert ricavi_soglia["residuo"] == "85000.00"


async def test_get_ceiling_headroom_without_a_fiscal_profile_is_guidance_not_a_dump(
    server,
) -> None:
    """The same `NotFound` `get_fiscal_estimate` would raise, rendered as guidance."""
    async with Client(server) as client:
        result = await client.call_tool("get_ceiling_headroom", {"anno": OGGI.year})
    assert result.is_error
    assert "errors.pydantic.dev" not in result.content[0].text


async def test_simulate_ceiling_adds_the_typed_value(server, mcp_session: Session) -> None:
    _configure_profile(mcp_session)
    async with Client(server) as client:
        result = await client.call_tool(
            "simulate_ceiling", {"anno": OGGI.year, "valore_preventivato": "20000.00"}
        )
    body = result.structured_content
    assert body["aggiunta_sintetica"] == "20000.00"
    ricavi_soglia = next(s for s in body["soglie"] if s["id"] == "soglia_ricavi")
    assert ricavi_soglia["rientra"] is True


async def test_simulate_ceiling_derives_the_addition_from_hours_and_rate(
    server, mcp_session: Session
) -> None:
    _configure_profile(mcp_session)
    async with Client(server) as client:
        result = await client.call_tool(
            "simulate_ceiling",
            {"anno": OGGI.year, "ore_preventivate": "100.00", "tariffa_oraria": "250.000000"},
        )
    assert result.structured_content["aggiunta_sintetica"] == "25000.00"


async def test_simulate_ceiling_without_any_estimate_is_guidance_not_a_dump(
    server, mcp_session: Session
) -> None:
    _configure_profile(mcp_session)
    async with Client(server) as client:
        result = await client.call_tool("simulate_ceiling", {"anno": OGGI.year})
    assert result.is_error
    assert "errors.pydantic.dev" not in result.content[0].text
