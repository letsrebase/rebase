"""The registry row of a hub-era engagement over HTTP: the merge route of a freelancer's own
space points `rebase_engagements` at the survivor when the customer «rebase» is folded into
another row of the same company (REB-1031). The hub's old door routes that created these rows
are gone (REB-1108): the row is written here through the services, as a space the door set up
before.

The registry and every space this file creates live in the container `api_engine` starts, so
`_serving`'s teardown drops every space the registry lists.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.repository import UserRepository
from pigrocrm.core.auth.tokens import issue_access_token
from pigrocrm.core.config import Settings, get_settings
from pigrocrm.core.db import session_factory
from pigrocrm.core.db.sidecar import drop_database
from pigrocrm.core.deals.schemas import DealCreate
from pigrocrm.core.deals.service import DealService
from pigrocrm.core.engagements.models import RebaseEngagement
from pigrocrm.core.organizations.schemas import OrganizationCreate
from pigrocrm.core.organizations.service import OrganizationService
from pigrocrm.core.tenants import TenantService, TenantSignup, ensure_tenants_database
from pigrocrm.core.tenants.database import tenant_database_name, tenant_database_url
from pigrocrm_api.deps import ACCESS_COOKIE, reset_session_factories
from pigrocrm_api.main import create_app
from pigrocrm_api.ratelimit import reset_rate_limit

FREELANCER_EMAIL = "ada@studio.it"
SLUG = "ada-lovelace"


@pytest.fixture
def container_settings(api_engine: Engine) -> Settings:
    return Settings(
        database_url=api_engine.url.render_as_string(hide_password=False),
        jwt_secret="test-secret-for-the-api-test-suite-only",
        cookie_secure=True,
        public_url="https://pigro.test",
        hub_url="http://127.0.0.1:9",
        _env_file=None,  # type: ignore[call-arg]
    )


@contextmanager
def _serving(settings: Settings) -> Iterator[TestClient]:
    """Like `test_tenants_api.py`'s own `_serving`, except the teardown drops every space the
    registry lists, and empties `rebase_engagements` before `tenants`: its `tenant_id` is a
    foreign key into it."""
    reset_session_factories()
    get_settings.cache_clear()
    reset_rate_limit()
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    registry = ensure_tenants_database(settings)
    try:
        with TestClient(app, base_url="https://testserver") as client:
            yield client
    finally:
        with registry.begin() as connection:
            slugs = connection.execute(text("SELECT slug FROM tenants")).scalars().all()
            connection.execute(text("DELETE FROM rebase_engagements"))
            connection.execute(text("DELETE FROM tenants"))
        registry.dispose()
        reset_session_factories()
        for slug in slugs:
            drop_database(settings, tenant_database_url(settings, tenant_database_name(slug)))
        get_settings.cache_clear()


@pytest.fixture
def door_client(container_settings: Settings) -> Iterator[TestClient]:
    with _serving(container_settings) as client:
        yield client


@contextmanager
def _space(settings: Settings, slug: str) -> Iterator[Session]:
    engine = create_engine(tenant_database_url(settings, tenant_database_name(slug)), future=True)
    try:
        with session_factory(engine)() as session:
            yield session
    finally:
        engine.dispose()


def _seed(settings: Settings, match_id: UUID) -> tuple[UUID, UUID]:
    """The freelancer's space with the customer «rebase», the letter's deal and the registry
    row naming them; returns the customer's and the deal's ids."""
    registry = ensure_tenants_database(settings)
    try:
        with session_factory(registry)() as session:
            tenant = TenantService(session, settings).provision(
                TenantSignup(
                    first_name="Ada", slug=SLUG, name="Ada Lovelace", email=FREELANCER_EMAIL
                )
            )
            tenant_id = tenant.id
        with _space(settings, SLUG) as space:
            customer = OrganizationService(space).create(
                OrganizationCreate(legal_name="rebase S.r.l.", vat_number="01234567890"),
                Actor.rebase(),
            )
            deal = DealService(space).create(
                DealCreate(name="Lettera n. 3/2026", customer_id=customer.id),
                Actor.rebase(),
                rebase_ref=f"rebase:match={match_id}",
            )
        with session_factory(registry)() as session:
            session.add(
                RebaseEngagement(
                    match_id=match_id,
                    tenant_id=tenant_id,
                    space_created=True,
                    customer_id=customer.id,
                    deal_id=deal.id,
                )
            )
            session.commit()
        return customer.id, deal.id
    finally:
        registry.dispose()


def _row(settings: Settings, match_id: UUID) -> RebaseEngagement:
    registry = ensure_tenants_database(settings)
    try:
        with session_factory(registry)() as session:
            row = session.query(RebaseEngagement).filter_by(match_id=match_id).one()
            session.expunge(row)
            return row
    finally:
        registry.dispose()


def test_a_merge_over_http_points_the_registry_at_the_survivor(
    door_client: TestClient, container_settings: Settings
) -> None:
    """REB-1031 through the route a person takes: folding the customer the door made into
    another row of the same company, inside the freelancer's space, points the registry row
    at the survivor instead of the archived row. A preview writes nothing."""
    match_id = uuid4()
    customer_id, deal_id = _seed(container_settings, match_id)
    with _space(container_settings, SLUG) as space:
        admin = UserRepository(space).get_by_email(FREELANCER_EMAIL)
        assert admin is not None
        token = issue_access_token(admin.id, "admin", container_settings, space=SLUG)
    member = {"Cookie": f"{ACCESS_COOKIE}={token}"}
    made = door_client.post(
        f"/{SLUG}/api/organizations", json={"legal_name": "rebase srl"}, headers=member
    )
    assert made.status_code == 201, made.text
    survivor = made.json()["id"]
    merge = f"/{SLUG}/api/organizations/{survivor}/merge"

    preview = door_client.post(merge, json={"loser_id": str(customer_id)}, headers=member)
    assert preview.status_code == 200, preview.text
    assert preview.json()["moved"]["rebase_engagements"] == 1
    assert _row(container_settings, match_id).customer_id == customer_id

    done = door_client.post(
        merge, json={"loser_id": str(customer_id), "dry_run": False}, headers=member
    )
    assert done.status_code == 200, done.text
    assert done.json()["moved"]["rebase_engagements"] == 1
    row = _row(container_settings, match_id)
    assert str(row.customer_id) == survivor
    assert row.deal_id == deal_id
