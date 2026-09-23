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

from collections.abc import Callable, Iterator
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, NamedTuple
from uuid import UUID

import pytest
from sqlalchemy import Engine, create_engine, delete, text
from sqlalchemy.orm import sessionmaker

from pigrocrm.core.activities.models import Activity
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db import Base, session_factory, today_local
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.documents.models import Document
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.pipeline.models import PipelineStage
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm.core.timetracking.models import Cost, CostCategory, TimeEntry
from pigrocrm_mcp.context import ScopedSessionProvider
from pigrocrm_mcp.prompts import customer as customer_prompts
from pigrocrm_mcp.prompts import dashboards as dashboard_prompts
from pigrocrm_mcp.server import build_server

ADMIN = Actor(id=None, type="mcp", role="admin")
_PREFIX = "MCPPROMPT"
# The month `chiusura-mese` is asked about everywhere in this file. In the past, and fixed:
# a P&L over a moving month would make every figure in the value-by-value comparison depend
# on the day the suite runs.
_ANNO, _MESE = 2026, 3

PROMPT_NAMES = {"revisione-pipeline", "chiusura-mese", "stato-cliente", "ore-da-registrare"}


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


def _seed(factory: sessionmaker[Any]) -> _Seeded:
    """One corpus, committed, carrying something for every section of every prompt.

    Committed and not flushed: a scoped session never sees another transaction's
    uncommitted rows, which is the point of it. Prefixed and additive rather than wholesale,
    for the reason `test_mcp_dashboard.py` gives -- `mcp_engine` is session-scoped and
    earlier files commit deals that outlive them, so emptying `pipeline_stages` here would
    fail on somebody else's foreign keys and would be wrong even if it did not.
    """
    with factory() as session:
        # `code=None`, so these are user-created stages as far as `seed_defaults` is
        # concerned and never collide with a seeded `lead`/`vinto` on the unique index.
        aperto = PipelineStage(
            nome=f"{_PREFIX} aperto", posizione=910, probabilita_default=50, tipo="open", code=None
        )
        vinto = PipelineStage(
            nome=f"{_PREFIX} vinto", posizione=911, probabilita_default=100, tipo="won", code=None
        )
        categoria = CostCategory(nome=f"{_PREFIX} generali", posizione=910, code=None)
        user = User(email=f"{_PREFIX.lower()}@example.test", password_hash="x", nome="Worker")
        customer = Customer(ragione_sociale=f"{_PREFIX} Cliente", nazione="IT", custom_fields={})
        session.add_all([aperto, vinto, categoria, user, customer])
        session.flush()

        open_deal = Deal(
            nome=f"{_PREFIX} in corso",
            customer_id=customer.id,
            pipeline_stage_id=aperto.id,
            valore_previsto=Decimal("1000.00"),
            probabilita=50,
            custom_fields={},
        )
        won_deal = Deal(
            nome=f"{_PREFIX} vinto",
            customer_id=customer.id,
            pipeline_stage_id=vinto.id,
            valore_previsto=Decimal("5000.00"),
            probabilita=100,
            chiuso_il=date(_ANNO, _MESE, 20),
            custom_fields={},
        )
        session.add_all([open_deal, won_deal])
        session.flush()

        # Two offers in `inviata`: one with a known age, one whose `stato_dal` was never
        # recorded. The second is the row that makes `giorni is None` reachable, and
        # "ferma da 0 giorni" -- which would read as "sent today" -- is the defect it pins.
        session.add_all(
            [
                # `ck_documents_customer_xor_deal`: one owner, never both. The dated offer
                # hangs off the deal and the undated one off the customer, so the two rows
                # also cover both spellings of ownership.
                Document(
                    deal_id=open_deal.id,
                    tipo="offerta",
                    titolo=f"{_PREFIX} offerta datata",
                    stato="inviata",
                    stato_dal=today_local() - timedelta(days=21),
                    versione_corrente=1,
                    custom_fields={},
                ),
                Document(
                    customer_id=customer.id,
                    tipo="offerta",
                    titolo=f"{_PREFIX} offerta senza data",
                    stato="inviata",
                    stato_dal=None,
                    versione_corrente=1,
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
                    tipo="fattura",
                    stato="emessa",
                    stato_pagamento="da_incassare",
                    anno=_ANNO,
                    numero=9001,
                    imponibile=Decimal("4000.00"),
                    imposta=Decimal("0.00"),
                    bollo=Decimal("0.00"),
                    totale=Decimal("4000.00"),
                    data_emissione=date(_ANNO, _MESE, 10),
                    data_scadenza=date(_ANNO, _MESE, 31),
                    causale=f"{_PREFIX} saldo",
                    tipo_documento="TD01",
                    divisa="EUR",
                    custom_fields={},
                ),
                Invoice(
                    customer_id=customer.id,
                    deal_id=open_deal.id,
                    tipo="fattura",
                    stato="emessa",
                    stato_pagamento="da_incassare",
                    anno=_ANNO,
                    numero=9002,
                    imponibile=Decimal("750.00"),
                    imposta=Decimal("0.00"),
                    bollo=Decimal("0.00"),
                    totale=Decimal("750.00"),
                    data_emissione=date(_ANNO, _MESE, 18),
                    data_scadenza=None,
                    causale=f"{_PREFIX} acconto",
                    tipo_documento="TD01",
                    divisa="EUR",
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
                    category_id=categoria.id,
                    data=date(_ANNO, _MESE, 8),
                    importo=Decimal("310.00"),
                    descrizione=f"{_PREFIX} costo diretto sul vinto",
                    custom_fields={},
                ),
                # `deal_id IS NULL`: a general expense lands in `spese_generali` and on no
                # deal's margin.
                Cost(
                    deal_id=None,
                    category_id=categoria.id,
                    data=date(_ANNO, _MESE, 5),
                    importo=Decimal("120.00"),
                    descrizione=f"{_PREFIX} spesa generale",
                    custom_fields={},
                ),
            ]
        )
        # Billable, unbilled, with a rate: the only way `valore_maturato` and
        # `ore_fatturabili_non_fatturate` are non-zero on both the period P&L and the
        # register-wide backlog. `costo_applicato` is what makes `costo_lavoro` non-zero.
        session.add_all(
            [
                TimeEntry(
                    deal_id=open_deal.id,
                    user_id=user.id,
                    data=date(_ANNO, _MESE, 12),
                    ore=Decimal("6.00"),
                    descrizione=f"{_PREFIX} analisi",
                    fatturabile=True,
                    tariffa_applicata=Decimal("80.000000"),
                    tariffa_origine="manuale",
                    costo_applicato=Decimal("30.000000"),
                    costo_origine="manuale",
                    custom_fields={},
                ),
                # `fatturabile=False`, and it is what puts this deal in the `chiusi` column
                # at all. A deal's P&L state is not its stage: `deal_summary` calls a deal on
                # a `won` stage "da fatturare" -- which lands in `in_corso` -- for as long as
                # it has one billable hour nobody has invoiced. A non-billable hour still
                # costs exactly what it would have cost billed, so `chiusi.costo_lavoro` is
                # non-zero all the same, which is the figure this row exists to produce.
                TimeEntry(
                    deal_id=won_deal.id,
                    user_id=user.id,
                    data=date(_ANNO, _MESE, 14),
                    ore=Decimal("9.00"),
                    descrizione=f"{_PREFIX} consegna",
                    fatturabile=False,
                    tariffa_applicata=None,
                    tariffa_origine="assente",
                    costo_applicato=Decimal("30.000000"),
                    costo_origine="manuale",
                    custom_fields={},
                ),
            ]
        )
        # The global recent feed is what `ore-da-registrare` reads to propose a deal. Two
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
            customer_id=customer.id, open_deal_name=open_deal.nome, won_deal_name=won_deal.nome
        )
        session.commit()

    return seeded


