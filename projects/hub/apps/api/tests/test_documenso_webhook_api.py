"""REB-391 phase 3: Documenso's webhook over HTTP. The secret before anything, a fast
answer, and the slow part (the sealed copy, the two mails, the released letter) after it,
in a session of its own."""

import json
import logging
from collections.abc import Callable, Iterator
from contextlib import nullcontext
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
import pytest
from contract_flow import ADMIN_EMAIL, SIGNER, TABLES, draft_match
from fakes_contracts import FakeRenderer
from fakes_documenso import FakeDocumenso
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from rebase_api.deps import get_documenso, get_renderer, get_session_opener, get_signing_factory
from rebase_core.config import Settings, get_settings
from rebase_core.documenso import Outcome
from rebase_core.errors import NotFound
from rebase_core.mail import RecordingSender
from rebase_core.models import ContractDocument, User

SECRET = "segreto-del-webhook-di-prova"
CONTRACTS_MAIL = "contratti@rebase.test"
# 23:30 UTC on 30 September is already 1 October in Rome.
SIGNED_AT = datetime(2026, 9, 30, 23, 30, tzinfo=UTC)
WEBHOOK = "/api/hub/documenso/webhook"


@pytest.fixture
def documenso(client: TestClient, api_session: Session) -> Iterator[FakeDocumenso]:
    """Documenso, the renderer, and the background task's session, which is the test's:
    `finish` runs inside the TestClient call, after the response."""
    fake = FakeDocumenso()
    renderer = FakeRenderer(draft=False)
    overrides = client.app.dependency_overrides  # type: ignore[attr-defined]
    overrides[get_documenso] = fake.client
    overrides[get_renderer] = lambda: renderer
    overrides[get_session_opener] = lambda: lambda: nullcontext(api_session)
    yield fake


@pytest.fixture
def admin(client: TestClient, api_session: Session) -> Iterator[None]:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        signer_json=json.dumps(SIGNER),
        contracts_mail=CONTRACTS_MAIL,
        documenso_webhook_secret=SECRET,
    )
    client.app.dependency_overrides[get_settings] = lambda: settings  # type: ignore[attr-defined]
    api_session.add(User(email=ADMIN_EMAIL, nome="Ivan", cognome="", role="admin"))
    api_session.commit()
    yield
    api_session.rollback()
    for table in TABLES:
        api_session.execute(text(f"DELETE FROM {table}"))
    api_session.commit()


def _deliver(
    client: TestClient, body: dict[str, Any], secret: str | None = SECRET
) -> httpx.Response:
    headers = {} if secret is None else {"X-Documenso-Secret": secret}
    return client.post(WEBHOOK, json=body, headers=headers)


def _document(session: Session, kind: str) -> ContractDocument:
    session.expire_all()
    return session.scalars(select(ContractDocument).where(ContractDocument.kind == kind)).one()


def _sent(
    client: TestClient, sender: RecordingSender, session: Session
) -> tuple[dict[str, Any], str]:
    """A match sent for signature: the match, and its framework agreement's envelope, read
    from the row since no page shows it."""
    match = draft_match(client, sender)
    assert client.post(f"/api/hub/matches/{match['id']}/send").status_code == 200
    envelope = _document(session, "quadro").documenso_id
    assert envelope is not None
    return match, envelope


def test_a_webhook_without_the_secret_or_with_an_empty_one_is_refused(
    client: TestClient,
    admin: None,
    sender: RecordingSender,
    documenso: FakeDocumenso,
    api_session: Session,
) -> None:
    """Probe § 11.4: a webhook saved without a secret sends the header empty, so an empty
    header is refused as a wrong one is; and nothing moves on a refused delivery."""
    _match, envelope = _sent(client, sender, api_session)
    documenso.sign(envelope, SIGNED_AT)
    body = documenso.webhook(envelope, "DOCUMENT_COMPLETED")
    for secret in (None, "", "segreto-sbagliato", SECRET.upper()):
        assert _deliver(client, body, secret).status_code == 401, secret
    assert _document(api_session, "quadro").stato == "inviato"


