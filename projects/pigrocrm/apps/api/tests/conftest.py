from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session
from testcontainers.community.postgres import PostgresContainer

from pigrocrm.core.actor import Actor, Role
from pigrocrm.core.auth.schemas import UserCreate
from pigrocrm.core.auth.service import UserService
from pigrocrm.core.config import Settings, get_settings
from pigrocrm.core.contract_expenses.triggers import CONTRACT_EXPENSE_TRIGGER_SQL
from pigrocrm.core.db import Base, create_engine_from_settings, session_factory
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm.core.work_units.triggers import WORK_UNIT_TRIGGER_SQL
from pigrocrm_api.deps import get_session, get_storage
from pigrocrm_api.main import create_app
from pigrocrm_api.ratelimit import reset_rate_limit

ADMIN_EMAIL = "admin@pigro.it"
ADMIN_PASSWORD = "supersegreta1"
# Obviously a placeholder, but >= 32 characters: Settings.jwt_secret now rejects
# anything shorter (see pigrocrm.core.config), so a short literal here would fail at
# fixture setup rather than merely warn.
TEST_JWT_SECRET = "test-secret-for-the-api-test-suite-only"


@pytest.fixture(scope="session")
def api_engine() -> Iterator[Engine]:
    with PostgresContainer("postgres:17-alpine", driver="psycopg") as container:
        settings = Settings(database_url=container.get_connection_url(), jwt_secret=TEST_JWT_SECRET)
        get_settings.cache_clear()
        engine = create_engine_from_settings(settings)
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
        # real API route (REB-360) started exercising it; `work_units` carries no API
        # route yet, but installing its own trigger here too avoids leaving the
        # identical gap for whoever adds one next.
        with engine.begin() as connection:
            connection.execute(text(WORK_UNIT_TRIGGER_SQL))
            connection.execute(text(CONTRACT_EXPENSE_TRIGGER_SQL))
        yield engine
        engine.dispose()


@pytest.fixture
def api_session(api_engine: Engine) -> Iterator[Session]:
    connection = api_engine.connect()
    transaction = connection.begin()
    # join_transaction_mode="create_savepoint" is load-bearing, not optional -- see
    # packages/core/tests/conftest.py's identical `db_session` fixture, established
    # in Task 2 specifically because its absence lets `session.rollback()` propagate
    # to the real, externally-managed transaction instead of nesting inside it: any
    # test that exercises a DomainError-then-continue sequence across more than one
    # request sharing this session would otherwise lose every earlier commit the
    # moment something later in the same test rolls back -- confirmed directly by
    # reproducing it with this parameter removed and watching two already-committed
    # rows disappear after an unrelated later rollback.
    session = session_factory(api_engine)(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture(autouse=True)
def _fresh_rate_limit() -> None:
    # The limiter's bucket is a module-level dict, so it outlives any one test's own
    # fixtures: without this, a test that spends its budget on `/api/auth/link` or
    # `/api/tenants/{slug}/disponibile` (REB-228) hands the next one an already-spent
    # bucket -- including a test that builds its own `TestClient` by hand instead of
    # going through `client` or `test_tenants_api.py`'s own `spaces_client`
    # (`test_the_root_slug_is_the_root_itself_and_nobody_elses_name` reaches
    # `disponibile` that way). Autouse, not tucked inside one fixture, so no test in
    # this directory can be the one left uncovered.
    reset_rate_limit()


@pytest.fixture
def client(api_session: Session, tmp_path: Path) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: api_session
    # Documents/templates tests upload and download real bytes; without this override
    # `get_storage` falls through to `storage_from_settings(get_settings())`, which
    # defaults to a `LocalFileStorage` rooted at the repository's own `./var/documents`
    # -- writing real files into the working tree on every test run, left behind for
    # git to notice. A fresh `tmp_path` per test keeps storage exactly as isolated as
    # the database already is (`api_session`'s own rolled-back transaction).
    app.dependency_overrides[get_storage] = lambda: LocalFileStorage(tmp_path)
    # And the settings themselves, for the same class of reason one line up. The default
    # `get_settings()` reads a `.env` from the working directory, so whether Gmail was
    # configured -- and therefore whether `/api/gmail/*` answers `Conflict` or works --
    # depended on the developer's own local instance rather than on anything the tests
    # declare. The two specs that assert an *unconfigured* installation said as much in
    # their docstrings and were falsified the day someone wrote a `.env`.
    #
    # `_env_file=None` is what `test_gmail_tools.py::gmail_settings` already does for the
    # configured half; a spec that wants Gmail present overrides this dependency with its
    # own `Settings`, which is the honest way round: an installation is declared, never
    # inherited.
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)  # type: ignore[call-arg]
    # The login/refresh cookies are `Secure` on purpose (production sits behind TLS
    # termination) and httpx's cookie jar honours that against the request's URL
    # scheme -- over the default "http://testserver" it would store the cookie but
    # never send it back, so every later request would look unauthenticated. Using an
    # "https://" base_url exercises the real cookie policy instead of weakening it.
    with TestClient(app, base_url="https://testserver") as test_client:
        yield test_client


@pytest.fixture
def admin_user(api_session: Session):
    return UserService(api_session).create(
        UserCreate(email=ADMIN_EMAIL, password=ADMIN_PASSWORD, nome="Admin", ruolo="admin"),
        Actor.system(),
    )


@pytest.fixture
def logged_in(client: TestClient, admin_user) -> TestClient:
    response = client.post(
        "/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return client


def _client_as(client: TestClient, session: Session, *, email: str, ruolo: Role) -> TestClient:
    """The same shape as `admin_user`/`logged_in` above, generalised over `ruolo` so a
    router's own `actor.require_write`/`actor.require_admin` gate can be exercised
    over real HTTP -- not only at the service layer, where every existing test in
    this suite already covers it."""
    UserService(session).create(
        UserCreate(email=email, password=ADMIN_PASSWORD, nome="Test", ruolo=ruolo),
        Actor.system(),
    )
    response = client.post("/api/auth/login", json={"email": email, "password": ADMIN_PASSWORD})
    assert response.status_code == 200, response.text
    return client


@pytest.fixture
def readonly_client(client: TestClient, api_session: Session) -> TestClient:
    return _client_as(client, api_session, email="readonly@pigro.it", ruolo="readonly")


@pytest.fixture
def collaborator_client(client: TestClient, api_session: Session) -> TestClient:
    return _client_as(client, api_session, email="collaboratore@pigro.it", ruolo="collaboratore")
