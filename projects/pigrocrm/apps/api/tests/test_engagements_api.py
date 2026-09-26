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

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

import pigrocrm.core.engagements.service as engagements_service
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.repository import UserRepository
from pigrocrm.core.config import Settings, get_settings
from pigrocrm.core.db import session_factory, today_local
from pigrocrm.core.db.sidecar import drop_database
from pigrocrm.core.deals.service import DealService
from pigrocrm.core.engagements.service import BUSY, GONE, REVERSED
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
# How `Conflict` renders its message (`errors.py`): the problem's `detail` carries the
# entity before the sentence, and `reason` the sentence alone, which is what the hub
# shows.
GONE_DETAIL = f"engagement: {GONE}"


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
        # The gate is a router-level dependency, run before FastAPI even looks at the
        # body or the path: an empty body and a non-UUID match id are still a 404, not
        # the 422 either would earn once the door exists.
        empty_body = client.put(f"/api/rebase/engagements/{match_id}", json={})
        assert empty_body.status_code == 404, empty_body.text
        bad_path = client.put("/api/rebase/engagements/not-a-uuid", json={})
        assert bad_path.status_code == 404, bad_path.text


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
    # The token gate runs before the body or the path are parsed: a missing bearer with
    # a body that would otherwise be a 422 (empty, or a non-UUID match id) is still 401.
    bad_body = door_client.put(f"/api/rebase/engagements/{match_id}", json={})
    assert bad_body.status_code == 401, bad_body.text
    bad_path = door_client.put("/api/rebase/engagements/not-a-uuid", json={})
    assert bad_path.status_code == 401, bad_path.text


def test_report_without_bearer_is_401(door_client: TestClient) -> None:
    match_id = uuid4()
    refused = door_client.get(f"/api/rebase/engagements/{match_id}/report")
    assert refused.status_code == 401, refused.text


def test_root_only_a_spaces_own_prefix_is_404(door_client: TestClient) -> None:
    """Spec § 2.1: the door answers only on the root installation. Under a real space's
    own prefix -- not merely one that names no tenant -- the request must still be
    refused outright: `space_base_settings` would fold that slug into
    `PIGROCRM_PUBLIC_URL` and double it into the links this service builds, so the
    router refuses the prefix itself rather than quietly using the wrong settings."""
    match_id = uuid4()
    created = door_client.put(
        f"/api/rebase/engagements/{match_id}", json=_upsert(), headers=_bearer()
    )
    assert created.status_code == 201, created.text
    slug = created.json()["slug"]

    other_match = uuid4()
    prefixed_put = door_client.put(
        f"/{slug}/api/rebase/engagements/{other_match}", json=_upsert(), headers=_bearer()
    )
    assert prefixed_put.status_code == 404, prefixed_put.text
    prefixed_report = door_client.get(
        f"/{slug}/api/rebase/engagements/{match_id}/report", headers=_bearer()
    )
    assert prefixed_report.status_code == 404, prefixed_report.text


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
    refused = door_client.put(f"/api/rebase/engagements/{match_id}", json=body, headers=_bearer())
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


def _gone_problem(refused: httpx.Response) -> None:
    """The deleted deal's 409 as the hub reads it: a problem document whose `reason`
    is the sentence alone, beside the `detail` `Conflict` builds from it."""
    assert refused.status_code == 409, refused.text
    body = refused.json()
    assert body["code"] == "conflict"
    assert body["reason"] == GONE
    assert body["detail"] == GONE_DETAIL


def test_put_of_deleted_deal_is_409(door_client: TestClient, container_settings: Settings) -> None:
    """Spec § 2.3 step 2 over HTTP: the match's deal deleted in the space, a retry of
    the PUT is refused and nothing is recreated."""
    match_id = uuid4()
    created = door_client.put(
        f"/api/rebase/engagements/{match_id}", json=_upsert(), headers=_bearer()
    )
    assert created.status_code == 201, created.text
    linked = created.json()
    with _space(container_settings, linked["slug"]) as space:
        DealService(space).soft_delete(UUID(linked["deal_id"]), Actor.system())

    refused = door_client.put(
        f"/api/rebase/engagements/{match_id}", json=_upsert(), headers=_bearer()
    )
    _gone_problem(refused)
    assert refused.json()["match_id"] == str(match_id)


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

    refused = door_client.get(f"/api/rebase/engagements/{match_id}/report", headers=_bearer())
    _gone_problem(refused)


