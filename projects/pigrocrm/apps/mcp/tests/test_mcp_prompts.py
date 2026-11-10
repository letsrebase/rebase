"""§10's four prompts. Criterion 10's own assertions are added in Task C9.

What is asserted here is that they exist, that their arguments are inferred from their
signatures, that each one carries its context *inside* the returned messages, and that a
prompt rendered against an empty database produces a valid message rather than an exception
-- the criterion names that last one explicitly, and it is the failure mode of every
"assemble a briefing" function ever written.

**This file builds its own servers, and that is the subject as much as the setup.** Three of
the four prompts open a `REPEATABLE READ` snapshot through `DashboardService`, which refuses
a session that is already in a transaction. The package's `server` fixture wires
`build_server(lambda: mcp_session, ...)` -- one `Session` shared by every call, held inside
an outer transaction -- so it is the wrong server for a prompt for exactly the reason
`test_mcp_dashboard.py` records. The servers here are wired the way `__main__.py` wires
production: a `ScopedSessionProvider`, one session per logical call, opened untouched.

**And the empty database is a real one.** "Renders on an empty corpus" is not something
`mcp_engine` can be asked -- it is session-scoped and earlier files in this package commit
rows that outlive them -- and a prompt that crashes on an empty register is precisely the
defect the criterion names, so faking it with a far-past period would have tested the one
thing that is easy instead of the one thing that is asked. `empty_engine` therefore creates
a second *database* on the same container, builds the schema in it, and drops it afterwards:
a second server, genuinely empty, for about a second of setup and no second container.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, NamedTuple
from uuid import UUID, uuid4

import pytest
from mcp import Client
from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_PARAMS
from sqlalchemy import Engine, create_engine, delete, text
from sqlalchemy.orm import sessionmaker

from pigrocrm.core.activities.models import Activity
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.config import Settings
from pigrocrm.core.db import Base, session_factory, today_local
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.documents.models import Document
from pigrocrm.core.due_items.schemas import SettlementCreate
from pigrocrm.core.due_items.service import DueItemService
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.pipeline.models import PipelineStage
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm.core.timetracking.models import Cost, CostCategory, TimeEntry
from pigrocrm_mcp.context import ScopedSessionProvider
from pigrocrm_mcp.errors import to_agent_message
from pigrocrm_mcp.prompts import dashboards as dashboard_prompts
from pigrocrm_mcp.prompts import organization as organization_prompts
from pigrocrm_mcp.server import DOMAIN_REFUSAL, build_server

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "core" / "tests"))
from fakes.legal_entity_fixtures import (  # noqa: E402
    committed_default_legal_entity,
    remove_legal_entity,
)

ADMIN = Actor(id=None, type="mcp", role="admin")
_PREFIX = "MCPPROMPT"
# The month `month-close` is asked about everywhere in this file. In the past, and fixed:
# a P&L over a moving month would make every figure in the value-by-value comparison depend
# on the day the suite runs.
_YEAR, _MONTH = 2026, 3

PROMPT_NAMES = {"pipeline-review", "month-close", "organization-status", "hours-to-log"}


class Corpus(NamedTuple):
    server: Any
    factory: sessionmaker[Any]
    customer_id: UUID
    open_deal_name: str
    won_deal_name: str


class _Identified(NamedTuple):
    """What the tests need of the seeded customer, and nothing else -- an ORM instance would
    be detached from the committed session it came from."""

    id: UUID


class _Seeded(NamedTuple):
    customer_id: UUID
    open_deal_name: str
    won_deal_name: str
    # The default azienda this corpus wrote for its invoices (REB-619), to remove last;
    # `None` when another file's world already held one.
    legal_entity_id: UUID | None


def _seed(factory: sessionmaker[Any]) -> _Seeded:
    """One corpus, committed, carrying something for every section of every prompt.

    Committed and not flushed: a scoped session never sees another transaction's
    uncommitted rows, which is the point of it. Prefixed and additive rather than wholesale,
    for the reason `test_mcp_dashboard.py` gives -- `mcp_engine` is session-scoped and
    earlier files commit deals that outlive them, so emptying `pipeline_stages` here would
    fail on somebody else's foreign keys and would be wrong even if it did not.
    """
    with factory() as session:
        legal_entity_id = committed_default_legal_entity(session)
        # `code=None`, so these are user-created stages as far as `seed_defaults` is
        # concerned and never collide with a seeded `lead`/`vinto` on the unique index.
        open = PipelineStage(
            name=f"{_PREFIX} aperto", position=910, default_probability=50, type="open", code=None
        )
        won = PipelineStage(
            name=f"{_PREFIX} vinto", position=911, default_probability=100, type="won", code=None
        )
        category = CostCategory(name=f"{_PREFIX} generali", position=910, code=None)
        user = User(email=f"{_PREFIX.lower()}@example.test", password_hash="x", name="Worker")
        customer = Organization(legal_name=f"{_PREFIX} Cliente", country="IT", custom_fields={})
        session.add_all([open, won, category, user, customer])
        session.flush()

        open_deal = Deal(
            name=f"{_PREFIX} in corso",
            customer_id=customer.id,
            pipeline_stage_id=open.id,
            expected_value=Decimal("1000.00"),
            probability=50,
            custom_fields={},
        )
        won_deal = Deal(
            name=f"{_PREFIX} vinto",
            customer_id=customer.id,
            pipeline_stage_id=won.id,
            expected_value=Decimal("5000.00"),
            probability=100,
            closed_at=date(_YEAR, _MONTH, 20),
            custom_fields={},
        )
        session.add_all([open_deal, won_deal])
        session.flush()

        # Two offers in `sent`: one with a known age, one whose `status_since` was never
        # recorded. The second is the row that makes `giorni is None` reachable, and
        # "stalled for 0 days" -- which would read as "sent today" -- is the defect it pins.
        session.add_all(
            [
                # `ck_documents_customer_xor_deal`: one owner, never both. The dated offer
                # hangs off the deal and the undated one off the customer, so the two rows
                # also cover both spellings of ownership.
                Document(
                    deal_id=open_deal.id,
                    type="offer",
                    title=f"{_PREFIX} offerta datata",
                    status="sent",
                    status_since=today_local() - timedelta(days=21),
                    current_version=1,
                    custom_fields={},
                ),
                Document(
                    customer_id=customer.id,
                    type="offer",
                    title=f"{_PREFIX} offerta senza data",
                    status="sent",
                    status_since=None,
                    current_version=1,
                    custom_fields={},
                ),
            ]
        )

        # Both P&L columns non-zero: one invoice on the closed deal, one on the open one.
        # `deal_id` is load-bearing -- `revenue_in_range` groups by it and skips nulls, so a
        # corpus of unlinked invoices would report zero revenue and every value-by-value
        # comparison would be between two zeros.
        session.add_all(
            [
                Invoice(
                    customer_id=customer.id,
                    deal_id=won_deal.id,
                    type="invoice",
                    status="issued",
                    payment_status="unpaid",
                    year=_YEAR,
                    number=9001,
                    taxable_amount=Decimal("4000.00"),
                    vat_amount=Decimal("0.00"),
                    stamp_duty=Decimal("0.00"),
                    total=Decimal("4000.00"),
                    issue_date=date(_YEAR, _MONTH, 10),
                    due_date=date(_YEAR, _MONTH, 31),
                    memo=f"{_PREFIX} saldo",
                    sdi_document_type="TD01",
                    currency="EUR",
                    custom_fields={},
                ),
                Invoice(
                    customer_id=customer.id,
                    deal_id=open_deal.id,
                    type="invoice",
                    status="issued",
                    payment_status="unpaid",
                    year=_YEAR,
                    number=9002,
                    taxable_amount=Decimal("750.00"),
                    vat_amount=Decimal("0.00"),
                    stamp_duty=Decimal("0.00"),
                    total=Decimal("750.00"),
                    issue_date=date(_YEAR, _MONTH, 18),
                    due_date=None,
                    memo=f"{_PREFIX} acconto",
                    sdi_document_type="TD01",
                    currency="EUR",
                    custom_fields={},
                ),
            ]
        )

        # Two costs and two time entries, one of each on the *closed* deal and one on the
        # open one, plus a general expense. The P&L has two columns and criterion 10 compares
        # them value by value, so a corpus that only touched one column would leave the other
        # at zero -- and a zero on both sides of a comparison passes on any implementation at
        # all, which is what makes the non-zero check in that test more than a formality.
        session.add_all(
            [
                Cost(
                    deal_id=won_deal.id,
                    category_id=category.id,
                    date=date(_YEAR, _MONTH, 8),
                    amount=Decimal("310.00"),
                    description=f"{_PREFIX} costo diretto sul vinto",
                    custom_fields={},
                ),
                # `deal_id IS NULL`: a general expense lands in `general_expenses` and on no
                # deal's margin.
                Cost(
                    deal_id=None,
                    category_id=category.id,
                    date=date(_YEAR, _MONTH, 5),
                    amount=Decimal("120.00"),
                    description=f"{_PREFIX} spesa generale",
                    custom_fields={},
                ),
            ]
        )
        # Billable, unbilled, with a rate: the only way `accrued_value` and
        # `billable_uninvoiced_hours` are non-zero on both the period P&L and the
        # register-wide backlog. `applied_cost` is what makes `work_cost` non-zero.
        session.add_all(
            [
                TimeEntry(
                    deal_id=open_deal.id,
                    user_id=user.id,
                    date=date(_YEAR, _MONTH, 12),
                    hours=Decimal("6.00"),
                    description=f"{_PREFIX} analisi",
                    billable=True,
                    applied_rate=Decimal("80.000000"),
                    rate_source="manual",
                    applied_cost=Decimal("30.000000"),
                    cost_source="manual",
                    custom_fields={},
                ),
                # `billable=False`, and it is what puts this deal in the `closed` column
                # at all. A deal's P&L state is not its stage: `deal_summary` calls a deal on
                # a `won` stage "da fatturare" -- which lands in `in_progress` -- for as long as
                # it has one billable hour nobody has invoiced. A non-billable hour still
                # costs exactly what it would have cost billed, so `closed.work_cost` is
                # non-zero all the same, which is the figure this row exists to produce.
                TimeEntry(
                    deal_id=won_deal.id,
                    user_id=user.id,
                    date=date(_YEAR, _MONTH, 14),
                    hours=Decimal("9.00"),
                    description=f"{_PREFIX} consegna",
                    billable=False,
                    applied_rate=None,
                    rate_source="absent",
                    applied_cost=Decimal("30.000000"),
                    cost_source="manual",
                    custom_fields={},
                ),
            ]
        )
        # The global recent feed is what `hours-to-log` reads to propose a deal. Two
        # entries on the same deal, so the deduplication has something to do.
        session.add_all(
            [
                Activity(
                    entity_type="deal",
                    entity_id=open_deal.id,
                    kind=f"{_PREFIX}_touched",
                    actor_id=None,
                    actor_type="system",
                    payload={},
                ),
                Activity(
                    entity_type="deal",
                    entity_id=open_deal.id,
                    kind=f"{_PREFIX}_touched_again",
                    actor_id=None,
                    actor_type="system",
                    payload={},
                ),
            ]
        )

        seeded = _Seeded(
            customer_id=customer.id,
            open_deal_name=open_deal.name,
            won_deal_name=won_deal.name,
            legal_entity_id=legal_entity_id,
        )
        session.commit()

    return seeded


def _teardown(factory: sessionmaker[Any], legal_entity_id: UUID | None = None) -> None:
    with factory() as session:
        # Children before parents. Everything is scoped to this file's prefix rather than
        # truncated, so a wholesale delete cannot take another file's committed rows with
        # it -- the activities by their own `kind`, which no other file writes.
        session.execute(delete(Activity).where(Activity.kind.like(f"{_PREFIX}_%")))
        session.execute(delete(TimeEntry).where(TimeEntry.description.like(f"{_PREFIX} %")))
        session.execute(delete(Cost).where(Cost.description.like(f"{_PREFIX} %")))
        session.execute(delete(Invoice).where(Invoice.memo.like(f"{_PREFIX} %")))
        session.execute(delete(Document).where(Document.title.like(f"{_PREFIX} %")))
        session.execute(delete(Deal).where(Deal.name.like(f"{_PREFIX} %")))
        session.execute(delete(Organization).where(Organization.legal_name.like(f"{_PREFIX} %")))
        session.execute(delete(CostCategory).where(CostCategory.name.like(f"{_PREFIX} %")))
        session.execute(delete(User).where(User.email.like(f"{_PREFIX.lower()}%")))
        session.execute(delete(PipelineStage).where(PipelineStage.name.like(f"{_PREFIX} %")))
        remove_legal_entity(session, legal_entity_id)
        session.commit()


@pytest.fixture
def prompt_corpus(mcp_engine: Engine, tmp_path: Path) -> Iterator[Corpus]:
    factory = session_factory(mcp_engine)
    seeded = _seed(factory)
    try:
        yield Corpus(
            server=build_server(
                ScopedSessionProvider(factory), lambda: ADMIN, LocalFileStorage(tmp_path)
            ),
            factory=factory,
            customer_id=seeded.customer_id,
            open_deal_name=seeded.open_deal_name,
            won_deal_name=seeded.won_deal_name,
        )
    finally:
        _teardown(factory, seeded.legal_entity_id)


@pytest.fixture
def mcp_server(prompt_corpus: Corpus) -> Any:
    return prompt_corpus.server


@pytest.fixture
def seeded_customer(prompt_corpus: Corpus) -> _Identified:
    return _Identified(id=prompt_corpus.customer_id)


@pytest.fixture(scope="module")
def empty_engine(mcp_engine: Engine) -> Iterator[Engine]:
    """A second, genuinely empty database on the same PostgreSQL server.

    A second *database*, not a second container: `CREATE DATABASE` costs about a second and
    the container is already running. Nothing else in this repository has needed one, which
    is why the reason is written here rather than assumed -- "the prompt renders on an empty
    register" is a claim about a register with no rows in it, and `mcp_engine` cannot make
    that claim on behalf of a session-scoped fixture other files commit into.

    `AUTOCOMMIT`, because `CREATE DATABASE` cannot run inside a transaction block.
    """
    name = "pigrocrm_prompts_empty"
    quoted = f'"{name}"'
    with mcp_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text(f"DROP DATABASE IF EXISTS {quoted}"))
        connection.execute(text(f"CREATE DATABASE {quoted}"))
    engine = create_engine(mcp_engine.url.set(database=name))
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
    import pigrocrm.core.models_registry  # noqa: F401

    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()
        with mcp_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS {quoted}"))


@pytest.fixture
def empty_server(empty_engine: Engine, tmp_path: Path) -> Any:
    return build_server(
        ScopedSessionProvider(session_factory(empty_engine)),
        lambda: ADMIN,
        LocalFileStorage(tmp_path),
    )


def _text_of(rendered: Any) -> str:
    """Every text block of every message, joined.

    `Message.content` is one `ContentBlock` in `mcp==2.0.0`, not a list, but the list form
    is handled too: a shape assumption is not what this file is testing.
    """
    blocks: list[str] = []
    for message in rendered.messages:
        content = message.content
        for block in content if isinstance(content, list) else [content]:
            if getattr(block, "type", None) == "text":
                blocks.append(block.text)
    return "\n".join(blocks)


def _resource_uris(rendered: Any) -> list[str]:
    uris: list[str] = []
    for message in rendered.messages:
        content = message.content
        for block in content if isinstance(content, list) else [content]:
            if getattr(block, "type", None) == "resource":
                uris.append(str(block.resource.uri))
    return uris


def _resource_text(rendered: Any) -> str:
    parts: list[str] = []
    for message in rendered.messages:
        content = message.content
        for block in content if isinstance(content, list) else [content]:
            if getattr(block, "type", None) == "resource":
                parts.append(block.resource.text)
    return "\n".join(parts)


# -- the surface -----------------------------------------------------------------


async def test_all_four_prompts_are_registered_with_their_names(mcp_server: Any) -> None:
    names = {prompt.name for prompt in await mcp_server.list_prompts()}
    assert names == PROMPT_NAMES


async def test_the_arguments_are_inferred_from_the_signatures(mcp_server: Any) -> None:
    """The SDK infers them exactly as it does for tools, so the signature *is* the schema.

    Pinned because a default moving from optional to required is a silent contract break for
    a menu the user drives -- and because the inference reads `inspect.signature`, which sees
    the guard's bare `(*args, **kwargs)` the moment `functools.wraps` is lost. That failure
    already cost the tools once; here it would show up as four prompts each taking `args`
    and `kwargs`.
    """
    by_name = {prompt.name: prompt for prompt in await mcp_server.list_prompts()}

    assert {arg.name: arg.required for arg in by_name["pipeline-review"].arguments} == {
        "date_from": False,
        "date_to": False,
    }
    assert {arg.name: arg.required for arg in by_name["month-close"].arguments} == {
        "year": True,
        "month": True,
    }
    assert {arg.name: arg.required for arg in by_name["organization-status"].arguments} == {
        "organization_id": True
    }
    assert {arg.name: arg.required for arg in by_name["hours-to-log"].arguments} == {"week": False}


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("pipeline-review", {}),
        ("month-close", {"year": _YEAR, "month": _MONTH}),
        ("hours-to-log", {}),
    ],
)
async def test_every_prompt_returns_at_least_one_user_message(
    mcp_server: Any, name: str, args: dict[str, Any]
) -> None:
    """A prompt whose only message is `assistant` puts words in the model's mouth and gives
    the user nothing to send."""
    rendered = await mcp_server.get_prompt(name, args)
    assert rendered.messages
    assert any(message.role == "user" for message in rendered.messages), name


# -- pipeline-review ----------------------------------------------------------


async def test_review_pipeline_carries_the_dashboard_as_text(mcp_server: Any) -> None:
    """The context arrives *inside* the prompt rather than depending on the model going to
    fetch it -- which is the whole reason these are prompts. Asserted on the figures, not
    only on the headings: a template with every value missing has all the same words in it.
    """
    text_block = _text_of(await mcp_server.get_prompt("pipeline-review", {}))
    assert f"{_PREFIX} aperto" in text_block, "the stage row is missing, so nothing was read"
    # The closed stages are in the table too since 2026-09-09, with the caveat that says
    # what they count. The corpus puts one deal in `{_PREFIX} vinto`.
    assert f"| {_PREFIX} vinto | 1 |" in text_block


async def test_review_pipeline_lists_the_pending_offers_with_their_age(
    mcp_server: Any,
) -> None:
    text_block = _text_of(await mcp_server.get_prompt("pipeline-review", {}))
    assert f"{_PREFIX} offerta datata — stalled for 21 days" in text_block


async def test_an_offer_with_no_recorded_date_is_of_unknown_age_and_not_zero_days(
    mcp_server: Any,
) -> None:
    """`giorni is None` is the branch every briefing function gets wrong, and it gets it
    wrong in the one direction that misleads: "stalled for 0 days" reads as "sent today",
    which is the opposite of what an offer with no recorded date might be."""
    text_block = _text_of(await mcp_server.get_prompt("pipeline-review", {}))
    assert f"{_PREFIX} offerta senza data — stalled for unknown date" in text_block
    assert "stalled for 0 days" not in text_block


async def test_a_period_where_nothing_closed_reports_a_dash_and_not_zero_per_cent(
    mcp_server: Any,
) -> None:
    """`conversion_rate` is `None`, never `0`, when nothing closed. Zero per cent means "I
    lost everything"; nothing closed means something else, and a briefing that renders the
    second as the first is telling the reader a bad month was a catastrophe."""
    rendered = await mcp_server.get_prompt(
        "pipeline-review", {"date_from": "2019-01-01", "date_to": "2019-01-31"}
    )
    text_block = _text_of(rendered)
    assert "Conversion rate: —" in text_block


async def test_review_pipeline_on_an_empty_database_is_to_valid_message(
    empty_server: Any,
) -> None:
    """§16 criterion 10's last sentence, on a database with no rows at all -- not on a
    period that happens to be empty. It is the failure mode of every briefing function:
    an empty pipeline, no offers, and a `None` conversion rate, all at once.
    """
    rendered = await empty_server.get_prompt("pipeline-review", {})
    assert rendered.messages
    assert all(message.role in ("user", "assistant") for message in rendered.messages)
    text_block = _text_of(rendered)
    assert "Conversion rate: —" in text_block


# -- month-close ---------------------------------------------------------------


async def test_closing_month_takes_to_year_and_to_month(mcp_server: Any) -> None:
    text_block = _text_of(await mcp_server.get_prompt("month-close", {"year": _YEAR, "month": 3}))
    assert "2026-03" in text_block


async def test_closing_month_refuses_month_thirteen_by_name(mcp_server: Any) -> None:
    """`month_bounds` owns the rule and names the field; the prompt adds no second copy of
    it. The guard carries the diagnosis through, so the message the caller sees still says
    which argument to change.

    Asserted on the whole sentence a client receives: «mese» alone is also in the prompt's
    name, so mcp 2.2's bare «Error rendering prompt month-close» passed the old check while
    the diagnosis was lost (REB-451)."""
    async with Client(mcp_server) as client:
        with pytest.raises(MCPError) as caught:
            await client.get_prompt("month-close", {"year": str(_YEAR), "month": "13"})

    assert caught.value.message == to_agent_message(
        ValidationFailed("period", "month", "mese fuori intervallo", expected="1-12")
    )
    assert caught.value.code == DOMAIN_REFUSAL


async def test_closing_month_on_an_empty_database_renders_to_null_margin_as_to_dash(
    empty_server: Any,
) -> None:
    """The second `None` branch, on the other prompt that has one: `percentage_margin` is
    `None` when revenue is zero, and `f"{None:.2f}"` is a `TypeError` in production."""
    text_block = _text_of(
        await empty_server.get_prompt("month-close", {"year": _YEAR, "month": _MONTH})
    )
    assert "| Margin % | — | — |" in text_block


async def test_closing_month_leaves_out_labour_and_hours_while_time_is_off(
    prompt_corpus: Corpus, tmp_path: Path
) -> None:
    """Without the `time` module the prompt has no labour cost, no hours and no backlog to
    report, so the lines are not printed and the closing advice does not name them."""
    arguments = {"year": _YEAR, "month": 3}
    on = _text_of(await prompt_corpus.server.get_prompt("month-close", arguments))
    switched_off = build_server(
        ScopedSessionProvider(prompt_corpus.factory),
        lambda: ADMIN,
        LocalFileStorage(tmp_path),
        Settings(_env_file=None, module_time=False),  # type: ignore[call-arg]
    )
    off = _text_of(await switched_off.get_prompt("month-close", arguments))

    for phrase in ("Labor cost", "Billable uninvoiced hours", "entries without a rate"):
        assert phrase in on
        assert phrase not in off
    # What is left of the arrears is the approved contract days, still reported.
    assert "Approved contract days not yet invoiced" in off
    assert "written late" in on and "written late" not in off
    assert "unbilled hours" in on and "unbilled hours" not in off
    # What is not time's stays.
    assert "| Gross margin |" in off and "Invoices issued in the period" in off


# -- hours-to-log -----------------------------------------------------------


async def test_hours_to_log_names_the_days_and_the_tool_that_records_them(
    mcp_server: Any,
) -> None:
    """The prompt §6 calls the most useful in the product: it attacks the "I never entered
    Tuesday" failure slice 4 §13 names when it refuses a stopwatch, and it ends by asking
    the user what they did so it can call `log_time` -- a tool the agent has."""
    text_block = _text_of(await mcp_server.get_prompt("hours-to-log", {}))
    assert "log_time" in text_block


async def test_hours_to_log_names_the_recent_deal_and_names_it_once(
    mcp_server: Any, prompt_corpus: Corpus
) -> None:
    """Names, not identifiers, and each deal once however many times the feed mentions it.

    A list of UUIDs would be a section nobody can answer from -- cost with no context in it,
    which is exactly what §10 is about -- and the corpus writes two activities on the same
    deal so a missing deduplication shows up here rather than as a briefing that lists one
    deal eight times.
    """
    text_block = _text_of(await mcp_server.get_prompt("hours-to-log", {}))
    assert text_block.count(f"- {prompt_corpus.open_deal_name}") == 1


async def test_a_past_week_is_refused_rather_than_answered_with_the_current_one(
    mcp_server: Any,
) -> None:
    """An argument the prompt advertises and answers with a different week's figures is
    worse than one it does not have: the caller would believe it worked."""
    text_block = _text_of(await mcp_server.get_prompt("hours-to-log", {"week": "2020-01-06"}))
    assert "2020-01-06" in text_block
    assert "| Day | Hours |" not in text_block


async def test_hours_to_log_on_an_empty_database_is_to_valid_message(
    empty_server: Any,
) -> None:
    rendered = await empty_server.get_prompt("hours-to-log", {})
    text_block = _text_of(rendered)
    # Seven days, all of them empty: an empty week is seven zeros, never an empty series.
    assert text_block.count("| 2") == 7


# -- organization-status ---------------------------------------------------------------


async def test_status_customer_embeds_the_existing_resource(
    mcp_server: Any, seeded_customer: _Identified
) -> None:
    """§10: the **only** prompt with a resource block, because it is the only one whose
    resource already exists. `organization://{id}` is slice 1 §8.4's."""
    rendered = await mcp_server.get_prompt(
        "organization-status", {"organization_id": str(seeded_customer.id)}
    )
    assert _resource_uris(rendered) == [f"organization://{seeded_customer.id}"]


