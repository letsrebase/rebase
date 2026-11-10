"""Documenso's webhook over HTTP (REB-1019, collaborators design § 2, «Routing the webhook»).

The route is the root's alone, behind `X-Documenso-Secret`. It reads the space from the envelope's
`externalId` (`<tenant id>:<document id>`), applies a rejection or a cancellation at once and
leaves a completion for `finish`, which confirms it with Documenso itself. **What is not ours is
acknowledged and ignored**: the hub keeps signing in production until a separate cutover, and its
envelopes must never be touched by the CRM.

The registry and every space this file creates live in the container `api_engine` starts; two
spaces are provisioned through the signup route and furnished through the services, and the
teardown drops every space the registry lists. Documenso is the core suite's fake, reached through
the API's one seam, `get_signing_factory`.
"""

import datetime as dt
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fakes.fake_documenso import BASE, TOKEN, WEBHOOK_SECRET, FakeDocumenso
from fakes.letter_world import ADMIN, World, needs_binaries
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.orm import Session

from pigrocrm.core.collaborators.signing import SigningService
from pigrocrm.core.config import Settings, get_settings
from pigrocrm.core.db import session_factory
from pigrocrm.core.db.sidecar import drop_database
from pigrocrm.core.documents.models import Document
from pigrocrm.core.mail import RecordingSender
from pigrocrm.core.storage import DocumentStorage
from pigrocrm.core.tenants import TenantService, ensure_tenants_database
from pigrocrm.core.tenants.database import tenant_database_name, tenant_database_url
from pigrocrm_api.deps import registry_session_factory, reset_session_factories
from pigrocrm_api.main import create_app
from pigrocrm_api.ratelimit import reset_rate_limit
from pigrocrm_api.signing import get_signing_factory

# Provisioning a space writes a real letter: pandoc and Typst live in the API's image.
pytestmark = needs_binaries

WEBHOOK = "/api/documenso/webhook"
SECRET = {"X-Documenso-Secret": WEBHOOK_SECRET}
SIGNED_AT = dt.datetime(2026, 11, 3, 10, 0, tzinfo=dt.UTC)
SLUGS = ("studio-uno", "studio-due")
ACK = {"ok": True}


@pytest.fixture
def container_settings(api_engine: Engine, tmp_path: Path) -> Settings:
    return Settings(
        database_url=api_engine.url.render_as_string(hide_password=False),
        jwt_secret="test-secret-for-the-api-test-suite-only",
        cookie_secure=True,
        public_url="https://pigro.test",
        hub_url="http://127.0.0.1:9",
        documenso_url=BASE,
        documenso_api_token=TOKEN,
        documenso_webhook_secret=WEBHOOK_SECRET,
        storage_local_root=str(tmp_path / "storage"),
        _env_file=None,  # type: ignore[call-arg]
    )


@contextmanager
def _serving(settings: Settings) -> Iterator[TestClient]:
    """A client whose root is the container's CRM database and whose spaces are real databases on
    the same server. The teardown drops every space the registry lists, then empties it."""
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
            connection.execute(text("DELETE FROM tenants"))
        registry.dispose()
        reset_session_factories()
        for slug in slugs:
            drop_database(settings, tenant_database_url(settings, tenant_database_name(slug)))
        get_settings.cache_clear()


@contextmanager
def _space(settings: Settings, slug: str) -> Iterator[Session]:
    engine = create_engine(tenant_database_url(settings, tenant_database_name(slug)), future=True)
    try:
        with session_factory(engine)() as session:
            yield session
    finally:
        engine.dispose()


def _body(
    event: str = "DOCUMENT_COMPLETED", external_id: str | None = None, envelope: str = "envelope_x"
) -> dict[str, Any]:
    """A delivery for an envelope the fake knows nothing about."""
    return {
        "event": event,
        "payload": {"envelopeId": envelope, "externalId": external_id, "status": "COMPLETED"},
    }


@dataclass
class Sent:
    """A provisioned space whose freelancer's framework agreement is out for signature."""

    slug: str
    tenant_id: UUID
    document_id: UUID
    envelope_id: str