def _teardown(factory: sessionmaker[Any]) -> None:
    with factory() as session:
        # Children before parents. Everything is scoped to this file's prefix rather than
        # truncated, so a wholesale delete cannot take another file's committed rows with
        # it -- the activities by their own `kind`, which no other file writes.
        session.execute(delete(Activity).where(Activity.kind.like(f"{_PREFIX}_%")))
        session.execute(delete(TimeEntry).where(TimeEntry.descrizione.like(f"{_PREFIX} %")))
        session.execute(delete(Cost).where(Cost.descrizione.like(f"{_PREFIX} %")))
        session.execute(delete(Invoice).where(Invoice.causale.like(f"{_PREFIX} %")))
        session.execute(delete(Document).where(Document.titolo.like(f"{_PREFIX} %")))
        session.execute(delete(Deal).where(Deal.nome.like(f"{_PREFIX} %")))
        session.execute(delete(Customer).where(Customer.ragione_sociale.like(f"{_PREFIX} %")))
        session.execute(delete(CostCategory).where(CostCategory.nome.like(f"{_PREFIX} %")))
        session.execute(delete(User).where(User.email.like(f"{_PREFIX.lower()}%")))
        session.execute(delete(PipelineStage).where(PipelineStage.nome.like(f"{_PREFIX} %")))
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
        _teardown(factory)


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

    assert {arg.name: arg.required for arg in by_name["revisione-pipeline"].arguments} == {
        "da": False,
        "a": False,
    }
    assert {arg.name: arg.required for arg in by_name["chiusura-mese"].arguments} == {
        "anno": True,
        "mese": True,
    }
    assert {arg.name: arg.required for arg in by_name["stato-cliente"].arguments} == {
        "customer_id": True
    }
    assert {arg.name: arg.required for arg in by_name["ore-da-registrare"].arguments} == {
        "settimana": False
    }


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("revisione-pipeline", {}),
        ("chiusura-mese", {"anno": _ANNO, "mese": _MESE}),
        ("ore-da-registrare", {}),
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


