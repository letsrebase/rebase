from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session
from testcontainers.community.postgres import PostgresContainer

from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings
from pigrocrm.core.contract_expenses.triggers import CONTRACT_EXPENSE_TRIGGER_SQL
from pigrocrm.core.db import Base, create_engine_from_settings, session_factory
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm.core.work_units.triggers import WORK_UNIT_TRIGGER_SQL
from pigrocrm_mcp.server import build_server

ADMIN = Actor(id=None, type="mcp", role="admin")


@pytest.fixture(scope="session")
def mcp_engine() -> Iterator[Engine]:
    with PostgresContainer("postgres:17-alpine", driver="psycopg") as container:
        engine = create_engine_from_settings(Settings(database_url=container.get_connection_url()))
        # Before `create_all`, and the order is load-bearing: four models declare GIN
        # indexes with `gin_trgm_ops`, and `create_all` fails outright with
        # `operator class "gin_trgm_ops" does not exist` without the extension.
        # Mirrors packages/core/tests/conftest.py, which explains it at length.
        with engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
        import pigrocrm.core.models_registry  # noqa: F401

        Base.metadata.create_all(engine)
        # The trigger DDL `create_all` cannot express (packages/core/tests/conftest.py
        # explains this at length) -- missing here left `contract_expenses.rimborsabile`
        # silently stuck at its column default through this test database the moment a
        # real MCP tool (REB-360) started exercising it; `work_units` carries no MCP
        # tool yet, but installing its own trigger here too avoids leaving the
        # identical gap for whoever adds one next.
        with engine.begin() as connection:
            connection.execute(text(WORK_UNIT_TRIGGER_SQL))
            connection.execute(text(CONTRACT_EXPENSE_TRIGGER_SQL))
        yield engine
        engine.dispose()