class Spaces:
    def __init__(
        self, client: TestClient, settings: Settings, fake: FakeDocumenso, tmp_path: Path
    ) -> None:
        self.client = client
        self.settings = settings
        self.fake = fake
        self.mails = RecordingSender()
        self.tmp_path = tmp_path
        client.app.dependency_overrides[get_signing_factory] = lambda: self.factory  # type: ignore[attr-defined]

    def factory(
        self, session: Session, storage: DocumentStorage, settings: Settings, tenant_id: UUID | None
    ) -> SigningService:
        return SigningService(
            session,
            storage,
            settings,
            documenso=self.fake.client(),
            sender=self.mails,
            tenant_id=tenant_id,
        )

    def provision(self, slug: str) -> Sent:
        """A space through the signup route, with a freelancer, an engagement and its letter, sent:
        the framework agreement is out for signature on the fake."""
        created = self.client.post(
            "/api/tenants/",
            json={
                "slug": slug,
                "name": "Ada Lovelace",
                "first_name": "Ada",
                "last_name": "Lovelace",
                "email": f"ada@{slug}.it",
            },
        )
        assert created.status_code == 201, created.text
        registry = registry_session_factory(self.settings)()
        try:
            tenant_id = TenantService(registry, self.settings).get(slug).id
        finally:
            registry.close()
        with _space(self.settings, slug) as session:
            # Where the webhook's own `space_storage` looks for this space's documents.
            world = World(session, self.tmp_path / "storage" / "tenants" / slug)
            who = world.freelancer()
            engagement = world.engagement(who)
            world.letters.create_letter(engagement.id, {"attivita": "Sviluppo"}, ADMIN)
            signing = SigningService(
                session,
                world.storage,
                self.settings,
                documenso=self.fake.client(),
                sender=self.mails,
                tenant_id=tenant_id,
            )
            signing.send_engagement(engagement.id, ADMIN)
            framework = session.scalars(
                select(Document).where(Document.type == "framework_agreement")
            ).one()
            assert framework.envelope_id is not None
            return Sent(slug, tenant_id, framework.id, framework.envelope_id)

    def deliver(self, body: dict[str, Any], headers: dict[str, str] | None = None) -> Any:
        return self.client.post(WEBHOOK, json=body, headers=SECRET if headers is None else headers)

    def event(self, sent: Sent, event: str = "DOCUMENT_COMPLETED") -> dict[str, Any]:
        return dict(self.fake.webhook(sent.envelope_id, event))

    def state(self, sent: Sent) -> str | None:
        with _space(self.settings, sent.slug) as session:
            document = session.get(Document, sent.document_id)
            assert document is not None
            return document.signature_state

    def cancel_reason(self, sent: Sent) -> str | None:
        with _space(self.settings, sent.slug) as session:
            document = session.get(Document, sent.document_id)
            assert document is not None
            return document.cancel_reason


@pytest.fixture
def spaces(container_settings: Settings, tmp_path: Path) -> Iterator[Spaces]:
    with _serving(container_settings) as client:
        yield Spaces(client, container_settings, FakeDocumenso(), tmp_path)


@pytest.fixture
def two(spaces: Spaces) -> tuple[Spaces, Sent, Sent]:
    return spaces, spaces.provision(SLUGS[0]), spaces.provision(SLUGS[1])


# ---- the secret and the root's door ------------------------------------------------------------


def test_without_a_secret_setting_the_webhook_is_off(container_settings: Settings) -> None:
    off = container_settings.model_copy(update={"documenso_webhook_secret": ""})
    with _serving(off) as client:
        for headers in ({}, SECRET):
            refused = client.post(WEBHOOK, json=_body(), headers=headers)
            assert refused.status_code == 503, refused.text
            assert refused.json() == {"detail": "webhook_disabled"}


def test_a_missing_empty_or_wrong_secret_is_refused(container_settings: Settings) -> None:
    with _serving(container_settings) as client:
        for headers in (
            {},
            {"X-Documenso-Secret": ""},
            {"X-Documenso-Secret": "sbagliato"},
            {"X-Documenso-Secret": WEBHOOK_SECRET[:-1]},
            {"X-Documenso-Secret": WEBHOOK_SECRET + "x"},
            # Starlette decodes headers as latin-1: a non-ASCII value is refused, not a 500.
            {"X-Documenso-Secret": "è".encode("latin-1")},
        ):
            refused = client.post(WEBHOOK, json=_body(), headers=headers)  # type: ignore[arg-type]
            assert refused.status_code == 401, (headers, refused.text)
            assert refused.json() == {"detail": "invalid_secret"}


def test_the_secret_is_checked_before_the_body_is_judged_but_after_it_is_read(
    container_settings: Settings,
) -> None:
    """The check is a dependency: a JSON body that fails the model's own checks meets the secret
    first, and a body that is not JSON at all is a 422 whatever the header says."""
    with _serving(container_settings) as client:
        assert client.post(WEBHOOK, json={}).status_code == 401
        broken = client.post(WEBHOOK, content=b"{", headers={"Content-Type": "application/json"})
        assert broken.status_code == 422, broken.text
        # With the secret, a body that is not an envelope is a 422 too.
        invalid = client.post(WEBHOOK, json={"event": "DOCUMENT_COMPLETED"}, headers=SECRET)
        assert invalid.status_code == 422, invalid.text


