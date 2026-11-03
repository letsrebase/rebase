"""rebase's leads route over HTTP (spec 2026-10-30 § 2, REB-866): `PUT
/api/rebase/leads/{request_id}` answers under `PIGROCRM_ENGAGEMENTS_TOKEN`, like the
engagements routes (`test_engagements_api.py`), and files into the one space
`PIGROCRM_LEADS_SPACE` names. The service behind it is exercised at length in
`packages/core/tests/test_leads.py`: this file is the route, its token and its statuses.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.orm import Session

import pigrocrm.core.engagements.leads as leads_module
from pigrocrm.core.config import Settings, get_settings
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db import session_factory
from pigrocrm.core.db.sidecar import drop_database
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.engagements.leads import BUSY, NOT_CONFIGURED
from pigrocrm.core.people.models import Person
from pigrocrm.core.tenants import TenantService, TenantSignup, ensure_tenants_database
from pigrocrm.core.tenants.database import tenant_database_name, tenant_database_url
from pigrocrm_api.deps import reset_session_factories
from pigrocrm_api.main import create_app
from pigrocrm_api.ratelimit import reset_rate_limit

ENGAGEMENTS_TOKEN = "un-token-per-la-porta-lungo-abbastanza"
REGISTRY_TOKEN = "un-token-lungo-solo-per-questa-suite"
SLUG = "rebase-lead-test"
MARIA = "maria.rossi@acme.it"


def _body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "legal_entity": "Acme S.r.l.",
        "contact_person": {"nome": "Maria", "last_name": "Rossi", "email": MARIA},
        "deal": {
            "nome": "Acme S.r.l.: Backend developer",
            "expected_value": "24000.00",
            "note": "Richiesta dell'azienda.",
        },
    }
    body.update(overrides)
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
        leads_space=SLUG,
        _env_file=None,  # type: ignore[call-arg]
    )


@contextmanager
def _serving(settings: Settings, *, provision: bool = True) -> Iterator[TestClient]:
    reset_session_factories()
    get_settings.cache_clear()
    reset_rate_limit()
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    registry = ensure_tenants_database(settings)
    if provision:
        with session_factory(registry)() as session:
            TenantService(session, settings).provision(
                TenantSignup(slug=SLUG, nome="rebase", email="spazio@studio.it")
            )
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
def lead_client(container_settings: Settings) -> Iterator[TestClient]:
    with _serving(container_settings) as client:
        yield client


@contextmanager
def _space(settings: Settings) -> Iterator[Session]:
    engine = create_engine(tenant_database_url(settings, tenant_database_name(SLUG)), future=True)
    try:
        with session_factory(engine)() as session:
            yield session
    finally:
        engine.dispose()


def _counts(settings: Settings) -> tuple[int, int, int]:
    with _space(settings) as space:
        return tuple(  # type: ignore[return-value]
            space.scalar(select(func.count()).select_from(model)) or 0
            for model in (Customer, Person, Deal)
        )


def test_door_is_absent_without_the_token(container_settings: Settings) -> None:
    without_token = container_settings.model_copy(update={"engagements_token": ""})
    with _serving(without_token) as client:
        request_id = uuid4()
        put = client.put(f"/api/rebase/leads/{request_id}", json=_body())
        assert put.status_code == 404, put.text
        wrong = client.put(f"/api/rebase/leads/{request_id}", json={}, headers=_bearer("x"))
        assert wrong.status_code == 404, wrong.text


def test_wrong_or_missing_bearer_is_401_before_the_body_is_read(lead_client: TestClient) -> None:
    request_id = uuid4()
    wrong = lead_client.put(
        f"/api/rebase/leads/{request_id}", json=_body(), headers=_bearer("sbagliato")
    )
    assert wrong.status_code == 401, wrong.text
    missing = lead_client.put(f"/api/rebase/leads/{request_id}", json=_body())
    assert missing.status_code == 401, missing.text
    bad_body = lead_client.put(f"/api/rebase/leads/{request_id}", json={})
    assert bad_body.status_code == 401, bad_body.text
    bad_path = lead_client.put("/api/rebase/leads/not-a-uuid", json={})
    assert bad_path.status_code == 401, bad_path.text


def test_registry_token_does_not_open_the_route(container_settings: Settings) -> None:
    both = container_settings.model_copy(update={"registry_token": REGISTRY_TOKEN})
    with _serving(both) as client:
        put = client.put(
            f"/api/rebase/leads/{uuid4()}", json=_body(), headers=_bearer(REGISTRY_TOKEN)
        )
        assert put.status_code == 401, put.text


def test_root_only_a_spaces_own_prefix_is_404(lead_client: TestClient) -> None:
    put = lead_client.put(f"/{SLUG}/api/rebase/leads/{uuid4()}", json=_body(), headers=_bearer())
    assert put.status_code == 404, put.text


def test_two_puts_leave_one_customer_one_person_and_one_deal(
    lead_client: TestClient, container_settings: Settings
) -> None:
    request_id = uuid4()
    created = lead_client.put(f"/api/rebase/leads/{request_id}", json=_body(), headers=_bearer())
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["created"] is True
    assert body["deal_url"] == f"https://pigro.test/{SLUG}/app/deal/{body['deal_id']}"

    again = lead_client.put(f"/api/rebase/leads/{request_id}", json=_body(), headers=_bearer())
    assert again.status_code == 200, again.text
    assert again.json() == {**body, "created": False}
    assert _counts(container_settings) == (1, 1, 1)


def test_another_request_of_the_same_person_is_a_second_deal(
    lead_client: TestClient, container_settings: Settings
) -> None:
    first = lead_client.put(f"/api/rebase/leads/{uuid4()}", json=_body(), headers=_bearer())
    second = lead_client.put(f"/api/rebase/leads/{uuid4()}", json=_body(), headers=_bearer())
    assert (first.status_code, second.status_code) == (201, 201)
    assert second.json()["customer_id"] == first.json()["customer_id"]
    assert second.json()["deal_id"] != first.json()["deal_id"]
    assert _counts(container_settings) == (1, 1, 2)


def test_an_explicit_deal_is_linked_and_a_missing_one_is_422(
    lead_client: TestClient, container_settings: Settings
) -> None:
    created = lead_client.put(f"/api/rebase/leads/{uuid4()}", json=_body(), headers=_bearer())
    deal_id = created.json()["deal_id"]

    linked = lead_client.put(
        f"/api/rebase/leads/{uuid4()}", json=_body(deal_id=deal_id), headers=_bearer()
    )
    assert linked.status_code == 200, linked.text
    assert linked.json()["deal_id"] == deal_id and linked.json()["created"] is False
    assert _counts(container_settings) == (1, 1, 1)

    missing = lead_client.put(
        f"/api/rebase/leads/{uuid4()}", json=_body(deal_id=str(uuid4())), headers=_bearer()
    )
    assert missing.status_code == 422, missing.text
    assert missing.json()["field"] == "deal_id"


@pytest.mark.parametrize(
    "bad",
    [
        {"contact_person": {"nome": "Maria", "email": "non-un-indirizzo"}},
        {"legal_entity": ""},
        {"extra": "sorpresa"},
        {"deal": {"nome": "x" * 256}},
        {"deal": {"nome": "ok", "expected_value": "1000000000000.00"}},
    ],
)
def test_a_body_that_does_not_validate_is_422(lead_client: TestClient, bad: dict[str, Any]) -> None:
    refused = lead_client.put(f"/api/rebase/leads/{uuid4()}", json=_body(**bad), headers=_bearer())
    assert refused.status_code == 422, refused.text


def test_an_unconfigured_space_is_503(container_settings: Settings) -> None:
    empty = container_settings.model_copy(update={"leads_space": ""})
    with _serving(empty, provision=False) as client:
        refused = client.put(f"/api/rebase/leads/{uuid4()}", json=_body(), headers=_bearer())
        assert refused.status_code == 503, refused.text
        assert refused.json() == {"detail": NOT_CONFIGURED}


def test_a_space_the_registry_lacks_is_503(container_settings: Settings) -> None:
    with _serving(container_settings, provision=False) as client:
        refused = client.put(f"/api/rebase/leads/{uuid4()}", json=_body(), headers=_bearer())
        assert refused.status_code == 503, refused.text
        assert refused.json() == {"detail": NOT_CONFIGURED}


def test_a_busy_address_is_503_without_the_address(
    lead_client: TestClient, container_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(leads_module, "LOCK_TIMEOUT", "200ms")
    registry = ensure_tenants_database(container_settings)
    key = f"{leads_module.LOCK_PREFIX}{MARIA}"
    try:
        with registry.connect() as holder:
            holder.execute(text("SELECT pg_advisory_lock(hashtext(:e))"), {"e": key})
            try:
                busy = lead_client.put(
                    f"/api/rebase/leads/{uuid4()}", json=_body(), headers=_bearer()
                )
            finally:
                holder.execute(text("SELECT pg_advisory_unlock(hashtext(:e))"), {"e": key})
                holder.commit()
    finally:
        registry.dispose()
    assert busy.status_code == 503, busy.text
    assert busy.json() == {"detail": BUSY}
    assert MARIA not in busy.text


def test_without_public_url_is_503(container_settings: Settings) -> None:
    no_url = container_settings.model_copy(update={"public_url": ""})
    with _serving(no_url) as client:
        refused = client.put(f"/api/rebase/leads/{uuid4()}", json=_body(), headers=_bearer())
        assert refused.status_code == 503, refused.text
        assert "PIGROCRM_PUBLIC_URL" in refused.text
