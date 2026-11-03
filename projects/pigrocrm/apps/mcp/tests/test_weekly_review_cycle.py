"""**Criterion 15**, the agent half. The human half is `apps/web/e2e/dashboard.spec.ts`.

The criterion's own shape is "Claude does this, the human does that", so it is split across
two surfaces rather than driven from one place. That is not a weakening, and it is not a
choice either: `apps/mcp/ruff.toml` forbids importing `pigrocrm_api` under `TID251` and
`apps/api/ruff.toml` forbids `pigrocrm_mcp` symmetrically, which is the guarantee that keeps
the two adapters adapters instead of a distributed monolith. `test_full_cycle.py` met the
same wall for criterion 12 and answered it the same way; the precedent is followed here
rather than the rule fought.

**What this file adds to a suite that already tests all four pieces.** `test_mcp_prompts.py`
renders `pipeline-review`, `test_mcp_dashboard.py` calls `get_commercial_dashboard` and
pins the absent `update_automation_config`, `test_mcp_search.py` searches a P.IVA fragment.
Each of them proves its own piece against its own corpus. None of them proves that the
pieces **join up** -- that the deal the briefing calls stalled is the deal the dashboard
counts, that the customer behind it is reachable from a fragment of the VAT number the
briefing never printed, and that the same corpus supports all of it at once. A weekly review
is that sequence, and the sequence is what an agent actually performs.

**This file builds its own server, and that is the subject as much as the setup.** The
package's `server` fixture wires `build_server(lambda: mcp_session, ...)`: one `Session`
shared by every call, held open inside an outer transaction. `DashboardService` refuses such
a session by design -- a dashboard that cannot set `REPEATABLE READ` is a dashboard whose
cards can disagree with their own drill-throughs -- so the server here is wired the way
`__main__.py` wires production, with a `ScopedSessionProvider` handing out one session per
logical call. `test_mcp_dashboard.py::test_the_shared_session_fixture_is_refused_loudly`
pins that this is a property of the service and not a quirk of the plumbing.

The corpus is committed, because a scoped session never sees another transaction's
uncommitted rows, and it is **additive**: `mcp_engine` is session-scoped and earlier files in
this package commit rows that outlive them, so this file creates two stages of its own
instead of seeding the defaults, keys every assertion on its own prefix, and removes only
its own rows on the way out.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, NamedTuple
from uuid import UUID

import pytest
from mcp import Client
from sqlalchemy import Engine, delete
from sqlalchemy.orm import sessionmaker

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.schemas import PeriodPnlQuery
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db import month_bounds, session_factory, today_local
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.documents.models import Document
from pigrocrm.core.pipeline.models import PipelineStage
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm_mcp.context import ScopedSessionProvider
from pigrocrm_mcp.server import build_server

# The agent, identical to `conftest.py`'s own `ADMIN`: `type="mcp"`, which is what every
# tool call and every prompt render below is attributed to.
AGENT_ACTOR = Actor(id=None, type="mcp", role="admin")

_PREFIX = "MCPCICLO"
STAGE_OPEN = f"{_PREFIX} stato aperto"
STAGE_WON = f"{_PREFIX} stato vinto"
CUSTOMER = f"{_PREFIX} Ingegneria Srl"
STALLED_DEAL = f"{_PREFIX} rifacimento impianti"
OFFER_STALLED = f"{_PREFIX} offerta impianti"
OFFER_ACCEPTED = f"{_PREFIX} offerta precedente"

# The VAT number the customer is resolved from, and the fragment taken out of its middle.
# An infix, never a prefix: a prefix would also be served by a plain `LIKE 'x%'`, and §8.1's
# promise -- the one §17 names as 6A's reason to exist on its own -- is that an infix finds
# it. Five digits, comfortably over the three-character floor, and distinctive enough that
# the per-class result limit cannot push this row out of the group.
VAT_NUMBER = "97531864200"
VAT_FRAGMENT = VAT_NUMBER[3:8]

# How long the stalled offer has been waiting. A literal number of days back from *today*,
# never a literal date: a fixed date would make the age drift by one every morning and the
# assertion below would be true only on the day it was written.
DAYS_STALLED = 21


class Corpus(NamedTuple):
    server: Any
    factory: sessionmaker[Any]
    customer_id: UUID
    deal_id: UUID


def _payload(result: Any) -> dict[str, Any]:
    return result.structured_content or json.loads(result.content[0].text)


def _text_of(rendered: Any) -> str:
    """Every text block of every message, joined.

    `Message.content` is one `ContentBlock` in `mcp==2.0.0`, not a list, but the list form
    is handled too: a shape assumption is not what this file is testing. Copied from
    `test_mcp_prompts.py` rather than imported, because a test helper imported across two
    test modules makes the second one fail for reasons that live in the first.
    """
    blocks: list[str] = []
    for message in rendered.messages:
        content = message.content
        for block in content if isinstance(content, list) else [content]:
            if getattr(block, "type", None) == "text":
                blocks.append(block.text)
    return "\n".join(blocks)


@pytest.fixture
def cycle_server(mcp_engine: Engine, tmp_path: Path) -> Iterator[Corpus]:
    """One customer, one open deal, and two offers on it: one still `sent` and stalled,
    one already `accepted` while the deal was never moved.

    The second offer is the whole reason the signal has anything to count. "Offer
    accepted, deal not won" is what automation A1 *not* firing looks like from the
    dashboard, and a corpus without such a row would let the assertion below pass against a
    count wired to nothing.
    """
    factory = session_factory(mcp_engine)
    with factory() as session:
        # `code=None`, so these are user-created stages as far as `seed_defaults` is
        # concerned and never collide with a seeded `lead`/`vinto` on the unique index.
        # `position` well past the defaults, so nothing already there is reordered.
        open = PipelineStage(
            name=STAGE_OPEN, position=920, default_probability=50, type="open", code=None
        )
        won = PipelineStage(
            name=STAGE_WON, position=921, default_probability=100, type="won", code=None
        )
        customer = Customer(
            legal_name=CUSTOMER, vat_number=VAT_NUMBER, country="IT", custom_fields={}
        )
        session.add_all([open, won, customer])
        session.flush()

        deal = Deal(
            name=STALLED_DEAL,
            customer_id=customer.id,
            pipeline_stage_id=open.id,
            expected_value=Decimal("18000.00"),
            probability=60,
            custom_fields={},
        )
        session.add(deal)
        session.flush()

        session.add_all(
            [
                Document(
                    deal_id=deal.id,
                    type="offer",
                    title=OFFER_STALLED,
                    status="sent",
                    status_since=today_local() - timedelta(days=DAYS_STALLED),
                    current_version=1,
                    custom_fields={},
                ),
                # Accepted, on a deal still in an `open` stage. Written straight to the
                # column rather than through `set_offer_state`, which would fire A1 and win
                # the deal -- the opposite of the state this row exists to represent.
                Document(
                    deal_id=deal.id,
                    type="offer",
                    title=OFFER_ACCEPTED,
                    status="accepted",
                    status_since=today_local() - timedelta(days=DAYS_STALLED + 7),
                    current_version=1,
                    custom_fields={},
                ),
            ]
        )
        customer_id, deal_id = customer.id, deal.id
        session.commit()

    try:
        yield Corpus(
            server=build_server(
                ScopedSessionProvider(factory), lambda: AGENT_ACTOR, LocalFileStorage(tmp_path)
            ),
            factory=factory,
            customer_id=customer_id,
            deal_id=deal_id,
        )
    finally:
        with factory() as session:
            # Children before parents, and every clause scoped to this file's own prefix:
            # a wholesale delete would take another file's committed rows with it.
            session.execute(delete(Document).where(Document.title.like(f"{_PREFIX} %")))
            session.execute(delete(Deal).where(Deal.name.like(f"{_PREFIX} %")))
            session.execute(delete(Customer).where(Customer.legal_name.like(f"{_PREFIX} %")))
            session.execute(delete(PipelineStage).where(PipelineStage.name.like(f"{_PREFIX} %")))
            session.commit()
    # `automation_config` is deliberately left alone: `describe_automations` creates the
    # single row on demand and this file never changes it, so what would be left behind is a
    # row carrying the defaults -- indistinguishable from the one the next reader would
    # create. Deleting a single-row table another file may equally have created is the worse
    # leftover of the two.


async def test_the_agent_runs_the_weekly_review_end_to_end(cycle_server: Corpus) -> None:
    """The four steps criterion 15 gives the agent, in its order, over one corpus.

    Each of the four is tested on its own elsewhere. What is only true here is that they are
    about the same rows: the deal the briefing calls stalled is the deal the dashboard
    counts, and the customer behind it is reachable from a fragment of a VAT number that
    appears in neither the briefing nor the dashboard.
    """
    async with Client(cycle_server.server) as client:
        # 1. Open the prompt. The context arrives *inside* it -- which is the whole reason
        #    these are prompts and not an instruction to go and fetch something.
        briefing = _text_of(await client.get_prompt("pipeline-review"))
        assert "## Pipeline per stato" in briefing
        assert "## Offerte inviate in attesa di risposta" in briefing
        assert STAGE_OPEN in briefing, "the stage row is missing, so nothing was read"
        # The age, not merely the title: an offer listed with no age is not a stalled offer,
        # it is a row. `— ferma da 0 giorni` would read as "sent today", which is a
        # different fact and the one `giorni is None` gets wrong.
        assert f"- {OFFER_STALLED} — ferma da {DAYS_STALLED} giorni" in briefing

        # 2. Read the dashboard as data, which is what a tool is for. The briefing is prose
        #    for a model to reason over; the tool is the same figures in a shape it can
        #    compare.
        board = await client.call_tool("get_commercial_dashboard", {})
        assert not board.is_error, str(board.content)
        payload = _payload(board)
        assert payload["offers_pending_total"] >= 1

        # 3. The stalled deal is identifiable *as stalled*: the offer carries its age, and
        #    the age is the seeded one rather than any number at all.
        stalled = next(
            offer for offer in payload["offers_pending"] if offer["title"] == OFFER_STALLED
        )
        assert stalled["days"] == DAYS_STALLED

        # 4. Resolve the customer from a fragment of its VAT number. The fragment appears in
        #    neither the briefing nor the dashboard payload, which is what makes this a
        #    *resolution* and not a lookup of something already in hand.
        assert VAT_FRAGMENT not in briefing
        found = await client.call_tool("search_everything", {"term": VAT_FRAGMENT})
        assert not found.is_error, str(found.content)
        customers = next(
            group for group in _payload(found)["groups"] if group["entity"] == "customer"
        )
        assert CUSTOMER in [hit["label"] for hit in customers["hits"]]
        assert str(cycle_server.customer_id) in [hit["id"] for hit in customers["hits"]]

        # 5. And the review ends where §11.1 says it must: with no tool to change what the
        #    system will do next. Asserted as the **absence of a tool** rather than as a
        #    refusal, because while residuo R10 is open -- a PAT has no scopes and inherits
        #    its owner's full role -- an authorisation check would let an admin token
        #    straight through. `test_mcp_dashboard.py` owns this assertion on its own; it is
        #    repeated here because it is the last step of the cycle, and a cycle that
        #    stopped one step early would not be the criterion.
        names = {tool.name for tool in (await client.list_tools()).tools}
        assert "update_automation_config" not in names
        assert not [name for name in names if "automation" in name and "update" in name]


async def test_the_economic_dashboard_the_agent_reads_passes_criterion_one(
    cycle_server: Corpus, mcp_engine: Engine
) -> None:
    """Criterion 1 through the MCP surface, not only through REST.

    §11.1's claim is that the tool returns *the same figures from the same owning service*,
    never a second version -- and `EconomicDashboard.pnl` embeds `PeriodPnl` verbatim rather
    than flattening it, precisely so this reconciliation is an identity and not a
    comparison. Checked against the service on its own fresh session over the same committed
    corpus, never against figures recomputed here: a test that re-adds the numbers itself is
    the second source of truth this whole slice exists to refuse.

    The month comes from the clock, not from a literal: a fixed month would make this test
    start failing on the first of the next one.
    """
    today = today_local()
    month_start, month_end = month_bounds(today.year, today.month)

    async with Client(cycle_server.server) as client:
        result = await client.call_tool(
            "get_economic_dashboard",
            {"date_from": month_start.isoformat(), "date_to": month_end.isoformat()},
        )
    assert not result.is_error, str(result.content)

    with session_factory(mcp_engine)() as session:
        direct = AnalyticsService(session).period_pnl(
            PeriodPnlQuery(date_from=month_start, date_to=month_end, customer_id=None), AGENT_ACTOR
        )
    assert _payload(result)["pnl"] == direct.model_dump(mode="json")


async def test_the_agent_sees_the_signal_and_the_automation_that_explains_it(
    cycle_server: Corpus,
) -> None:
    """The pair that makes an automation observable at all.

    The signal counts what did not happen; `describe_automations` says what was supposed to.
    Without both, "it did not fire" and "it was not supposed to fire" are the same empty
    screen (§9.5) -- and the count on its own would be a number nobody can act on.

    `describe_automations` is also the shape of the exclusion: it reads, and there is no
    counterpart that writes. Its keys are asserted exactly, because a fourth key appearing
    here would most likely be a configuration mutation arriving by another name.
    """
    async with Client(cycle_server.server) as client:
        board = await client.call_tool("get_commercial_dashboard", {})
        described = await client.call_tool("describe_automations", {})

    assert _payload(board)["accepted_offers_deal_not_won"] >= 1

    assert not described.is_error, str(described.content)
    description = _payload(described)
    assert set(description) == {"configuration", "rules", "runs"}
    # A1 is named, in prose, in the same words the settings screen shows: the agent and the
    # human read one description, so the two surfaces cannot drift into two explanations of
    # the same rule.
    a1 = next(rule for rule in description["rules"] if rule["code"] == "A1")
    assert a1["title"] == "Offerta accettata → deal vinto"
    # The prose says *which* transition fires it, which is what turns the count above from a
    # number into something a reader can act on.
    assert "«accettata»" in a1["description"]
    assert "«vinto»" in a1["description"]
