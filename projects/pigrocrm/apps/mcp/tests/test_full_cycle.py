"""**Criterion 12.** The whole cycle over one session, ending on the one thing an agent
must not be able to do.

Claude logs eight hours through the MCP tool and reads `deal://{id}` back; a **human**
turns those hours into a draft and issues it; the deal's P&L then reconciles to the cent
with the invoices behind it, against direct SQL rather than against another call to the
method under test; the hour freezes; the timeline tells `mcp` apart from `user`; and
Claude, re-reading the same resource, sees the decision -- while still finding no tool
with which to have taken it.

**Why the human half is driven through the services and not over REST.** The brief's own
sample called `POST /api/deals/{id}/time-entries/to-invoice-draft` and
`POST /api/invoices/{id}/issue` from this file, through an `api_client` fixture. That
import is banned: `apps/mcp/ruff.toml` forbids `pigrocrm_api` under `TID251`, and
`apps/api/ruff.toml` forbids `pigrocrm_mcp` symmetrically -- neither adapter may depend on
the other, which is the guarantee that keeps them adapters rather than a distributed
monolith. This project has met that wall once before and answered it the same way rather
than by widening the rule: see `apps/api/tests/test_entities_api.py`'s
`test_schema_endpoint_matches_describe_entity_exactly`, which states in its own docstring
that it lives on the API side *because* `apps/mcp` may not import `pigrocrm_api`, and
compares against `pigrocrm.core` directly instead.

So the actor changes, and nothing else does. `Actor(type="user")` is exactly what
`get_actor` hands the routers after a cookie login, the routers add nothing to the call
but the actor (`routers/analytics.py` and `routers/invoices.py` are three lines each), and
the REST wire for this same cycle is driven twice elsewhere, both times end to end:
`apps/api/tests/test_analytics_api.py` over HTTP, and `apps/web/e2e/economics.spec.ts`
from a browser against a real uvicorn. What only this file can show is the other half --
that the agent's own tool call and the human's decision land on the same rows, through the
same services, with the surface an agent is given still missing the three names it must
never have.
"""

from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from mcp import Client
from sqlalchemy import text
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.schemas import BindTimeRequest
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.auth.schemas import UserCreate
from pigrocrm.core.auth.service import UserService
from pigrocrm.core.clock import oggi_in_italia
from pigrocrm.core.customers.schemas import CustomerCreate
from pigrocrm.core.customers.service import CustomerService
from pigrocrm.core.deals.schemas import DealCreate
from pigrocrm.core.deals.service import DealService
from pigrocrm.core.emitter.schemas import LegalEntityUpsert
from pigrocrm.core.emitter.service import LegalEntityService
from pigrocrm.core.errors import ImmutableField
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.invoices.schemas import InvoiceIssue
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.pipeline.service import PipelineService
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm.core.timetracking.schemas import TimeEntryUpdate
from pigrocrm.core.timetracking.service import TimeEntryService

# The agent, identical to `conftest.py`'s own `ADMIN`, which is what the `server` fixture
# hands every tool call.
AGENTE = Actor(id=None, type="mcp", role="admin")

# Never a literal year: `InvoiceService._check_issue_date` refuses a `data_emissione`
# outside the current one, so a hard-coded date would be a test that starts failing on the
# first of January -- the same reason `apps/api/tests/test_analytics_api.py` derives its
# own window at runtime.
OGGI = oggi_in_italia()

TARIFFA = Decimal("100.000000")
COSTO = Decimal("30.000000")
ORE = Decimal("8.00")