def test_without_the_setting_the_webhook_is_off(
    client: TestClient, admin: None, documenso: FakeDocumenso
) -> None:
    client.app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)  # type: ignore[attr-defined,call-arg]
    assert (
        _deliver(
            client, {"event": "DOCUMENT_COMPLETED", "payload": {"envelopeId": "x"}}
        ).status_code
        == 503
    )


def test_a_signed_framework_is_stored_mailed_and_releases_its_letter(
    client: TestClient,
    admin: None,
    sender: RecordingSender,
    documenso: FakeDocumenso,
    api_session: Session,
) -> None:
    match, envelope = _sent(client, sender, api_session)
    documenso.sign(envelope, SIGNED_AT)
    before = len(sender.sent)

    answered = _deliver(client, documenso.webhook(envelope, "DOCUMENT_COMPLETED"))

    assert answered.status_code == 200 and answered.json() == {"ok": True}
    quadro = _document(api_session, "quadro")
    assert (quadro.stato, quadro.signed_at) == ("firmato", SIGNED_AT)
    assert quadro.signed_pdf == documenso.signed_pdf(envelope)
    mails = sender.sent[before:]
    assert [(mail.to, mail.subject) for mail in mails[:2]] == [
        ("ada@studio.it", "Firmato: contratto quadro rebase"),
        (CONTRACTS_MAIL, "Firmato da Ada Lovelace: contratto quadro rebase"),
    ]
    assert all(mail.attachments[0].content == quadro.signed_pdf for mail in mails[:2])
    letter = _document(api_session, "lettera")
    assert letter.stato == "inviato"
    assert letter.data["data-contratto-quadro"] == "1° ottobre 2026"
    assert mails[2].subject == f"Da firmare: lettera di incarico n. {letter.numero}"

    assert letter.documenso_id is not None
    documenso.sign(letter.documenso_id, SIGNED_AT)
    assert (
        _deliver(client, documenso.webhook(letter.documenso_id, "DOCUMENT_COMPLETED")).status_code
        == 200
    )
    assert client.get(f"/api/hub/matches/{match['id']}").json()["stato"] == "attivo"


def test_the_same_completion_twice_does_everything_once(
    client: TestClient,
    admin: None,
    sender: RecordingSender,
    documenso: FakeDocumenso,
    api_session: Session,
) -> None:
    _match, envelope = _sent(client, sender, api_session)
    documenso.sign(envelope, SIGNED_AT)
    body = documenso.webhook(envelope, "DOCUMENT_COMPLETED")
    assert _deliver(client, body).status_code == 200
    mails, envelopes = len(sender.sent), len(documenso.envelopes)

    assert _deliver(client, body).status_code == 200

    assert (len(sender.sent), len(documenso.envelopes)) == (mails, envelopes)
    downloads = [call for call in documenso.calls if call[1].endswith("/download?version=signed")]
    assert len(downloads) == 1


def test_a_refusal_cancels_the_document_and_says_why(
    client: TestClient,
    admin: None,
    sender: RecordingSender,
    documenso: FakeDocumenso,
    api_session: Session,
) -> None:
    match, envelope = _sent(client, sender, api_session)
    documenso.reject(envelope, "Il domicilio è sbagliato")
    assert _deliver(client, documenso.webhook(envelope, "DOCUMENT_REJECTED")).status_code == 200
    quadro = _document(api_session, "quadro")
    assert (quadro.stato, quadro.cancel_reason) == (
        "annullato",
        "Rifiutato dal freelance: Il domicilio è sbagliato",
    )
    # Spec § 6: a refused framework agreement is still shown on «Match e contratti»,
    # not hidden as a stale draft would be.
    page = client.get(f"/api/hub/freelancers/{match['freelancer_id']}/matches").json()
    assert (page["quadro"]["stato"], page["quadro"]["cancel_reason"]) == (
        "annullato",
        "Rifiutato dal freelance: Il domicilio è sbagliato",
    )