def test_the_webhook_is_the_roots_and_a_spaces_prefix_is_a_404(spaces: Spaces) -> None:
    sent = spaces.provision(SLUGS[0])
    body = spaces.event(sent, "DOCUMENT_REJECTED")

    # A real space's own prefix, with the right secret and a document that would move: the same
    # 404 a route that does not exist gives, and nothing is touched.
    prefixed = spaces.client.post(f"/{sent.slug}/api/documenso/webhook", json=body, headers=SECRET)
    assert prefixed.status_code == 404, prefixed.text
    nowhere = spaces.client.post("/nessuno-qui/api/documenso/webhook", json=body, headers=SECRET)
    assert nowhere.status_code == 404, nowhere.text
    assert spaces.state(sent) == "sent"


# ---- what is not ours is acknowledged and ignored ----------------------------------------------


def test_an_event_that_moves_nothing_is_acknowledged(spaces: Spaces) -> None:
    sent = spaces.provision(SLUGS[0])

    for event in ("DOCUMENT_SENT", "DOCUMENT_OPENED", "DOCUMENT_CREATED", "WHATEVER"):
        acknowledged = spaces.deliver(spaces.event(sent, event))
        assert (acknowledged.status_code, acknowledged.json()) == (200, ACK), event
    assert spaces.state(sent) == "sent"


def test_an_envelope_of_another_system_is_acknowledged_and_nothing_is_touched(
    two: tuple[Spaces, Sent, Sent],
) -> None:
    spaces, first, second = two
    for external_id in (
        None,
        "",
        "contratto-77",
        f"{uuid4()}-{uuid4()}",
        "root:not-a-uuid",
        f"not-a-uuid:{uuid4()}",
        # A space this installation does not have, and the id of a document nobody holds.
        f"{uuid4()}:{first.document_id}",
        f"{first.tenant_id}:{uuid4()}",
        f"root:{first.document_id}",
    ):
        # A rejection is what would move a document at once, if anything did.
        body = spaces.event(first, "DOCUMENT_REJECTED")
        body["payload"]["externalId"] = external_id
        acknowledged = spaces.deliver(body)
        assert (acknowledged.status_code, acknowledged.json()) == (200, ACK), external_id
    assert spaces.state(first) == spaces.state(second) == "sent"


def test_a_document_that_does_not_hold_the_envelope_is_left_alone(
    two: tuple[Spaces, Sent, Sent],
) -> None:
    """The externalId names a real space and a real document, but the document's own envelope is
    another: a forged id, or a leftover of an envelope that was replaced."""
    spaces, first, second = two
    body = spaces.event(first, "DOCUMENT_REJECTED")
    body["payload"]["externalId"] = f"{first.tenant_id}:{second.document_id}"
    assert spaces.deliver(body).json() == ACK
    # The right space, the right id, and an envelope the document never had.
    other = spaces.event(first, "DOCUMENT_REJECTED")
    other["payload"]["envelopeId"] = "envelope_9999"
    other["payload"]["externalId"] = f"{first.tenant_id}:{first.document_id}"
    assert spaces.deliver(other).json() == ACK
    assert spaces.state(first) == spaces.state(second) == "sent"


def test_a_bare_hub_id_is_ignored_when_the_leads_space_does_not_hold_it(
    two: tuple[Spaces, Sent, Sent],
) -> None:
    spaces, first, second = two
    # The hub's envelopes carry the bare document id. With no leads space there is nowhere to
    # resolve it, even if some space holds a document with that very id and envelope.
    for sent in (first, second):
        spaces.fake.envelopes[sent.envelope_id].payload["externalId"] = str(sent.document_id)
        assert spaces.deliver(spaces.event(sent, "DOCUMENT_REJECTED")).json() == ACK
    assert spaces.state(first) == spaces.state(second) == "sent"

    # With a leads space, only that space is asked: the other space's document is not found there.
    with_leads = spaces.settings.model_copy(update={"leads_space": first.slug})
    spaces.client.app.dependency_overrides[get_settings] = lambda: with_leads  # type: ignore[attr-defined]
    assert spaces.deliver(spaces.event(second, "DOCUMENT_REJECTED")).json() == ACK
    assert spaces.state(second) == "sent"
    # A leads space that does not exist routes nowhere.
    missing = spaces.settings.model_copy(update={"leads_space": "non-esiste"})
    spaces.client.app.dependency_overrides[get_settings] = lambda: missing  # type: ignore[attr-defined]
    assert spaces.deliver(spaces.event(first, "DOCUMENT_REJECTED")).json() == ACK
    assert spaces.state(first) == "sent"