@pytest.fixture
def cycle(mcp_session: Session, tmp_path: Path) -> dict[str, Any]:
    """Everything the cycle needs before either half is driven: the regime, the
    letterhead, a customer complete enough to be *invoiced*, a priced deal, the person
    whose hours these are, and the human who decides.

    The customer carries the four address parts, the P.IVA and the `codice_sdi` because
    `check_party_exportable` and `check_recipient_routing` both run inside `issue`, before
    a register number is consumed, and refuse a recipient missing any of them. Without
    them the emission fails on the customer record rather than on anything this test is
    about.
    """
    # The install's own configuration, as `Actor.system()` and not as the agent: this
    # test is about issuing, and the scene is the installation's, not the agent's. (Until
    # ORB-188 there was a second reason -- `update_fiscal_profile` was one of the
    # operations `AGENT_FORBIDDEN_ACTIONS` refused to any agent credential -- which no
    # longer holds: an admin's agent may set the profile, see
    # `test_fiscal_identity_tools.py`.)
    FiscalProfileService(mcp_session).upsert(
        FiscalProfileUpsert(codice_regime="RF19"), Actor.system()
    )
    LegalEntityService(mcp_session).upsert_default(
        LegalEntityUpsert(
            ragione_sociale="Studio Rossi",
            partita_iva="01234567890",
            indirizzo="Via Vittorio Veneto 12",
            cap="20124",
            comune="Milano",
            provincia="MI",
            email="mario@example.com",
        ),
        AGENTE,
    )
    stages = PipelineService(mcp_session).seed_defaults(AGENTE)
    customer = CustomerService(mcp_session).create(
        CustomerCreate(
            ragione_sociale="Acme S.r.l.",
            partita_iva="12345678901",
            codice_sdi="ABCDEFG",
            indirizzo="Corso Italia 5",
            cap="00100",
            comune="Roma",
            provincia="RM",
            nazione="IT",
        ),
        AGENTE,
    )
    deal = DealService(mcp_session).create(
        DealCreate(nome="Progetto Ciclo", customer_id=customer.id, tariffa_oraria=TARIFFA),
        AGENTE,
    )
    worker = UserService(mcp_session).create(
        UserCreate(
            email="worker-full-cycle@pigro.it",
            password="supersegreta1",
            nome="Worker",
            ruolo="collaboratore",
            costo_orario_default=COSTO,
        ),
        Actor.system(),
    )
    umano = UserService(mcp_session).create(
        UserCreate(
            email="umano-full-cycle@pigro.it",
            password="supersegreta1",
            nome="Umano",
            ruolo="admin",
        ),
        Actor.system(),
    )
    return {
        "deal_id": deal.id,
        "user_id": worker.id,
        "vinto_id": next(stage.id for stage in stages if stage.code == "vinto"),
        # `type="user"` with a real id: exactly the Actor `get_actor` builds from a cookie
        # login, and the only reason the timeline assertion at the end can tell the two
        # halves of this test apart at all.
        "umano": Actor(id=umano.id, type="user", role="admin"),
        # `bind_time_to_invoice` reaches `InvoiceService`, whose constructor takes a
        # backend because a *later* emission renders artefacts through it. A tmp-dir
        # backend for the same reason the `server` fixture builds one: the default root is
        # this repository's own working tree.
        "storage": LocalFileStorage(tmp_path / "fatture"),
    }


def _revenue_from_sql(session: Session, deal_id: UUID) -> Decimal:
    """The deal's revenue, read straight from the table with `_revenue_filter`'s own three
    conditions written out by hand.

    Deliberately not `AnalyticsRepository.deal_revenue`: a reconciliation that calls the
    method under test reconciles nothing. `imponibile`, never `totale` -- the total carries
    VAT, which is money collected on the State's behalf and is not revenue (criterion 1).
    Under the forfettario the two coincide, which is exactly why the distinction has to be
    written down somewhere it can be read.
    """
    return Decimal(
        session.execute(
            text(
                "SELECT COALESCE(SUM(imponibile), 0) FROM invoices "
                "WHERE deal_id = :id AND tipo = 'fattura' AND stato = 'emessa' "
                "  AND deleted_at IS NULL"
            ),
            {"id": deal_id},
        ).scalar_one()
    )