# -- revisione-pipeline ----------------------------------------------------------


async def test_revisione_pipeline_carries_the_dashboard_as_text(mcp_server: Any) -> None:
    """The context arrives *inside* the prompt rather than depending on the model going to
    fetch it -- which is the whole reason these are prompts. Asserted on the figures, not
    only on the headings: a template with every value missing has all the same words in it.
    """
    text_block = _text_of(await mcp_server.get_prompt("revisione-pipeline", {}))
    assert "Pipeline per stato" in text_block
    assert "Tasso di conversione" in text_block
    assert f"{_PREFIX} aperto" in text_block, "the stage row is missing, so nothing was read"
    # The closed stages are in the table too since 2026-09-09, with the caveat that says
    # what they count. The corpus puts one deal in `{_PREFIX} vinto`.
    assert f"| {_PREFIX} vinto | 1 |" in text_block
    assert "contano i deal che ci sono **oggi**" in text_block
    # The posture, which is the half a tool could not carry without becoming an injection.
    assert "fermi" in text_block.lower()


async def test_revisione_pipeline_lists_the_pending_offers_with_their_age(
    mcp_server: Any,
) -> None:
    text_block = _text_of(await mcp_server.get_prompt("revisione-pipeline", {}))
    assert f"{_PREFIX} offerta datata — ferma da 21 giorni" in text_block


async def test_an_offer_with_no_recorded_date_is_of_unknown_age_and_not_zero_days(
    mcp_server: Any,
) -> None:
    """`giorni is None` is the branch every briefing function gets wrong, and it gets it
    wrong in the one direction that misleads: "ferma da 0 giorni" reads as "sent today",
    which is the opposite of what an offer with no recorded date might be."""
    text_block = _text_of(await mcp_server.get_prompt("revisione-pipeline", {}))
    assert f"{_PREFIX} offerta senza data — ferma da data ignota" in text_block
    assert "ferma da 0 giorni" not in text_block