def test_an_unknown_envelope_and_an_event_nobody_handles_are_acknowledged(
    client: TestClient,
    admin: None,
    sender: RecordingSender,
    documenso: FakeDocumenso,
    api_session: Session,
) -> None:
    _match, envelope = _sent(client, sender, api_session)
    opened = documenso.webhook(envelope, "DOCUMENT_OPENED")
    assert _deliver(client, opened).status_code == 200
    stranger = {**documenso.webhook(envelope, "DOCUMENT_COMPLETED")}
    stranger["payload"] = {**stranger["payload"], "envelopeId": "envelope_di_un_altro_ambiente"}
    assert _deliver(client, stranger).status_code == 200
    assert _document(api_session, "quadro").stato == "inviato"


def test_a_malformed_signer_setting_does_not_stop_a_refusal_from_cancelling_its_document(
    client: TestClient,
    admin: None,
    sender: RecordingSender,
    documenso: FakeDocumenso,
    api_session: Session,
) -> None:
    """REB-391: `REBASE_SIGNER_JSON` parses lazily, only on a path that typesets a
    document (REB-406); neither `apply` nor a cancellation's `finish` ever reaches one,
    so a value malformed on this environment must not stop a refusal from cancelling
    its document."""
    _match, envelope = _sent(client, sender, api_session)
    broken = Settings(  # type: ignore[call-arg]
        _env_file=None,
        signer_json="{not json",
        contracts_mail=CONTRACTS_MAIL,
        documenso_webhook_secret=SECRET,
    )
    client.app.dependency_overrides[get_settings] = lambda: broken  # type: ignore[attr-defined]
    documenso.reject(envelope, "Il domicilio non è raggiungibile")

    answered = _deliver(client, documenso.webhook(envelope, "DOCUMENT_REJECTED"))

    assert answered.status_code == 200
    quadro = _document(api_session, "quadro")
    assert (quadro.stato, quadro.cancel_reason) == (
        "annullato",
        "Rifiutato dal freelance: Il domicilio non è raggiungibile",
    )


def test_an_exception_inside_apply_is_swallowed_into_a_200(
    client: TestClient,
    admin: None,
    sender: RecordingSender,
    documenso: FakeDocumenso,
    api_session: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """REB-391: the webhook answers 200 for everything past the secret check, so
    Documenso never retries an event the hub already has -- even one `apply` itself
    fails on (a `NotFound`, a database error). The failure is logged, naming the
    envelope, and left for «Aggiorna stato» to recover."""
    # `api_engine`'s `upgrade_to_head` runs Alembic's own `env.py`, whose `fileConfig`
    # disables every logger not in `alembic.ini`'s own `[loggers]` list -- this module's
    # among them (the same trap `test_member_api.py` documents). Undo it here so
    # `caplog` can see what this test is about.
    logging.getLogger("rebase_api.routers.documenso").disabled = False
    _match, envelope = _sent(client, sender, api_session)

    class RaisingSigning:
        def apply(self, outcome: Outcome) -> UUID | None:
            raise NotFound("documento", envelope)

    broken: Callable[[Session], RaisingSigning] = lambda session: RaisingSigning()  # noqa: E731
    client.app.dependency_overrides[get_signing_factory] = lambda: broken  # type: ignore[attr-defined]

    with caplog.at_level(logging.ERROR):
        answered = _deliver(client, documenso.webhook(envelope, "DOCUMENT_COMPLETED"))

    assert answered.status_code == 200 and answered.json() == {"ok": True}
    assert any(envelope in record.getMessage() for record in caplog.records)
    assert _document(api_session, "quadro").stato == "inviato"