async def test_the_full_cycle_ends_on_the_tool_an_agent_cannot_find(
    server: Any, mcp_session: Session, cycle: dict[str, Any]
) -> None:
    deal_id: UUID = cycle["deal_id"]
    umano: Actor = cycle["umano"]
    storage: LocalFileStorage = cycle["storage"]

    # 1. Claude records the hours and reads the deal back.
    async with Client(server) as client:
        logged = await client.call_tool(
            "log_time",
            {
                "deal_id": str(deal_id),
                "user_id": str(cycle["user_id"]),
                "data": OGGI.isoformat(),
                # A string with two decimal places, not a bare `8`: a JSON integer arrives
                # as a Python float whose `Decimal` conversion carries only as many places
                # as the float itself. `test_timetracking_tools.py` states the same reason
                # at its own call site.
                "ore": str(ORE),
                "descrizione": "Analisi e sviluppo",
            },
        )
        entry_id = UUID(logged.structured_content["id"])
        # The rate was resolved from the deal at write time and frozen onto the row --
        # from here on nothing reads `deals.tariffa_oraria` again, which is why raising it
        # tomorrow cannot move this hour.
        assert logged.structured_content["tariffa_applicata"] == "100.000000"
        assert logged.structured_content["tariffa_origine"] == "deal"

        rendered = (await client.read_resource(f"deal://{deal_id}")).contents[0].text
        assert "- Ore consuntivate: 8.00" in rendered
        assert "## Economia" in rendered
        assert "- Ricavi fatturati: 0.00 EUR (0 fatture emesse)" in rendered
        # Never a bare margin on an unfinished deal: an agent handed one quotes it as
        # final.
        assert "**provvisorio**" in rendered
        assert "- Valore maturato (stima, non un ricavo): 800.00 EUR" in rendered

        # 2. And the tools it must not find are not there. Not a permission check: a PAT
        #    inherits its owner's full role and never expires (residual R10), so the
        #    absence of the tool is the only mechanism that holds.
        names = {tool.name for tool in (await client.list_tools()).tools}
        assert "bind_time_to_invoice" not in names
        assert "get_fiscal_estimate" not in names
        assert "recalculate_rates" not in names
        # It can still read the economics it has just contributed to: the ban is on
        # deciding, not on knowing.
        assert {"log_time", "get_deal_pnl", "describe_rates"} <= names

    # 3. The human turns those hours into a draft -- and the deal's economics do not move.
    #    A draft is not revenue: the same hour is still billable-and-unbilled, which is the
    #    invoice-state reading of "already invoiced" that every figure in this slice
    #    inherits. The link-based reading would leave the work in neither figure: priced,
    #    done, and invisible until somebody pressed "issue".
    draft = AnalyticsService(mcp_session, storage).bind_time_to_invoice(
        deal_id, BindTimeRequest(entry_ids=[entry_id], raggruppa_per_mese=True), umano
    )
    assert draft.stato == "bozza"
    assert draft.imponibile == Decimal("800.00")

    pending = AnalyticsService(mcp_session).deal_pnl(deal_id, umano)
    assert pending.ricavi == Decimal("0.00") == _revenue_from_sql(mcp_session, deal_id)
    assert pending.ore_fatturabili_non_fatturate == Decimal("8.00")

    # 4. ...and then issues it, which is the step that makes it revenue -- and the step
    #    with no MCP tool behind it (slice 3 §11).
    issued = InvoiceService(mcp_session, storage).issue(draft.id, InvoiceIssue(), umano)
    assert issued.numero is not None
    assert issued.anno == OGGI.year

    # 5. The deal is won, so its margin earns the right to stop being provisional: `stato`
    #    is `chiuso` only when the stage is not `open` *and* nothing billable is left
    #    unbilled. Both halves are now true, and neither alone would have been enough.
    DealService(mcp_session).move_stage(deal_id, cycle["vinto_id"], umano)

    # 6. Criterion 1, against direct SQL rather than a second call to the method checked.
    pnl = AnalyticsService(mcp_session).deal_pnl(deal_id, umano)
    assert pnl.ricavi == _revenue_from_sql(mcp_session, deal_id) == Decimal("800.00")
    assert pnl.stato == "chiuso"
    assert pnl.fatture_emesse == 1
    assert pnl.ore_fatturabili_non_fatturate == Decimal("0.00")
    assert pnl.costo_lavoro == Decimal("240.00")
    assert pnl.margine_lordo == Decimal("560.00")
    # The hours are invoiced, so the accrued estimate carries nothing the revenue does not
    # already carry -- and from here on the figure is the invoice.
    assert pnl.valore_maturato == pnl.ricavi

    # 7. The hour is frozen. Not because `issue()` went looking for it: the link was
    #    written when the draft line was born, and `billed_entry_ids` reads the *invoice's*
    #    state through it -- which is why the same hour was still editable one step ago.
    with pytest.raises(ImmutableField) as refusal:
        TimeEntryService(mcp_session).update(entry_id, TimeEntryUpdate(ore=Decimal("9.00")), umano)
    assert refusal.value.details["field"] == "ore"
    assert refusal.value.code == "immutable_field"

    # 8. The timeline tells the two actors apart -- which is the whole reason an agent is
    #    allowed to write at all: everything it did is attributable to it, by name.
    timeline = ActivityService(mcp_session).timeline("deal", deal_id, 200)
    tipi_attore = {entry.actor_type for entry in timeline}
    assert {"mcp", "user"} <= tipi_attore
    umani = {entry.kind for entry in timeline if entry.actor_type == "user"}
    agente = {entry.kind for entry in timeline if entry.actor_type == "mcp"}
    assert "time_bound_to_invoice" in umani
    assert "time_bound_to_invoice" not in agente

    # 9. And Claude, reading the same resource again, sees the decision it could not take.
    async with Client(server) as client:
        rendered = (await client.read_resource(f"deal://{deal_id}")).contents[0].text
    assert "- Ricavi fatturati: 800.00 EUR (1 fatture emesse)" in rendered
    assert "- Margine lordo: 560.00 EUR (definitivo)" in rendered
    assert "provvisorio" not in rendered
    # Dropped once the deal is closed: repeating the estimate beside the revenue invites it
    # to be read as a second, disagreeing figure for the same thing.
    assert "Valore maturato" not in rendered