async def test_a_period_where_nothing_closed_reports_a_dash_and_not_zero_per_cent(
    mcp_server: Any,
) -> None:
    """`tasso_conversione` is `None`, never `0`, when nothing closed. Zero per cent means "I
    lost everything"; nothing closed means something else, and a briefing that renders the
    second as the first is telling the reader a bad month was a catastrophe."""
    rendered = await mcp_server.get_prompt(
        "revisione-pipeline", {"da": "2019-01-01", "a": "2019-01-31"}
    )
    text_block = _text_of(rendered)
    assert "Tasso di conversione: —" in text_block


async def test_revisione_pipeline_on_an_empty_database_is_a_valid_message(
    empty_server: Any,
) -> None:
    """§16 criterion 10's last sentence, on a database with no rows at all -- not on a
    period that happens to be empty. It is the failure mode of every briefing function:
    an empty pipeline, no offers, and a `None` conversion rate, all at once.
    """
    rendered = await empty_server.get_prompt("revisione-pipeline", {})
    assert rendered.messages
    assert all(message.role in ("user", "assistant") for message in rendered.messages)
    text_block = _text_of(rendered)
    assert "Nessuna offerta in attesa." in text_block
    assert "Tasso di conversione: —" in text_block


# -- chiusura-mese ---------------------------------------------------------------


async def test_chiusura_mese_takes_a_year_and_a_month(mcp_server: Any) -> None:
    text_block = _text_of(await mcp_server.get_prompt("chiusura-mese", {"anno": _ANNO, "mese": 3}))
    assert "# Chiusura mese — 2026-03" in text_block
    assert "Conto economico del periodo" in text_block


async def test_chiusura_mese_refuses_month_thirteen_by_name(mcp_server: Any) -> None:
    """`month_bounds` owns the rule and names the field; the prompt adds no second copy of
    it. The guard carries the diagnosis through, so the message the caller sees still says
    which argument to change."""
    with pytest.raises(Exception) as caught:  # noqa: PT011 -- the SDK wraps it in ValueError
        await mcp_server.get_prompt("chiusura-mese", {"anno": _ANNO, "mese": 13})
    assert "mese" in str(caught.value)


async def test_chiusura_mese_on_an_empty_database_renders_a_null_margin_as_a_dash(
    empty_server: Any,
) -> None:
    """The second `None` branch, on the other prompt that has one: `margine_percentuale` is
    `None` when revenue is zero, and `f"{None:.2f}"` is a `TypeError` in production."""
    text_block = _text_of(
        await empty_server.get_prompt("chiusura-mese", {"anno": _ANNO, "mese": _MESE})
    )
    assert "| Margine % | — | — |" in text_block


# -- ore-da-registrare -----------------------------------------------------------


async def test_ore_da_registrare_names_the_days_and_the_tool_that_records_them(
    mcp_server: Any,
) -> None:
    """The prompt §6 calls the most useful in the product: it attacks the "I never entered
    Tuesday" failure slice 4 §13 names when it refuses a stopwatch, and it ends by asking
    the user what they did so it can call `log_time` -- a tool the agent has."""
    text_block = _text_of(await mcp_server.get_prompt("ore-da-registrare", {}))
    assert "log_time" in text_block
    assert "Ore da registrare — settimana" in text_block
    assert "| Giorno | Ore |" in text_block


async def test_ore_da_registrare_names_the_recent_deal_and_names_it_once(
    mcp_server: Any, prompt_corpus: Corpus
) -> None:
    """Names, not identifiers, and each deal once however many times the feed mentions it.

    A list of UUIDs would be a section nobody can answer from -- cost with no context in it,
    which is exactly what §10 is about -- and the corpus writes two activities on the same
    deal so a missing deduplication shows up here rather than as a briefing that lists one
    deal eight times.
    """
    text_block = _text_of(await mcp_server.get_prompt("ore-da-registrare", {}))
    assert text_block.count(f"- {prompt_corpus.open_deal_name}") == 1