@pytest.fixture
def mcp_session(mcp_engine: Engine) -> Iterator[Session]:
    connection = mcp_engine.connect()
    transaction = connection.begin()
    # join_transaction_mode="create_savepoint" is load-bearing, not optional -- see
    # packages/core/tests/conftest.py's identical `db_session` fixture, established
    # in Task 2 specifically because its absence lets `session.rollback()` propagate
    # to the real, externally-managed transaction instead of nesting inside it. This
    # was dormant here because no MCP-side code ever called `session.rollback()`
    # across more than one tool call sharing this session -- until the final review's
    # item 6 fix made `_guard` roll back on every exception, which surfaced it
    # immediately: a blocked `archive_customer` call's `Conflict` rolling back with
    # this parameter absent silently discarded the customer and deal an earlier,
    # already-committed tool call in the *same test* had created. Confirmed directly
    # by reproducing it with this parameter removed and watching two already-
    # committed rows disappear after an unrelated later rollback.
    session = session_factory(mcp_engine)(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def server(mcp_session: Session, tmp_path: Path):
    # `create_document_from_template` (task 13) actually writes bytes through
    # `context.storage`. Without this override, `build_server`'s own default --
    # `storage_from_settings(get_settings())` -- falls back to a `LocalFileStorage`
    # rooted at the repository's own `./var/documents`, exactly the same real-files-
    # left-in-the-working-tree problem the API's `client` fixture was fixed for in
    # task 12. A fresh `tmp_path` per test keeps storage exactly as isolated as the
    # database already is (`mcp_session`'s own rolled-back transaction).
    #
    # `settings` is passed explicitly, and that is the second isolation this fixture
    # provides. `build_server`'s default is `get_settings()`, which reads a `.env` from
    # the working directory -- so the whole Gmail surface appeared or disappeared
    # depending on whether the developer happened to have configured Gmail for their own
    # local instance. The tests that assert the surface is *absent* said so out loud
    # ("this repository has no .env") and were falsified the day someone wrote one.
    #
    # `_env_file=None` is the same guard `test_gmail_tools.py::gmail_settings` already
    # applies to the configured half; this is the unconfigured half it was missing.
    # An installation is now something these tests declare, not something they inherit.
    return build_server(
        lambda: mcp_session,
        lambda: ADMIN,
        LocalFileStorage(tmp_path),
        Settings(_env_file=None),  # type: ignore[call-arg]
    )


@pytest.fixture
def mcp_storage(tmp_path: Path) -> LocalFileStorage:
    return LocalFileStorage(str(tmp_path / "documents"))


@pytest.fixture
def seeded_customer_id(mcp_session: Session) -> str:
    from pigrocrm.core.customers.models import Customer

    customer = Customer(ragione_sociale="ACME S.r.l.")
    mcp_session.add(customer)
    mcp_session.flush()
    return str(customer.id)


@pytest.fixture
def seeded_template_id(mcp_session: Session) -> str:
    """Built with the same in-process services `server` itself calls -- an
    emitter profile (`create_document_from_template` renders through it) and one
    template with a single declared variable, `oggetto`, so
    `test_describe_template_reports_the_variables_before_anyone_is_asked` has
    exactly one name to check for."""
    from pigrocrm.core.emitter.schemas import EmitterProfileUpsert
    from pigrocrm.core.emitter.service import EmitterProfileService
    from pigrocrm.core.templates.schemas import TemplateCreate, TemplateVariable
    from pigrocrm.core.templates.service import TemplateService

    EmitterProfileService(mcp_session).upsert(
        EmitterProfileUpsert(ragione_sociale="Studio Rossi", partita_iva="01234567890"),
        ADMIN,
    )
    template = TemplateService(mcp_session).create(
        TemplateCreate(
            nome="Consulenza CTO",
            tipo="offerta",
            corpo_markdown="Spett.le {{cliente.ragione_sociale}} — {{oggetto}}",
            variabili_dichiarate=[
                TemplateVariable(
                    nome="oggetto", etichetta="Oggetto", tipo="text", obbligatoria=True
                )
            ],
        ),
        ADMIN,
    )
    return str(template.id)


@pytest.fixture
def seeded_user_id(mcp_session: Session):
    """A UUID object, not a `str` like `seeded_customer_id` above: the time-tracking
    tests that consume this feed it straight into `TimeEntryCreate`/raw SQL bind
    parameters, and `str(seeded_user_id)` is what the tool-call tests do at their own
    call sites when they need the wire form -- matching `packages/core/tests/
    conftest.py`'s identical fixture."""
    from pigrocrm.core.auth.models import User

    user = User(email="worker-mcp@example.test", password_hash="x", nome="Worker")
    mcp_session.add(user)
    mcp_session.flush()
    return user.id


@pytest.fixture
def seeded_deal_id(mcp_session: Session, seeded_customer_id: str):
    """A UUID object -- see `seeded_user_id`'s docstring for why this differs from
    `seeded_customer_id`'s `str`."""
    from uuid import UUID

    from pigrocrm.core.deals.models import Deal
    from pigrocrm.core.pipeline.models import PipelineStage

    stage = PipelineStage(nome="Aperto", posizione=0, probabilita_default=10, tipo="open")
    mcp_session.add(stage)
    mcp_session.flush()
    deal = Deal(
        nome="Progetto MCP",
        customer_id=UUID(seeded_customer_id),
        pipeline_stage_id=stage.id,
        probabilita=10,
    )
    mcp_session.add(deal)
    mcp_session.flush()
    return deal.id


@pytest.fixture
def seeded_offer_id(mcp_session: Session, seeded_customer_id: str, tmp_path: Path) -> str:
    """A plain (not template-generated) offer in its initial `bozza` state -- enough
    to exercise `set_offer_state`'s guard and `get_document_versions` on a document
    with zero versions, without needing Pandoc/Typst installed."""
    from uuid import UUID

    from pigrocrm.core.documents.schemas import DocumentCreate
    from pigrocrm.core.documents.service import DocumentService

    document = DocumentService(mcp_session, LocalFileStorage(tmp_path)).create(
        DocumentCreate(customer_id=UUID(seeded_customer_id), tipo="offerta", titolo="Offerta test"),
        ADMIN,
    )
    return str(document.id)
