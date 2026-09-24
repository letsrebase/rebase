"""REB-387 phase 3 over HTTP: «Invia per la firma» and the actions that follow it, with a
Documenso that lives in a dict and a mailbox that keeps what it gets."""

import json
from collections.abc import Iterator

import pytest
from contract_flow import ADMIN_EMAIL, MISSING, SIGNER, TABLES, draft_match
from fakes_contracts import FakeRenderer
from fakes_documenso import FakeDocumenso
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from rebase_api.deps import get_documenso, get_renderer
from rebase_core.config import Settings, get_settings
from rebase_core.mail import RecordingSender
from rebase_core.models import User

CONTRACTS_MAIL = "contratti@rebase.test"


@pytest.fixture
def renderer(client: TestClient) -> Iterator[FakeRenderer]:
    fake = FakeRenderer(draft=False)
    client.app.dependency_overrides[get_renderer] = lambda: fake  # type: ignore[attr-defined]
    yield fake


@pytest.fixture
def documenso(client: TestClient) -> Iterator[FakeDocumenso]:
    fake = FakeDocumenso()
    client.app.dependency_overrides[get_documenso] = fake.client  # type: ignore[attr-defined]
    yield fake


@pytest.fixture
def admin(client: TestClient, api_session: Session) -> Iterator[None]:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, signer_json=json.dumps(SIGNER), contracts_mail=CONTRACTS_MAIL
    )
    client.app.dependency_overrides[get_settings] = lambda: settings  # type: ignore[attr-defined]
    api_session.add(User(email=ADMIN_EMAIL, nome="Ivan", cognome="", role="admin"))
    api_session.commit()
    yield
    api_session.rollback()
    for table in TABLES:
        api_session.execute(text(f"DELETE FROM {table}"))
    api_session.commit()


def test_without_the_cookie_the_send_is_a_401(client: TestClient, admin: None) -> None:
    assert client.post(f"/api/hub/matches/{MISSING}/send").status_code == 401


def test_the_send_hands_documenso_the_framework_and_mails_one_link(
    client: TestClient,
    admin: None,
    sender: RecordingSender,
    renderer: FakeRenderer,
    documenso: FakeDocumenso,
) -> None:
    match = draft_match(client, sender)
    before = len(sender.sent)

    sent = client.post(f"/api/hub/matches/{match['id']}/send")

    assert sent.status_code == 200, sent.text
    report = sent.json()
    assert (report["inviato"], report["mail_inviata"]) == ("quadro", True)
    assert report["match"]["stato"] == "in_firma"
    assert report["match"]["lettera"]["stato"] == "in_attesa"
    [payload] = documenso.created()
    assert payload["meta"]["distributionMethod"] == "NONE"
    assert not any(payload["meta"]["emailSettings"].values())
    assert payload["recipients"][0]["email"] == "ada@studio.it"
    [mail] = sender.sent[before:]
    assert mail.subject == "Da firmare: contratto quadro rebase"
    assert "https://firma.letsrebase.test/sign/" in mail.text
    page = client.get(f"/api/hub/freelancers/{match['freelancer_id']}/matches")
    assert page.json()["quadro"]["stato"] == "inviato"
    # The recipient's token is the signing URL's secret half: no admin page reads it.
    assert "firma.letsrebase.test/sign" not in page.text
    assert "777.77" not in sent.text


def test_without_documenso_the_send_says_signing_is_off(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    client.app.dependency_overrides[get_documenso] = lambda: None  # type: ignore[attr-defined]
    match = draft_match(client, sender)
    answered = client.post(f"/api/hub/matches/{match['id']}/send")
    assert answered.status_code == 503
    assert "non è attiva" in answered.json()["detail"]
    assert client.get(f"/api/hub/matches/{match['id']}").json()["stato"] == "bozza"


def test_a_malformed_signer_setting_503s_the_send_naming_it(
    client: TestClient,
    admin: None,
    sender: RecordingSender,
    renderer: FakeRenderer,
    documenso: FakeDocumenso,
) -> None:
    """Left over from Task 2's review: `REBASE_SIGNER_JSON` parses lazily, only where a
    document is about to be typeset (REB-406 fix round 1, I1), and the send is exactly
    that path -- a malformed value 503s it, naming the setting."""
    match = draft_match(client, sender)
    client.app.dependency_overrides[get_settings] = lambda: Settings(  # type: ignore[attr-defined,call-arg]
        _env_file=None, signer_json="{not json", contracts_mail=CONTRACTS_MAIL
    )

    answered = client.post(f"/api/hub/matches/{match['id']}/send")

    assert answered.status_code == 503
    assert "REBASE_SIGNER_JSON" in answered.json()["detail"]
    assert client.get(f"/api/hub/matches/{match['id']}").json()["stato"] == "bozza"


def test_a_draft_text_never_leaves(
    client: TestClient, admin: None, sender: RecordingSender, documenso: FakeDocumenso
) -> None:
    draft = FakeRenderer(draft=True)
    client.app.dependency_overrides[get_renderer] = lambda: draft  # type: ignore[attr-defined]
    match = draft_match(client, sender)
    answered = client.post(f"/api/hub/matches/{match['id']}/send")
    assert answered.status_code == 409
    assert "ancora una bozza" in answered.json()["detail"]
    assert documenso.created() == []


def test_documenso_refusing_is_a_502_with_its_sentence_alone(
    client: TestClient,
    admin: None,
    sender: RecordingSender,
    renderer: FakeRenderer,
    documenso: FakeDocumenso,
) -> None:
    match = draft_match(client, sender)
    documenso.fail("create", 400, "Invalid PDF")
    answered = client.post(f"/api/hub/matches/{match['id']}/send")
    assert answered.status_code == 502
    assert answered.json() == {"detail": "Documenso ha rifiutato la richiesta: Invalid PDF"}
    page = client.get(f"/api/hub/freelancers/{match['freelancer_id']}/matches").json()
    assert page["quadro"]["stato"] == "generato"
    assert page["matches"][0]["stato"] == "bozza"


def test_a_soft_deleted_freelancers_match_cannot_be_sent(
    client: TestClient,
    admin: None,
    sender: RecordingSender,
    renderer: FakeRenderer,
    documenso: FakeDocumenso,
    api_session: Session,
) -> None:
    """REB-406 controller ruling: the send route checks `_require_live_freelancer`
    exactly as `cancel_match` already does, before calling the service. REB-406 fix
    round 1, M1: the service itself would eventually answer a NotFound too (once
    `_dispatch` reads the freelancer), so the exact sentence -- naming the match, the
    guard's own entity, not the freelancer -- is what proves the guard, not the
    service's own lookup, is what actually stopped this."""
    match = draft_match(client, sender)
    api_session.execute(
        text("UPDATE freelancers SET deleted_at = now() WHERE id = :id"),
        {"id": match["freelancer_id"]},
    )
    api_session.commit()

    answered = client.post(f"/api/hub/matches/{match['id']}/send")

    assert answered.status_code == 404
    assert answered.json() == {"detail": f"match {match['id']} non trovato"}
    assert documenso.created() == []