# ---- what is ours ------------------------------------------------------------------------------


def test_a_bare_hub_id_resolves_in_the_leads_space(two: tuple[Spaces, Sent, Sent]) -> None:
    """An envelope imported with its documents carries the bare id, which only the leads space
    holds."""
    spaces, first, second = two
    spaces.fake.envelopes[first.envelope_id].payload["externalId"] = str(first.document_id)
    with_leads = spaces.settings.model_copy(update={"leads_space": first.slug})
    spaces.client.app.dependency_overrides[get_settings] = lambda: with_leads  # type: ignore[attr-defined]

    spaces.fake.sign(first.envelope_id, SIGNED_AT)
    assert spaces.deliver(spaces.event(first)).json() == ACK
    assert spaces.state(first) == "signed"
    assert spaces.state(second) == "sent"


def test_a_completion_is_confirmed_with_documenso_in_the_space_the_envelope_names(
    two: tuple[Spaces, Sent, Sent],
) -> None:
    spaces, first, second = two
    assert first.tenant_id != second.tenant_id
    payload = spaces.fake.envelopes[second.envelope_id].payload
    assert payload["externalId"] == f"{second.tenant_id}:{second.document_id}"
    # Both freelancers sign, but Documenso's delivery reaches only the second space's envelope.
    spaces.fake.sign(first.envelope_id, SIGNED_AT)
    spaces.fake.sign(second.envelope_id, SIGNED_AT)

    acknowledged = spaces.deliver(spaces.event(second))
    assert (acknowledged.status_code, acknowledged.json()) == (200, ACK)

    assert spaces.state(second) == "signed"
    # The other space heard of nothing: its document waits for its own delivery.
    assert spaces.state(first) == "sent"
    with _space(spaces.settings, second.slug) as session:
        document = session.get(Document, second.document_id)
        assert document is not None
        assert document.signed_at == SIGNED_AT
        assert document.signed_version is not None
        assert document.signed_copy_to_freelancer_at is not None
    # The recovery ran after the response, in the second space: the sealed copy was mailed there.
    assert any(mail.attachments for mail in spaces.mails.sent)
    assert spaces.fake.called("download") == 1

    # Delivered again (Documenso retries), nothing happens twice.
    assert spaces.deliver(spaces.event(second)).json() == ACK
    assert spaces.fake.called("download") == 1


def test_a_forged_completion_with_no_signature_on_documenso_leaves_the_document_sent(
    two: tuple[Spaces, Sent, Sent],
) -> None:
    """The secret travels in clear: a completion is moved only once Documenso itself says so."""
    spaces, first, _ = two
    assert spaces.fake.envelopes[first.envelope_id].status == "PENDING"

    acknowledged = spaces.deliver(spaces.event(first))
    assert (acknowledged.status_code, acknowledged.json()) == (200, ACK)

    assert spaces.state(first) == "sent"
    # It did ask: `finish` read the envelope, found it still pending and moved nothing.
    assert spaces.fake.called("get") >= 1
    assert spaces.fake.called("download") == 0


def test_a_rejection_cancels_the_document_at_once_with_the_signers_reason(
    two: tuple[Spaces, Sent, Sent],
) -> None:
    spaces, first, second = two
    spaces.fake.reject(first.envelope_id, "Non sono io")

    assert spaces.deliver(spaces.event(first, "DOCUMENT_REJECTED")).json() == ACK

    assert spaces.state(first) == "cancelled"
    assert "Non sono io" in (spaces.cancel_reason(first) or "")
    assert spaces.state(second) == "sent"


def test_a_failure_while_applying_is_acknowledged_not_raised(
    two: tuple[Spaces, Sent, Sent], monkeypatch: pytest.MonkeyPatch
) -> None:
    spaces, first, _ = two

    def broken(self: SigningService, outcome: object) -> None:
        raise RuntimeError("the database went away")

    monkeypatch.setattr(SigningService, "apply", broken)
    acknowledged = spaces.deliver(spaces.event(first, "DOCUMENT_REJECTED"))
    assert (acknowledged.status_code, acknowledged.json()) == (200, ACK)
    assert spaces.state(first) == "sent"
