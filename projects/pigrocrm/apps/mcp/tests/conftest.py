import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings, get_settings
from pigrocrm.core.db import create_engine_from_settings, session_factory
from pigrocrm.core.storage import LocalFileStorage

# The HTTP app refuses a placeholder signing key (PCORE-B-05) and `pigrocrm_mcp.http` builds one
# at import: the secret is set before anything imports it.
os.environ.setdefault("PIGROCRM_JWT_SECRET", "test-secret-for-the-mcp-test-suite-only")
get_settings.cache_clear()  # an import above may have read the settings already

from pigrocrm_mcp.server import build_server  # noqa: E402

ADMIN = Actor(id=None, type="mcp", role="admin")
# What fixtures seed with: defining a template is not something an agent credential may do
# (`core/agent_actions.py`), and a fixture is not an agent.
SETUP = Actor(id=None, type="system", role="admin")


def _ensure_committed_default(engine: Engine) -> None:
    """One committed default azienda in the worker's database (REB-623): since the
    chain, a customer row and everything under it need an azienda, and a world that
    commits for real sees only committed rows. The per-test seed below then finds it
    and writes nothing. Inline rather than imported from the core suite's `fakes`,
    which this root's conftest does not put on the path."""
    from pigrocrm.core.legal_entities.models import LegalEntity

    with engine.begin() as connection:
        if connection.execute(
            select(LegalEntity.id).where(LegalEntity.is_default.is_(True))
        ).first():
            return
        connection.execute(
            LegalEntity.__table__.insert().values(
                name="Spazio di prova", legal_name="Spazio di prova", is_default=True
            )
        )


@pytest.fixture(scope="session")
def mcp_engine(pigrocrm_postgres: Any) -> Iterator[Engine]:
    """A clone of the worker's template database (`projects/pigrocrm/conftest.py`,
    REB-579): extensions, tables and the trigger DDL are built there once per worker,
    which is also where the reasons for their order live."""
    engine = create_engine_from_settings(Settings(database_url=pigrocrm_postgres.clone("mcp")))
    _ensure_committed_default(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def mcp_session(mcp_engine: Engine) -> Iterator[Session]:
    connection = mcp_engine.connect()
    transaction = connection.begin()
    # The default azienda every provisioned space has (REB-615), seeded on the outer
    # connection so a service's rollback inside the session cannot remove it.
    from pigrocrm.core.legal_entities.models import LegalEntity

    # Since REB-623 the engine fixture commits one, so this is the fallback for a
    # database that has none. Not an early `return`: this is a generator fixture.
    if not connection.execute(
        select(LegalEntity.id).where(LegalEntity.is_default.is_(True))
    ).first():
        connection.execute(
            LegalEntity.__table__.insert().values(
                name="Spazio di prova", legal_name="Spazio di prova", is_default=True
            )
        )
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

    customer = Customer(legal_name="ACME S.r.l.")
    mcp_session.add(customer)
    mcp_session.flush()
    return str(customer.id)


@pytest.fixture
def seeded_template_id(mcp_session: Session) -> str:
    """Built with the same in-process services `server` itself calls -- an
    emitter profile (`create_document_from_template` renders through it) and one
    template with a single declared variable, `subject`, so
    `test_describe_template_reports_the_variables_before_anyone_is_asked` has
    exactly one name to check for."""
    from pigrocrm.core.legal_entities.schemas import LegalEntityUpsert
    from pigrocrm.core.legal_entities.service import LegalEntityService
    from pigrocrm.core.templates.schemas import TemplateCreate, TemplateVariable
    from pigrocrm.core.templates.service import TemplateService

    LegalEntityService(mcp_session).upsert_default(
        LegalEntityUpsert(legal_name="Studio Rossi", vat_number="01234567890"),
        ADMIN,
    )
    template = TemplateService(mcp_session).create(
        TemplateCreate(
            name="Consulenza CTO",
            type="offer",
            body_markdown="Spett.le {{cliente.ragione_sociale}} — {{subject}}",
            declared_variables=[
                TemplateVariable(name="subject", label="Oggetto", type="text", required=True)
            ],
        ),
        SETUP,
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

    user = User(email="worker-mcp@example.test", password_hash="x", name="Worker")
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

    stage = PipelineStage(name="Aperto", position=0, default_probability=10, type="open")
    mcp_session.add(stage)
    mcp_session.flush()
    deal = Deal(
        name="Progetto MCP",
        customer_id=UUID(seeded_customer_id),
        pipeline_stage_id=stage.id,
        probability=10,
    )
    mcp_session.add(deal)
    mcp_session.flush()
    return deal.id


@pytest.fixture
def seeded_offer_id(mcp_session: Session, seeded_customer_id: str, tmp_path: Path) -> str:
    """A plain (not template-generated) offer in its initial `draft` state -- enough
    to exercise `set_offer_state`'s guard and `get_document_versions` on a document
    with zero versions, without needing Pandoc/Typst installed."""
    from uuid import UUID

    from pigrocrm.core.documents.schemas import DocumentCreate
    from pigrocrm.core.documents.service import DocumentService

    document = DocumentService(mcp_session, LocalFileStorage(tmp_path)).create(
        DocumentCreate(customer_id=UUID(seeded_customer_id), type="offer", title="Offerta test"),
        ADMIN,
    )
    return str(document.id)