def test_report_of_unknown_match_is_404(door_client: TestClient) -> None:
    unknown = uuid4()
    refused = door_client.get(f"/api/rebase/engagements/{unknown}/report", headers=_bearer())
    assert refused.status_code == 404, refused.text
    assert refused.json()["code"] == "not_found"


def test_report_reversed_period_is_422(door_client: TestClient) -> None:
    """`a` before `da` is a `ValidationFailed("engagement", "periodo", ...)` like a span
    over 800 days, with a sentence of its own (`test_engagements.py`'s own coverage):
    here only the wire shape of the problem document is new."""
    match_id = uuid4()
    created = door_client.put(
        f"/api/rebase/engagements/{match_id}", json=_upsert(), headers=_bearer()
    )
    assert created.status_code == 201, created.text

    refused = door_client.get(
        f"/api/rebase/engagements/{match_id}/report",
        params={"da": "2026-10-02", "a": "2026-10-01"},
        headers=_bearer(),
    )
    assert refused.status_code == 422, refused.text
    body = refused.json()
    assert body["code"] == "validation_failed"
    assert body["entity"] == "engagement"
    assert body["field"] == "periodo"
    assert body["reason"] == REVERSED


class _Lines(logging.Handler):
    """The router's own log lines, on a handler of the logger itself: not `caplog`,
    whose handler hangs off the root, which the provisioning's migration rebuilds
    (`fileConfig` in `env.py`, the same reason `test_migrations.py`'s REB-190 test
    brings its own)."""

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


def test_unreachable_space_is_503(door_client: TestClient, container_settings: Settings) -> None:
    """After the space exists, its database going away must not surface as a 500:
    `_run` in `routers/engagements.py` turns it into a 503, on both routes, and logs
    the match's id and the tenant's -- enough to find a space a restart left without
    its database -- never its slug nor the address a lock statement's parameters would
    carry."""
    match_id = uuid4()
    created = door_client.put(
        f"/api/rebase/engagements/{match_id}", json=_upsert(), headers=_bearer()
    )
    assert created.status_code == 201, created.text
    slug = created.json()["slug"]
    registry = ensure_tenants_database(container_settings)
    try:
        with registry.connect() as connection:
            tenant_id = connection.execute(
                text("SELECT id FROM tenants WHERE slug = :slug"), {"slug": slug}
            ).scalar_one()
    finally:
        registry.dispose()
    drop_database(
        container_settings, tenant_database_url(container_settings, tenant_database_name(slug))
    )

    router_log = logging.getLogger("pigrocrm_api.routers.engagements")
    grab = _Lines()
    router_log.addHandler(grab)
    try:
        again = door_client.put(
            f"/api/rebase/engagements/{match_id}", json=_upsert(), headers=_bearer()
        )
        assert again.status_code == 503, again.text

        report = door_client.get(f"/api/rebase/engagements/{match_id}/report", headers=_bearer())
        assert report.status_code == 503, report.text
    finally:
        router_log.removeHandler(grab)

    assert len(grab.lines) == 2
    for line in grab.lines:
        assert str(match_id) in line and str(tenant_id) in line
        assert slug not in line and FREELANCER_EMAIL not in line


def test_busy_address_is_503_without_the_address(
    door_client: TestClient, container_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another call holding the freelancer's address past the lock's timeout is a «not
    now»: 503, which the hub retries, not the 409 it would file as refused for good;
    and the answer carries the sentence, not the address."""
    monkeypatch.setattr(engagements_service, "LOCK_TIMEOUT", "200ms")
    registry = ensure_tenants_database(container_settings)
    try:
        with registry.connect() as holder:
            holder.execute(text("SELECT pg_advisory_lock(hashtext(:e))"), {"e": FREELANCER_EMAIL})
            try:
                busy = door_client.put(
                    f"/api/rebase/engagements/{uuid4()}", json=_upsert(), headers=_bearer()
                )
            finally:
                holder.execute(
                    text("SELECT pg_advisory_unlock(hashtext(:e))"), {"e": FREELANCER_EMAIL}
                )
                holder.commit()
    finally:
        registry.dispose()

    assert busy.status_code == 503, busy.text
    assert busy.json() == {"detail": BUSY}
    assert FREELANCER_EMAIL not in busy.text
