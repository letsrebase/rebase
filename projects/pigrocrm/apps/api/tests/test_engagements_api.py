"""rebase's engagements door over HTTP (spec 2026-09-25 § 2.3, § 2.4): the two routes,
`PUT /api/rebase/engagements/{match_id}` and its `.../report`, answer under
`PIGROCRM_ENGAGEMENTS_TOKEN`, the way `GET /api/tenants/` answers under
`PIGROCRM_REGISTRY_TOKEN` (`test_tenants_api.py`). The service behind them is A4/A5's
own, exercised at length in `packages/core/tests/test_engagements.py`: this file is
only the two routes and the token that guards them.

The registry and every space this file creates live in the container `api_engine`
starts. Unlike `test_tenants_api.py`'s fixed `SLUG`, the slug here is whatever
`slugify` makes of the freelancer's name, so `_serving`'s teardown drops every space
the registry lists rather than one name it assumes.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.repository import UserRepository
from pigrocrm.core.config import Settings, get_settings
from pigrocrm.core.db import session_factory, today_local
from pigrocrm.core.db.sidecar import drop_database
from pigrocrm.core.deals.service import DealService
from pigrocrm.core.tenants import ensure_tenants_database
from pigrocrm.core.tenants.database import tenant_database_name, tenant_database_url
from pigrocrm.core.timetracking.schemas import TimeEntryCreate
from pigrocrm.core.timetracking.service import TimeEntryService
from pigrocrm_api.deps import reset_session_factories
from pigrocrm_api.main import create_app
from pigrocrm_api.ratelimit import reset_rate_limit

ENGAGEMENTS_TOKEN = "un-token-per-la-porta"
REGISTRY_TOKEN = "un-token-lungo-solo-per-questa-suite"
FREELANCER_EMAIL = "ada@studio.it"


def _upsert(**parts: dict[str, Any]) -> dict[str, Any]:
    """The body of spec § 2.3, with any part's fields overridden by name -- the same
    shape as `test_engagements.py`'s own `_upsert`, as JSON rather than a model."""
    body: dict[str, dict[str, Any]] = {
        "freelancer": {"email": FREELANCER_EMAIL, "nome": "Ada", "cognome": "Lovelace"},
        "lettera": {
            "numero": "3/2026",
            "ruolo": "Backend developer",
            "azienda": "Acme S.r.l.",
            "data_inizio": "2026-10-01",
            "data_fine": "2026-12-31",
            "compenso": "400.00",
            "giorni_previsti": 40,
        },
        "rebase": {
            "ragione_sociale": "rebase S.r.l.",
            "partita_iva": "01234567890",
            "codice_fiscale": "01234567890",
            "indirizzo": "Via Roma 1, 20121 Milano",
            "pec": "rebase@pec.it",
            "codice_sdi": "M5UXCR1",
        },
    }
    for part, overrides in parts.items():
        body[part] = {**body[part], **overrides}
    return body