async def test_a_past_week_is_refused_rather_than_answered_with_the_current_one(
    mcp_server: Any,
) -> None:
    """An argument the prompt advertises and answers with a different week's figures is
    worse than one it does not have: the caller would believe it worked."""
    text_block = _text_of(
        await mcp_server.get_prompt("ore-da-registrare", {"settimana": "2020-01-06"})
    )
    assert "non è disponibile" in text_block
    assert "| Giorno | Ore |" not in text_block


async def test_ore_da_registrare_on_an_empty_database_is_a_valid_message(
    empty_server: Any,
) -> None:
    rendered = await empty_server.get_prompt("ore-da-registrare", {})
    text_block = _text_of(rendered)
    assert "Nessuna attività recente su un deal." in text_block
    # Seven days, all of them empty: an empty week is seven zeros, never an empty series.
    assert text_block.count("| 2") == 7


# -- stato-cliente ---------------------------------------------------------------


async def test_stato_cliente_embeds_the_existing_resource(
    mcp_server: Any, seeded_customer: _Identified
) -> None:
    """§10: the **only** prompt with a resource block, because it is the only one whose
    resource already exists. `customer://{id}` is slice 1 §8.4's."""
    rendered = await mcp_server.get_prompt(
        "stato-cliente", {"customer_id": str(seeded_customer.id)}
    )
    assert _resource_uris(rendered) == [f"customer://{seeded_customer.id}"]


async def test_the_embedded_resource_carries_the_card_and_not_only_the_uri(
    mcp_server: Any, seeded_customer: _Identified, prompt_corpus: Corpus
) -> None:
    """`TextResourceContents.text` is required by the protocol types, and that is what makes
    the resource block *be* the context rather than a pointer to it: a client that cannot
    follow the URI still has the card, so "the context arrives inside the prompt" holds
    without the model going to fetch anything."""
    rendered = await mcp_server.get_prompt(
        "stato-cliente", {"customer_id": str(seeded_customer.id)}
    )
    card = _resource_text(rendered)
    assert f"# {_PREFIX} Cliente" in card
    assert prompt_corpus.open_deal_name in card


async def test_stato_cliente_does_not_repeat_the_deals_the_card_already_carries(
    mcp_server: Any, seeded_customer: _Identified, prompt_corpus: Corpus
) -> None:
    """The tax half of §10, and the reason this prompt diverges from the obvious shape.

    `render_customer` already lists the deals, and the resource block must carry its text,
    so a "## Deal" section in the text block beside it would be the most expensive section
    of the message paid for twice -- for rows the reader already has. What the card has no
    notion of is money owed, so that is what the text block is.
    """
    rendered = await mcp_server.get_prompt(
        "stato-cliente", {"customer_id": str(seeded_customer.id)}
    )
    assert prompt_corpus.open_deal_name not in _text_of(rendered)
    assert prompt_corpus.open_deal_name in _resource_text(rendered)


async def test_stato_cliente_lists_the_unpaid_invoices_with_their_deadline(
    mcp_server: Any, seeded_customer: _Identified
) -> None:
    """The one thing the card cannot say. Both rows, because the undated one is the branch
    that would otherwise print `None`."""
    text_block = _text_of(
        await mcp_server.get_prompt("stato-cliente", {"customer_id": str(seeded_customer.id)})
    )
    assert f"- {_ANNO}/9001 — 4000,00 € (totale con IVA), scadenza {_ANNO}-03-31" in text_block
    assert f"- {_ANNO}/9002 — 750,00 € (totale con IVA), scadenza senza scadenza" in text_block


async def test_stato_cliente_on_an_unknown_customer_is_a_domain_error(mcp_server: Any) -> None:
    """An empty briefing about nobody would read as "this customer has nothing open", which
    is the more dangerous of the two possible answers."""
    from uuid import uuid4

    with pytest.raises(Exception) as caught:  # noqa: PT011 -- the SDK wraps it in ValueError
        await mcp_server.get_prompt("stato-cliente", {"customer_id": str(uuid4())})
    assert "customer" in str(caught.value).lower()