async def test_the_embedded_resource_carries_the_card_and_not_only_the_uri(
    mcp_server: Any, seeded_customer: _Identified, prompt_corpus: Corpus
) -> None:
    """`TextResourceContents.text` is required by the protocol types, and that is what makes
    the resource block *be* the context rather than a pointer to it: a client that cannot
    follow the URI still has the card, so "the context arrives inside the prompt" holds
    without the model going to fetch anything."""
    rendered = await mcp_server.get_prompt(
        "organization-status", {"organization_id": str(seeded_customer.id)}
    )
    card = _resource_text(rendered)
    assert f"# {_PREFIX} Cliente" in card
    assert prompt_corpus.open_deal_name in card


async def test_status_customer_does_not_repeat_the_deals_the_card_already_carries(
    mcp_server: Any, seeded_customer: _Identified, prompt_corpus: Corpus
) -> None:
    """The tax half of §10, and the reason this prompt diverges from the obvious shape.

    `render_organization` already lists the deals, and the resource block must carry its text,
    so a "## Deal" section in the text block beside it would be the most expensive section
    of the message paid for twice -- for rows the reader already has. What the card has no
    notion of is money owed, so that is what the text block is.
    """
    rendered = await mcp_server.get_prompt(
        "organization-status", {"organization_id": str(seeded_customer.id)}
    )
    assert prompt_corpus.open_deal_name not in _text_of(rendered)
    assert prompt_corpus.open_deal_name in _resource_text(rendered)