def _bearer(token: str = ENGAGEMENTS_TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def container_settings(api_engine: Engine) -> Settings:
    return Settings(
        database_url=api_engine.url.render_as_string(hide_password=False),
        jwt_secret="test-secret-for-the-api-test-suite-only",
        cookie_secure=True,
        public_url="https://pigro.test",
        hub_url="http://127.0.0.1:9",
        engagements_token=ENGAGEMENTS_TOKEN,
        _env_file=None,  # type: ignore[call-arg]
    )


@contextmanager
def _serving(settings: Settings) -> Iterator[TestClient]:
    """Like `test_tenants_api.py`'s own `_serving`, except the teardown drops every
    space the registry lists instead of one fixed slug (the door names its own slug
    from the freelancer's name), and empties `rebase_engagements` before `tenants`:
    its `tenant_id` is a foreign key into it."""
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


def _log_hours(settings: Settings, slug: str, deal_id: UUID, day: date, descrizione: str) -> None:
    """Eight hours on the deal, logged for the space's admin through the service: the
    admin has no password, so the service is the way in (A5's own test does the same
    at the core level, `test_engagements.py::_log`)."""
    with _space(settings, slug) as space:
        admin = UserRepository(space).get_by_email(FREELANCER_EMAIL)
        assert admin is not None
        TimeEntryService(space).create(
            TimeEntryCreate(
                deal_id=deal_id,
                user_id=admin.id,
                data=day,
                ore=Decimal("8.00"),
                descrizione=descrizione,
            ),
            Actor.system(),
        )


def test_door_is_absent_without_the_token(container_settings: Settings) -> None:
    """The default, empty token: a self-hosted installation exposes nothing new."""
    without_token = container_settings.model_copy(update={"engagements_token": ""})
    with _serving(without_token) as client:
        match_id = uuid4()
        put = client.put(f"/api/rebase/engagements/{match_id}", json=_upsert())
        assert put.status_code == 404, put.text
        report = client.get(f"/api/rebase/engagements/{match_id}/report")
        assert report.status_code == 404, report.text
        # Whatever the caller presents: a 401 would say "there is a door".
        wrong = client.put(
            f"/api/rebase/engagements/{match_id}", json=_upsert(), headers=_bearer("qualcosa")
        )
        assert wrong.status_code == 404, wrong.text


def test_wrong_bearer_is_401(door_client: TestClient) -> None:
    match_id = uuid4()
    wrong = _bearer("sbagliato")
    put_wrong = door_client.put(
        f"/api/rebase/engagements/{match_id}", json=_upsert(), headers=wrong
    )
    assert put_wrong.status_code == 401, put_wrong.text
    report_wrong = door_client.get(f"/api/rebase/engagements/{match_id}/report", headers=wrong)
    assert report_wrong.status_code == 401, report_wrong.text
    # No bearer at all is the same refusal, not a 404.
    put_missing = door_client.put(f"/api/rebase/engagements/{match_id}", json=_upsert())
    assert put_missing.status_code == 401, put_missing.text


def test_registry_token_does_not_open_the_door(container_settings: Settings) -> None:
    """`PIGROCRM_REGISTRY_TOKEN` keeps listing spaces alone: a hub that holds only it
    cannot create a space or read an hour (spec § 2.1)."""
    both = container_settings.model_copy(update={"registry_token": REGISTRY_TOKEN})
    with _serving(both) as client:
        match_id = uuid4()
        headers = _bearer(REGISTRY_TOKEN)
        put = client.put(f"/api/rebase/engagements/{match_id}", json=_upsert(), headers=headers)
        assert put.status_code == 401, put.text
        report = client.get(f"/api/rebase/engagements/{match_id}/report", headers=headers)
        assert report.status_code == 401, report.text


def test_put_creates_then_answers_200(door_client: TestClient) -> None:
    match_id = uuid4()
    created = door_client.put(
        f"/api/rebase/engagements/{match_id}", json=_upsert(), headers=_bearer()
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["creato"] is True and body["spazio_creato"] is True
    assert body["slug"] == "ada-lovelace"
    assert body["url"] == "https://pigro.test/ada-lovelace/app/"
    assert body["deal_url"] == f"https://pigro.test/ada-lovelace/app/deal/{body['deal_id']}"

    again = door_client.put(
        f"/api/rebase/engagements/{match_id}", json=_upsert(), headers=_bearer()
    )
    assert again.status_code == 200, again.text
    again_body = again.json()
    assert again_body["creato"] is False
    assert again_body["deal_id"] == body["deal_id"]
    assert again_body["customer_id"] == body["customer_id"]


def test_put_refuses_an_unknown_field(door_client: TestClient) -> None:
    match_id = uuid4()
    body = {**_upsert(), "extra": "sorpresa"}
    refused = door_client.put(
        f"/api/rebase/engagements/{match_id}", json=body, headers=_bearer()
    )
    assert refused.status_code == 422, refused.text


def test_put_without_public_url_is_503(container_settings: Settings) -> None:
    """A missing setting is the installation's fault, not the caller's: 503, not 422."""
    no_url = container_settings.model_copy(update={"public_url": ""})
    with _serving(no_url) as client:
        match_id = uuid4()
        refused = client.put(
            f"/api/rebase/engagements/{match_id}", json=_upsert(), headers=_bearer()
        )
        assert refused.status_code == 503, refused.text
        assert "PIGROCRM_PUBLIC_URL" in refused.text


def test_report_answers_the_hours(door_client: TestClient, container_settings: Settings) -> None:
    match_id = uuid4()
    created = door_client.put(
        f"/api/rebase/engagements/{match_id}", json=_upsert(), headers=_bearer()
    )
    assert created.status_code == 201, created.text
    linked = created.json()
    deal_id = UUID(linked["deal_id"])
    today = today_local(container_settings)
    first, second = today - timedelta(days=1), today
    _log_hours(container_settings, linked["slug"], deal_id, first, "Setup")
    _log_hours(container_settings, linked["slug"], deal_id, second, "API")

    report = door_client.get(
        f"/api/rebase/engagements/{match_id}/report",
        params={"da": first.isoformat()},
        headers=_bearer(),
    )
    assert report.status_code == 200, report.text
    body = report.json()
    assert body["slug"] == linked["slug"]
    assert body["deal_url"] == linked["deal_url"]
    assert [g["descrizione"] for g in body["giorni"]] == ["Setup", "API"]
    assert body["totale_ore"] == "16.00"
    assert body["ore_fatturate"] == "0.00"
    assert body["ore_non_fatturate"] == "16.00"


def test_report_of_deleted_deal_is_409(
    door_client: TestClient, container_settings: Settings
) -> None:
    match_id = uuid4()
    created = door_client.put(
        f"/api/rebase/engagements/{match_id}", json=_upsert(), headers=_bearer()
    )
    assert created.status_code == 201, created.text
    linked = created.json()
    with _space(container_settings, linked["slug"]) as space:
        DealService(space).soft_delete(UUID(linked["deal_id"]), Actor.system())

    refused = door_client.get(
        f"/api/rebase/engagements/{match_id}/report", headers=_bearer()
    )
    assert refused.status_code == 409, refused.text