# == criterion 10 =================================================================
#
# Two prohibitions and two positive claims, and the second positive one is the reason this
# block exists at all. "The prompts carry the context, and not the tax" is a statement about
# *size*, and a test that only checked the words in a prompt would pass on a briefing that
# pasted the whole register into the model's context window on every render.

# Field names, not concepts. Criterion 10: "verificato per nome di campo, non per
# intenzione". The first four are the fiscal estimate's own fields (slice 4 §11); the last
# three are the parameters it is derived from, which are just as sensitive and would let a
# reader reconstruct it.
FORBIDDEN_FISCAL_FIELDS = frozenset(
    {
        "imponibile_fiscale",
        "imposta_sostitutiva",
        "contributi",
        "netto_stimato",
        "coefficiente_redditivita",
        "aliquota_imposta_sostitutiva",
        "aliquota_inps",
    }
)

# The resources that exist. §11.1 adds none, and inventing `dashboard://commerciale?da=...`
# in order to have a URI to embed would be adding one without saying so.
KNOWN_RESOURCE_URIS = frozenset({"customer://", "person://", "deal://"})

_ALL_PROMPTS = (
    ("revisione-pipeline", {}),
    ("chiusura-mese", {"anno": _ANNO, "mese": _MESE}),
    ("ore-da-registrare", {}),
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
                        tipo="offerta",
                        titolo=f"{_PREFIX} offerta massa {serial:03d}",
                        stato="inviata",
                        stato_dal=today_local() - timedelta(days=serial + 1),
                        versione_corrente=1,
                        custom_fields={},
                    )
                )
                session.add(
                    Invoice(
                        customer_id=prompt_corpus.customer_id,
                        tipo="fattura",
                        stato="emessa",
                        stato_pagamento="da_incassare",
                        anno=_ANNO,
                        numero=9100 + serial,
                        imponibile=Decimal("10.00"),
                        imposta=Decimal("0.00"),
                        bollo=Decimal("0.00"),
                        totale=Decimal("10.00"),
                        data_emissione=date(_ANNO, _MESE, 1),
                        data_scadenza=date(_ANNO, _MESE, 1) + timedelta(days=serial),
                        causale=f"{_PREFIX} massa {serial:03d}",
                        tipo_documento="TD01",
                        divisa="EUR",
                        custom_fields={},
                    )
                )
            session.commit()

    return _inflate


# -- the tax ---------------------------------------------------------------------