async def test_status_customer_prints_what_is_still_owed_on_a_partly_paid_invoice(
    mcp_server: Any, seeded_customer: _Identified, mcp_engine: Engine
) -> None:
    """The briefing reads the due item, not the invoice's frozen total: 4000 paid 1000 is 3000
    still owed, with the total beside it, and the invoice nobody paid keeps its old line."""
    with session_factory(mcp_engine)() as session:
        invoice = session.query(Invoice).filter(Invoice.memo == f"{_PREFIX} saldo").one()
        item = DueItemService(session).repo.by_source("sales_invoice", invoice.id)
        assert item is not None
        DueItemService(session).settle(
            item.id,
            SettlementCreate(amount=Decimal("1000.00"), settled_on=today_local()),
            Actor(id=None, type="system", role="admin"),
        )
    text_block = _text_of(
        await mcp_server.get_prompt(
            "organization-status", {"organization_id": str(seeded_customer.id)}
        )
    )
    assert (
        f"- {_YEAR}/9001 — 3000,00 € still owed of 4000,00 € (total incl. VAT), "
        f"due {_YEAR}-03-31" in text_block
    )
    assert f"- {_YEAR}/9002 — 750,00 € (total incl. VAT), due no due date" in text_block


async def test_status_customer_lists_the_unpaid_invoices_with_their_deadline(
    mcp_server: Any, seeded_customer: _Identified
) -> None:
    """The one thing the card cannot say. Both rows, because the undated one is the branch
    that would otherwise print `None`."""
    text_block = _text_of(
        await mcp_server.get_prompt(
            "organization-status", {"organization_id": str(seeded_customer.id)}
        )
    )
    assert f"- {_YEAR}/9001 — 4000,00 € (total incl. VAT), due {_YEAR}-03-31" in text_block
    assert f"- {_YEAR}/9002 — 750,00 € (total incl. VAT), due no due date" in text_block


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_status_customer_on_an_unknown_customer_reaches_the_assistant_as_the_domain_error(
    mcp_server: Any, mode: Literal["auto", "legacy"]
) -> None:
    """An empty briefing about nobody would read as "this customer has nothing open", which
    is the more dangerous of the two possible answers. So the prompt refuses, and the refusal
    is only worth something if the assistant can read it.

    The assertion is on what a client receives, in both protocol eras, and not on the
    exception the server raises in process. mcp 2.2's `Prompt.render` passes an `MCPError`
    through and replaces anything else with «Error rendering prompt organization-status», with no
    text after it; the modern protocol then answers «Internal server error» for that, and
    the legacy one did already on mcp 2.0 (REB-451). The guard's `MCPError` is what reaches
    the assistant as the domain's own sentence, whole."""
    unknown = uuid4()

    async with Client(mcp_server, mode=mode) as client:
        with pytest.raises(MCPError) as caught:
            await client.get_prompt("organization-status", {"organization_id": str(unknown)})

    assert caught.value.message == to_agent_message(NotFound("organization", unknown))
    assert caught.value.code == INVALID_PARAMS


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_a_conflict_inside_a_prompt_carries_the_application_code(
    mcp_server: Any, mode: Literal["auto", "legacy"], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A conflict is a refusal the domain meant, not a server failure, so it must not share
    `-32603` with a crash: the client reads `DOMAIN_REFUSAL` and the domain's own sentence
    (REB-451)."""
    refusal = Conflict("organization", "briefing già in preparazione")

    def _refuse(context: Any, organization_id: str) -> list[dict[str, Any]]:
        raise refusal

    monkeypatch.setattr(organization_prompts, "status_organization", _refuse)

    async with Client(mcp_server, mode=mode) as client:
        with pytest.raises(MCPError) as caught:
            await client.get_prompt("organization-status", {"organization_id": str(uuid4())})

    assert caught.value.code == DOMAIN_REFUSAL
    assert caught.value.message == to_agent_message(refusal)


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_a_crash_inside_a_prompt_keeps_its_text_on_the_server(
    mcp_server: Any, mode: Literal["auto", "legacy"], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of the guard's contract on a prompt: only a domain error becomes an
    `MCPError`. A programming error is re-raised untouched, so mcp 2.2 treats it as the crash
    it is and the assistant reads the SDK's generic sentence, never the exception's text nor
    a domain rendering that would send it to correct an argument it got right."""

    def _crash(context: Any, organization_id: str) -> list[dict[str, Any]]:
        raise KeyError("boom")

    monkeypatch.setattr(organization_prompts, "status_organization", _crash)

    async with Client(mcp_server, mode=mode) as client:
        with pytest.raises(MCPError) as caught:
            await client.get_prompt("organization-status", {"organization_id": str(uuid4())})

    assert "boom" not in caught.value.message
    assert "Rivedi" not in caught.value.message
    assert caught.value.message in {
        "Internal server error",
        "Error rendering prompt organization-status",
    }
    assert caught.value.code not in {DOMAIN_REFUSAL, INVALID_PARAMS}


# == criterion 10 =================================================================
#
# Two prohibitions and two positive claims, and the second positive one is the reason this
# block exists at all. "The prompts carry the context, and not the tax" is a statement about
# *size*, and a test that only checked the words in a prompt would pass on a briefing that
# pasted the whole register into the model's context window on every render.

# Field names, not concepts. Criterion 10: checked by field name, not by
# intent. The first four are the fiscal estimate's own fields (slice 4 §11); the last
# three are the parameters it is derived from, which are just as sensitive and would let a
# reader reconstruct it.
FORBIDDEN_FISCAL_FIELDS = frozenset(
    {
        "taxable_amount",
        "substitute_tax",
        "contributions",
        "estimated_net_income",
        "profitability_coefficient",
        "substitute_tax_rate",
        "social_security_rate",
    }
)

# The resources that exist. §11.1 adds none, and inventing `dashboard://commercial?da=...`
# in order to have a URI to embed would be adding one without saying so.
KNOWN_RESOURCE_URIS = frozenset({"organization://", "person://", "deal://"})

_ALL_PROMPTS = (
    ("pipeline-review", {}),
    ("month-close", {"year": _YEAR, "month": _MONTH}),
    ("hours-to-log", {}),
)

# How many extra rows the inflation fixture commits. Comfortably above every cap so that a
# cap removed shows up as growth of hundreds of characters rather than of a handful -- a cap
# asserted against a corpus smaller than itself is a comment, not a test.
_EXTRA_ROWS = 40

# What a prompt may grow by when the register grows by `_EXTRA_ROWS` rows it would otherwise
# have listed. Deliberately tiny, and it is what makes the two growth tests below say
# something: they measure the second batch, not the first, so the register is already past
# every cap when the "before" reading is taken and the only legitimate growth left is the
# truncation line's own count going from two digits to two digits. An uncapped section would
# grow by roughly two thousand characters instead, at about fifty characters a row.
_GROWTH_BUDGET = 40


@pytest.fixture
def inflate(prompt_corpus: Corpus) -> Callable[[int], None]:
    """Commit `_EXTRA_ROWS` more sent offers and unpaid invoices, on demand.

    A callable rather than a fixture that has already run, so a test can take its own
    "before" reading and the comparison is between two renders of the same server rather than
    between two servers that differ in more than one way.

    It takes a batch number because the growth tests call it **twice**: once to push the
    register past every cap, and again to measure. Growing from below a cap up to it is
    legitimate growth and would have made the bound meaningless -- the first version of this
    test measured exactly that and reported 440 characters for a cap that was working
    perfectly. Everything it writes carries the file's prefix, so the existing teardown
    removes it.
    """

    def _inflate(batch: int) -> None:
        with prompt_corpus.factory() as session:
            for index in range(_EXTRA_ROWS):
                serial = batch * _EXTRA_ROWS + index
                session.add(
                    Document(
                        customer_id=prompt_corpus.customer_id,
                        type="offer",
                        title=f"{_PREFIX} offerta massa {serial:03d}",
                        status="sent",
                        status_since=today_local() - timedelta(days=serial + 1),
                        current_version=1,
                        custom_fields={},
                    )
                )
                session.add(
                    Invoice(
                        customer_id=prompt_corpus.customer_id,
                        type="invoice",
                        status="issued",
                        payment_status="unpaid",
                        year=_YEAR,
                        number=9100 + serial,
                        taxable_amount=Decimal("10.00"),
                        vat_amount=Decimal("0.00"),
                        stamp_duty=Decimal("0.00"),
                        total=Decimal("10.00"),
                        issue_date=date(_YEAR, _MONTH, 1),
                        due_date=date(_YEAR, _MONTH, 1) + timedelta(days=serial),
                        memo=f"{_PREFIX} massa {serial:03d}",
                        sdi_document_type="TD01",
                        currency="EUR",
                        custom_fields={},
                    )
                )
            session.commit()

    return _inflate


# -- the tax ---------------------------------------------------------------------


async def test_review_pipeline_does_not_grow_with_the_register(
    mcp_server: Any, inflate: Callable[[int], None]
) -> None:
    """**The bound.** A prompt's cost is paid in the model's context window on every render,
    so a section that grows with the register is a tax the user pays for rows nobody asked
    about.

    Asserted as growth rather than as an absolute size: an absolute budget is a number
    somebody tunes upward the first time it fails, while growth is the property itself -- the
    prompt is the same size whether the register holds forty sent offers or eighty.

    The first `inflate` is not the measurement; it is what puts the register past the cap so
    that the measurement means something. Growing from two offers up to the cap of ten is
    legitimate growth, and measuring *that* is how this test read 440 characters for a cap
    that was working perfectly. Removing `[:PENDING_OFFERS_SHOWN]` now fails it by roughly
    two thousand.
    """
    inflate(0)
    before = len(_text_of(await mcp_server.get_prompt("pipeline-review", {})))
    inflate(1)
    after = len(_text_of(await mcp_server.get_prompt("pipeline-review", {})))
    assert after - before <= _GROWTH_BUDGET, (
        f"the prompt grew by {after - before} characters when {_EXTRA_ROWS} offers were "
        "added to a register that was already past the cap"
    )


async def test_review_pipeline_shows_the_postal_code_and_admits_the_rest(
    mcp_server: Any, inflate: Callable[[int], None]
) -> None:
    """The other half of a cap: the rows that are not here are named.

    A briefing that omits its tail silently cannot be told apart from a short register, and
    the count comes from `offers_pending_total` -- the number the owning service
    reported -- not from the length of a list this prompt has already truncated.
    """
    inflate(0)
    text_block = _text_of(await mcp_server.get_prompt("pipeline-review", {}))
    section = text_block.split("## Offers sent awaiting a reply", 1)[1]
    listed = [line for line in section.splitlines() if line.startswith("- ")]
    assert len(listed) == dashboard_prompts.PENDING_OFFERS_SHOWN
    # The omitted rows are admitted in an italic note after the list.
    assert [line for line in section.splitlines() if line.startswith("_")]


async def test_status_customer_does_not_grow_with_the_register(
    mcp_server: Any, seeded_customer: _Identified, inflate: Callable[[int], None]
) -> None:
    """The same bound on the prompt whose list is the customer's own receivables.

    The embedded card is measured too, not only the text block: it is the larger half of this
    message, and a bound that ignored it would be a bound on the cheaper half.
    """
    args = {"organization_id": str(seeded_customer.id)}
    inflate(0)
    first = await mcp_server.get_prompt("organization-status", args)
    before = len(_text_of(first)) + len(_resource_text(first))
    inflate(1)
    second = await mcp_server.get_prompt("organization-status", args)
    after = len(_text_of(second)) + len(_resource_text(second))
    assert after - before <= _GROWTH_BUDGET, (
        f"the briefing grew by {after - before} characters when {_EXTRA_ROWS} unpaid "
        "invoices were added"
    )


async def test_status_customer_shows_the_postal_code_and_admits_the_rest(
    mcp_server: Any, seeded_customer: _Identified, inflate: Callable[[int], None]
) -> None:
    inflate(0)
    text_block = _text_of(
        await mcp_server.get_prompt(
            "organization-status", {"organization_id": str(seeded_customer.id)}
        )
    )
    listed = [line for line in text_block.splitlines() if line.startswith("- ")]
    assert len(listed) == organization_prompts.UNPAID_INVOICES_SHOWN
    assert [line for line in text_block.splitlines() if line.startswith("_")]


# -- the two prohibitions --------------------------------------------------------


async def test_no_prompt_contains_a_fiscal_field_by_name(
    mcp_server: Any, seeded_customer: _Identified
) -> None:
    """Criterion 10's first prohibition, checked over every rendered message of every prompt.

    Slice 4 §11 reason 4 keeps the fiscal estimate off the MCP surface because a PAT has no
    scopes (residuo R10) and is therefore indistinguishable from full access. A prompt that
    carried it would bypass that decision without calling the tool that deliberately does not
    exist -- and nobody would think to look for it in a prompt.

    By field name, not by intention, and over the raw messages rather than the text blocks:
    a fiscal figure smuggled into the embedded resource would be just as reachable.
    """
    cases = [*_ALL_PROMPTS, ("organization-status", {"organization_id": str(seeded_customer.id)})]
    for name, args in cases:
        rendered = await mcp_server.get_prompt(name, args)
        haystack = str(rendered.messages).lower()
        assert haystack, name
        for field in FORBIDDEN_FISCAL_FIELDS:
            assert field not in haystack, f"{name} carries the fiscal field {field}"


def test_no_prompt_reaches_a_service_method_on_an_exclusion_list() -> None:
    """Criterion 10's second prohibition: a prompt is another packaging of the same
    permissions, not a shortcut through them.

    `test_mcp_surface_coverage.py` sweeps `tools/` and `resources/` for reachability and does
    not sweep `prompts/`, so a prompt calling an excluded method would be a hole in exactly
    the artefact built to have none. This is the narrow version of that sweep: the names that
    are excluded *because exposing them would be wrong*, matched on the call.
    """
    prompts_dir = Path(__file__).resolve().parents[1] / "src" / "pigrocrm_mcp" / "prompts"
    source = "\n".join(path.read_text(encoding="utf-8") for path in prompts_dir.rglob("*.py"))
    assert source, "no prompt source was read, so this test proved nothing"
    for excluded in (
        "get_fiscal_estimate",
        "update_automation_config",
        "bind_time_to_invoice",
        "close_period",
        "reopen_period",
        "recalculate_rates",
        "update_user_rates",
        "update_deal_rate",
        "issue",
        "annul",
        "export_xml",
    ):
        assert f".{excluded}(" not in source, (
            f"a prompt reaches {excluded}, which is on an MCP exclusion list -- a prompt is "
            "another packaging of the same permissions, not a shortcut through them"
        )


# -- the figures are the owning service's, to the cent ---------------------------


async def test_closing_month_matches_the_pnl_value_by_value(
    mcp_server: Any, prompt_corpus: Corpus
) -> None:
    """Criterion 10's positive half: the rendered figures are `period_pnl`'s, compared value
    by value and not by eye.

    A prompt that reformats a margin is a second source of truth with a friendly tone. The
    comparison undoes exactly one substitution, the decimal comma, and nothing else, so a
    figure re-derived rather than printed fails here -- `float(value)`, a different scale, a
    different rounding.

    Worth saying plainly, because the obvious version of this claim is false: a `:.2f` would
    **not** be caught on this corpus, and cannot be on any corpus, because every money field
    in this product is already `Numeric(_, 2)` and `round_money` has already run. That is the
    reason the assertion compares `str(value)` rather than a formatted string -- the defect
    it can actually see is a figure that went through `float`, and that one is real.


    Every figure is asserted to be non-zero first. The corpus exists to make that true, and
    without the check a comparison between a P&L of zeros and a template printing zeros would
    pass on any implementation at all.
    """
    from pigrocrm.core.analytics.schemas import PeriodPnlQuery
    from pigrocrm.core.analytics.service import AnalyticsService
    from pigrocrm.core.db import month_bounds

    da, a = month_bounds(_YEAR, _MONTH)
    with prompt_corpus.factory() as session:
        pnl = AnalyticsService(session).period_pnl(
            PeriodPnlQuery(date_from=da, date_to=a, customer_id=None), ADMIN
        )

    text_block = _text_of(
        await mcp_server.get_prompt("month-close", {"year": _YEAR, "month": _MONTH})
    )

    for label, value in (
        ("chiusi.ricavi", pnl.closed.revenue),
        ("chiusi.costi_diretti", pnl.closed.direct_costs),
        ("chiusi.costo_lavoro", pnl.closed.work_cost),
        ("chiusi.margine_lordo", pnl.closed.gross_margin),
        ("in_corso.ricavi", pnl.in_progress.revenue),
        ("general_expenses", pnl.general_expenses),
        ("accrued_value", pnl.accrued_value),
    ):
        assert value != 0, f"{label} is zero, so comparing it proves nothing"
        assert str(value).replace(".", ",") in text_block, label

    assert str(pnl.written_entries_overdue) in text_block
    assert ("sì" if pnl.closed_period else "no") in text_block


# -- no new resource -------------------------------------------------------------


async def test_status_customer_is_the_only_prompt_with_to_resource_block(
    mcp_server: Any, seeded_customer: _Identified
) -> None:
    """§10: it is the only one because it is the only one whose resource already exists."""
    for name, args in _ALL_PROMPTS:
        rendered = await mcp_server.get_prompt(name, args)
        assert _resource_uris(rendered) == [], name

    rendered = await mcp_server.get_prompt(
        "organization-status", {"organization_id": str(seeded_customer.id)}
    )
    assert _resource_uris(rendered) == [f"organization://{seeded_customer.id}"]


async def test_no_prompt_introduces_a_new_resource_uri_scheme(
    mcp_server: Any, seeded_customer: _Identified
) -> None:
    """Criterion 10: the URIs the four embed are listed and checked for new ones. A resource
    is a surface, and adding one silently is adding a surface silently."""
    cases = [*_ALL_PROMPTS, ("organization-status", {"organization_id": str(seeded_customer.id)})]
    seen: set[str] = set()
    for name, args in cases:
        rendered = await mcp_server.get_prompt(name, args)
        for uri in _resource_uris(rendered):
            seen.add(uri.split("//")[0] + "//")
    assert seen, "no prompt embedded any resource, so this test proved nothing"
    assert seen <= KNOWN_RESOURCE_URIS, f"new resource schemes: {seen - KNOWN_RESOURCE_URIS}"


async def test_the_registered_resource_templates_are_still_exactly_three(
    mcp_server: Any,
) -> None:
    """The other half of "no new resource": not only that no prompt embeds a new URI, but
    that none was registered at all. §11.1 -- the existing three stay the way an agent is
    made to **read** before it **acts**."""
    templates = {
        str(template.uri_template) for template in await mcp_server.list_resource_templates()
    }
    assert templates == {
        "organization://{organization_id}",
        "person://{person_id}",
        "deal://{deal_id}",
    }