async def test_revisione_pipeline_does_not_grow_with_the_register(
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
    before = len(_text_of(await mcp_server.get_prompt("revisione-pipeline", {})))
    inflate(1)
    after = len(_text_of(await mcp_server.get_prompt("revisione-pipeline", {})))
    assert after - before <= _GROWTH_BUDGET, (
        f"the prompt grew by {after - before} characters when {_EXTRA_ROWS} offers were "
        "added to a register that was already past the cap"
    )


async def test_revisione_pipeline_shows_the_cap_and_admits_the_rest(
    mcp_server: Any, inflate: Callable[[int], None]
) -> None:
    """The other half of a cap: the rows that are not here are named.

    A briefing that omits its tail silently cannot be told apart from a short register, and
    the count comes from `offerte_in_attesa_totale` -- the number the owning service
    reported -- not from the length of a list this prompt has already truncated.
    """
    inflate(0)
    text_block = _text_of(await mcp_server.get_prompt("revisione-pipeline", {}))
    section = text_block.split("## Offerte inviate in attesa di risposta", 1)[1]
    listed = [line for line in section.splitlines() if line.startswith("- ")]
    assert len(listed) == dashboard_prompts.PENDING_OFFERS_SHOWN
    assert "righe non mostrate" in section


async def test_stato_cliente_does_not_grow_with_the_register(
    mcp_server: Any, seeded_customer: _Identified, inflate: Callable[[int], None]
) -> None:
    """The same bound on the prompt whose list is the customer's own receivables.

    The embedded card is measured too, not only the text block: it is the larger half of this
    message, and a bound that ignored it would be a bound on the cheaper half.
    """
    args = {"customer_id": str(seeded_customer.id)}
    inflate(0)
    first = await mcp_server.get_prompt("stato-cliente", args)
    before = len(_text_of(first)) + len(_resource_text(first))
    inflate(1)
    second = await mcp_server.get_prompt("stato-cliente", args)
    after = len(_text_of(second)) + len(_resource_text(second))
    assert after - before <= _GROWTH_BUDGET, (
        f"the briefing grew by {after - before} characters when {_EXTRA_ROWS} unpaid "
        "invoices were added"
    )


async def test_stato_cliente_shows_the_cap_and_admits_the_rest(
    mcp_server: Any, seeded_customer: _Identified, inflate: Callable[[int], None]
) -> None:
    inflate(0)
    text_block = _text_of(
        await mcp_server.get_prompt("stato-cliente", {"customer_id": str(seeded_customer.id)})
    )
    listed = [line for line in text_block.splitlines() if line.startswith("- ")]
    assert len(listed) == customer_prompts.UNPAID_INVOICES_SHOWN
    assert "ce ne sono altre" in text_block


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
    cases = [*_ALL_PROMPTS, ("stato-cliente", {"customer_id": str(seeded_customer.id)})]
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


async def test_chiusura_mese_matches_the_pnl_value_by_value(
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

    da, a = month_bounds(_ANNO, _MESE)
    with prompt_corpus.factory() as session:
        pnl = AnalyticsService(session).period_pnl(
            PeriodPnlQuery(da=da, a=a, customer_id=None), ADMIN
        )

    text_block = _text_of(
        await mcp_server.get_prompt("chiusura-mese", {"anno": _ANNO, "mese": _MESE})
    )

    for label, value in (
        ("chiusi.ricavi", pnl.chiusi.ricavi),
        ("chiusi.costi_diretti", pnl.chiusi.costi_diretti),
        ("chiusi.costo_lavoro", pnl.chiusi.costo_lavoro),
        ("chiusi.margine_lordo", pnl.chiusi.margine_lordo),
        ("in_corso.ricavi", pnl.in_corso.ricavi),
        ("spese_generali", pnl.spese_generali),
        ("valore_maturato", pnl.valore_maturato),
    ):
        assert value != 0, f"{label} is zero, so comparing it proves nothing"
        assert str(value).replace(".", ",") in text_block, label

    assert str(pnl.voci_scritte_in_ritardo) in text_block
    assert ("sì" if pnl.periodo_chiuso else "no") in text_block


# -- no new resource -------------------------------------------------------------


async def test_stato_cliente_is_the_only_prompt_with_a_resource_block(
    mcp_server: Any, seeded_customer: _Identified
) -> None:
    """§10: it is the only one because it is the only one whose resource already exists."""
    for name, args in _ALL_PROMPTS:
        rendered = await mcp_server.get_prompt(name, args)
        assert _resource_uris(rendered) == [], name

    rendered = await mcp_server.get_prompt(
        "stato-cliente", {"customer_id": str(seeded_customer.id)}
    )
    assert _resource_uris(rendered) == [f"customer://{seeded_customer.id}"]


async def test_no_prompt_introduces_a_new_resource_uri_scheme(
    mcp_server: Any, seeded_customer: _Identified
) -> None:
    """Criterion 10: the URIs the four embed are listed and checked for new ones. A resource
    is a surface, and adding one silently is adding a surface silently."""
    cases = [*_ALL_PROMPTS, ("stato-cliente", {"customer_id": str(seeded_customer.id)})]
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
        "customer://{customer_id}",
        "person://{person_id}",
        "deal://{deal_id}",
    }
