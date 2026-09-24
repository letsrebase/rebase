"""The send surface, over real HTTP -- and the shape of the gap beside it.

`POST /api/email-drafts/{id}/send` is the only endpoint in the product that sends an
email, and it deliberately has **no** MCP counterpart. That gap is asserted from the
other side, in `apps/mcp/tests/test_gmail_tools.py`; what this file asserts is the half
that lives here:

  * the endpoint exists, and only a write-capable actor reaches it;
  * an `incerto` draft is refused with a sentence that says «verifica», never one that
    claims the message went -- the whole of spec 6.3(b) as a person reads it;
  * `reconcile` is reachable on its own, because an unknown outcome is resolved by
    asking Gmail and never by sending again;
  * a `fallito` draft is editable, and editing it clears the stale error (spec 6.3(a):
    the composer reopens with the text inside);
  * nothing on this surface accepts an uploaded file. Attachments come from
    `document_versions` (spec 6.4), and a `multipart/form-data` body anywhere under
    `/api/email-drafts` would be the way round that -- checked against the generated
    OpenAPI document so a route added later cannot slip one in.

**Not one test here opens a socket.** Every path that would reach Google is refused
before the request: `send` gates on the account, and the two tests that get past that
gate are stopped by the draft's own state. The repository-root socket guard is what
turns that from an intention into a fact.
"""

from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pigrocrm.core.config import Settings, get_settings
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.gmail.models import EmailDraft, GoogleAccount
from pigrocrm.core.gmail.schemas import REQUESTED_SCOPES

# 32 bytes of "k", base64. Literal rather than imported from `fakes.gmail_fixtures`:
# that package lives under `packages/core/tests`, which is only on `sys.path` once a
# test from that root has been collected -- importing it would make this file pass in a
# full run and fail when run on its own.
TOKEN_KEY_B64 = "a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s="

DRAFTS = "/api/email-drafts"


def _configured() -> Settings:
    """The suite's own settings with the Gmail variables filled in. A copy, not a fresh
    `Settings(...)`: `jwt_secret` is what the login cookie was signed with, and an
    override that invented its own would answer 401 everywhere."""
    return get_settings().model_copy(
        update={
            "google_client_id": "cid.apps.googleusercontent.com",
            "google_client_secret": "il-segreto-del-client",
            "google_token_key": TOKEN_KEY_B64,
            "public_url": "https://crm.example.it",
        }
    )


@pytest.fixture
def gmail_ready(client: TestClient) -> Iterator[TestClient]:
    client.app.dependency_overrides[get_settings] = _configured
    yield client
    client.app.dependency_overrides.pop(get_settings, None)


@pytest.fixture
def customer_id(api_session: Session) -> str:
    customer = Customer(ragione_sociale="Acme S.r.l.", email="ada@acme.it")
    api_session.add(customer)
    api_session.flush()
    return str(customer.id)


def _create(client: TestClient, customer_id: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "entity_type": "customer",
        "entity_id": customer_id,
        "to_addresses": ["ada@acme.it"],
        "subject": "Accompagnamento offerta",
        "body_markdown": "Gentile Ada,\n\nin allegato l'offerta.",
        **overrides,
    }
    response = client.post(DRAFTS, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _row_draft(session: Session, customer_id: str) -> EmailDraft:
    """A draft written straight to the database, for the tests whose actor is `readonly`.

    `readonly_client` logs in over the same `TestClient`, replacing the cookie, and pytest
    resolves every fixture before the test body runs -- so "create it as admin, then act as
    readonly" is not expressible through the API here. The row is what those tests need
    anyway: what is being refused is the actor, not the row.
    """
    draft = EmailDraft(
        entity_type="customer",
        entity_id=customer_id,
        to_addresses=["ada@acme.it"],
        cc_addresses=[],
        subject="Accompagnamento offerta",
        body_markdown="Gentile Ada,",
        attachment_version_ids=[],
        message_id_header=f"<{uuid4()}@crm.example.it>",
        send_state="bozza",
    )
    session.add(draft)
    session.flush()
    return draft


def _set_state(session: Session, draft_id: str, state: str, **columns: Any) -> EmailDraft:
    """Straight onto the row. `send_state` is a fact about what happened, never an input:
    no schema on this surface accepts it, and the only writer is `EmailSendService`."""
    row = session.get(EmailDraft, draft_id)
    assert row is not None
    row.send_state = state
    for name, value in columns.items():
        setattr(row, name, value)
    session.flush()
    return row


def _connect(session: Session, user_id: Any) -> GoogleAccount:
    """A mailbox in the state a user reaches after consenting. The token bytes are opaque:
    nothing here decrypts them, and no test below gets far enough to try."""
    account = GoogleAccount(
        user_id=user_id,
        google_sub="sub-123",
        email_address="io@example.it",
        refresh_token_ciphertext=b"\x01\x02ciphertext",
        refresh_token_nonce=b"\x03\x04nonce",
        scopes_granted=list(REQUESTED_SCOPES),
        status="active",
    )
    session.add(account)
    session.flush()
    return account


# --- the ordinary surface -------------------------------------------------------------


def test_a_draft_is_created_read_listed_edited_and_discarded(
    logged_in: TestClient, customer_id: str
) -> None:
    created = _create(logged_in, customer_id)
    assert created["send_state"] == "bozza"
    # Minted at creation, before anything is sent -- that is what makes an unknown
    # outcome resolvable by an exact lookup later (spec 6.2).
    assert created["message_id_header"]

    draft_id = created["id"]
    assert logged_in.get(f"{DRAFTS}/{draft_id}").json()["subject"] == "Accompagnamento offerta"

    listed = logged_in.get(DRAFTS, params={"entity_id": customer_id}).json()
    assert [item["id"] for item in listed["items"]] == [draft_id]
    assert listed["total"] == 1
    # The Email tab's own read (REB-415): a draft that has not left is in it.
    unsent = logged_in.get(DRAFTS, params={"entity_id": customer_id, "unsent": "true"}).json()
    assert [item["id"] for item in unsent["items"]] == [draft_id]

    patched = logged_in.patch(f"{DRAFTS}/{draft_id}", json={"subject": "Offerta rivista"})
    assert patched.status_code == 200, patched.text
    assert patched.json()["subject"] == "Offerta rivista"

    assert logged_in.delete(f"{DRAFTS}/{draft_id}").status_code == 204
    assert logged_in.get(f"{DRAFTS}/{draft_id}").status_code == 404


def test_a_readonly_actor_cannot_create_a_draft(
    readonly_client: TestClient, customer_id: str
) -> None:
    response = readonly_client.post(
        DRAFTS,
        json={
            "entity_type": "customer",
            "entity_id": customer_id,
            "to_addresses": ["ada@acme.it"],
            "subject": "Offerta",
            "body_markdown": "Testo",
        },
    )
    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"


def test_a_draft_filed_against_a_row_of_the_wrong_kind_is_a_404(logged_in: TestClient) -> None:
    """`entity_id` carries no foreign key -- it points at one of three tables -- so the
    service checks it against the *right* one. Over HTTP that has to be a problem
    document naming the entity, not an IntegrityError from the driver."""
    response = logged_in.post(
        DRAFTS,
        json={
            "entity_type": "customer",
            "entity_id": str(uuid4()),
            "to_addresses": ["ada@acme.it"],
            "subject": "Offerta",
            "body_markdown": "Testo",
        },
    )
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


# --- the send, and the state that is neither yes nor no ---------------------------------


def test_send_exists_as_an_endpoint(
    gmail_ready: TestClient, logged_in: TestClient, customer_id: str
) -> None:
    """And deliberately not as an MCP tool. That gap between the two surfaces is the
    design, not an omission to fill in later.

    409 here, and it is the true answer: this installation has Google configured and no
    mailbox connected, so the gate refuses before anything is composed and before any
    request is made -- `GoogleAccountService` holds no transport to make one with.
    """
    draft = _create(logged_in, customer_id)
    response = logged_in.post(f"{DRAFTS}/{draft['id']}/send")

    assert response.status_code == 409, response.text
    assert "nessuna casella Google collegata" in response.json()["detail"]


def test_a_readonly_actor_cannot_send(
    gmail_ready: TestClient,
    readonly_client: TestClient,
    customer_id: str,
    api_session: Session,
) -> None:
    """The draft is a real, sendable row; what is refused is the actor.

    Asserted with Gmail configured, because the order inside `EmailSendService.send`
    matters: `require_gmail_configured` runs first, so on an unconfigured installation
    this would answer 409 and prove nothing about the role.
    """
    draft = _row_draft(api_session, customer_id)

    response = readonly_client.post(f"{DRAFTS}/{draft.id}/send")

    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"
    # And nothing moved: a refused actor must not leave a claimed draft behind.
    api_session.refresh(draft)
    assert draft.send_state == "bozza"
    assert draft.send_attempted_at is None


def test_an_uncertain_draft_answers_409_and_the_body_says_verify_not_sent(
    gmail_ready: TestClient,
    logged_in: TestClient,
    customer_id: str,
    api_session: Session,
    admin_user: Any,
) -> None:
    """Spec 6.3(b) as a person reads it. `incerto` means nobody knows whether the message
    left, so the refusal must send them to «verifica» -- and must not assert either
    outcome. Asserting that the sentence does not say «inviata» is the half that matters:
    a refusal claiming the mail is already gone would stop somebody verifying it."""
    _connect(api_session, admin_user.id)
    draft = _create(logged_in, customer_id)
    _set_state(api_session, draft["id"], "incerto")

    response = logged_in.post(f"{DRAFTS}/{draft['id']}/send")

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert "verifica" in detail
    assert "inviata" not in detail.lower()
    assert response.json()["send_state"] == "incerto"


def test_a_sent_draft_is_refused_rather_than_sent_again(
    gmail_ready: TestClient,
    logged_in: TestClient,
    customer_id: str,
    api_session: Session,
    admin_user: Any,
) -> None:
    """The one call in the system with no idempotency key is guarded by the row, not by
    the caller. A second press answers 409 and reaches neither Gmail nor the claim."""
    _connect(api_session, admin_user.id)
    draft = _create(logged_in, customer_id)
    _set_state(api_session, draft["id"], "inviato", sent_gmail_message_id="m-1")

    response = logged_in.post(f"{DRAFTS}/{draft['id']}/send")

    assert response.status_code == 409
    assert "questa email è già stata inviata" in response.json()["detail"]


def test_sending_a_draft_that_does_not_exist_is_a_404(
    gmail_ready: TestClient, logged_in: TestClient, api_session: Session, admin_user: Any
) -> None:
    _connect(api_session, admin_user.id)
    assert logged_in.post(f"{DRAFTS}/{uuid4()}/send").status_code == 404


# --- resolving an outcome nobody knows -------------------------------------------------


def test_reconcile_is_reachable_on_its_own(
    gmail_ready: TestClient, logged_in: TestClient, customer_id: str
) -> None:
    """An unknown outcome is resolved by asking Gmail, never by sending again -- so
    «verifica» has to be an endpoint of its own rather than a branch of send.

    A `bozza` draft has no outcome to look up, so the service returns it untouched and
    without a request: this asserts the route, its serialisation and its idempotence at
    once, and the socket guard asserts the silence.
    """
    draft = _create(logged_in, customer_id)
    response = logged_in.post(f"{DRAFTS}/{draft['id']}/reconcile")

    assert response.status_code == 200, response.text
    assert response.json()["send_state"] == "bozza"
    assert response.json()["id"] == draft["id"]


def test_a_readonly_actor_cannot_verify_an_outcome(
    gmail_ready: TestClient,
    readonly_client: TestClient,
    customer_id: str,
    api_session: Session,
) -> None:
    """«verifica» is the repair half of having pressed Invia: it writes `send_state` to a
    terminal value and it spends the owner's Gmail quota. Gated like the send it repairs.

    On an `incerto` draft, so the refusal is not the early return a `bozza` draft would
    take: this is the state the button actually appears next to.
    """
    draft = _row_draft(api_session, customer_id)
    _set_state(api_session, str(draft.id), "incerto")

    response = readonly_client.post(f"{DRAFTS}/{draft.id}/reconcile")

    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"
    api_session.refresh(draft)
    assert draft.send_state == "incerto"


# --- what a failure leaves behind -------------------------------------------------------


def test_editing_a_failed_draft_reopens_it_and_clears_the_stale_error(
    logged_in: TestClient, customer_id: str, api_session: Session
) -> None:
    """Spec 6.3(a): «la bozza intatta con l'errore accanto, il composer si riapre con il
    testo dentro». Keeping `fallito` after an edit would leave an error sentence beside
    text it no longer describes, which reads as a failure that has just happened -- the
    stale-banner defect, stored in a column."""
    draft = _create(logged_in, customer_id)
    _set_state(api_session, draft["id"], "fallito", last_error="Gmail ha rifiutato il messaggio")

    response = logged_in.patch(
        f"{DRAFTS}/{draft['id']}", json={"body_markdown": "Gentile Ada,\n\ncorretto."}
    )

    assert response.status_code == 200, response.text
    assert response.json()["send_state"] == "bozza"
    assert response.json()["last_error"] is None
    # The identity survives the edit: minting a new `Message-ID` here would make a later
    # reconciliation look for a message that was never sent under that name.
    assert response.json()["message_id_header"] == draft["message_id_header"]


def test_a_draft_already_gone_cannot_be_edited_or_deleted(
    logged_in: TestClient, customer_id: str, api_session: Session
) -> None:
    draft = _create(logged_in, customer_id)
    _set_state(api_session, draft["id"], "inviato", sent_gmail_message_id="m-1")

    assert logged_in.patch(f"{DRAFTS}/{draft['id']}", json={"subject": "Altro"}).status_code == 409
    assert logged_in.delete(f"{DRAFTS}/{draft['id']}").status_code == 409


# --- what the surface must never accept --------------------------------------------------


def test_no_draft_endpoint_accepts_an_arbitrary_attachment_upload(logged_in: TestClient) -> None:
    """Spec 6.4: attachments come from `document_versions` and never from an upload.
    Checked against the generated document rather than against the routes this file
    happens to know, so a `POST /api/email-drafts/{id}/attachments` added later fails
    here instead of quietly opening the one hole the whole rule exists to close."""
    schema = logged_in.get("/openapi.json").json()
    checked = 0
    for path, operations in schema["paths"].items():
        if not path.startswith(DRAFTS):
            continue
        for operation in operations.values():
            checked += 1
            body = operation.get("requestBody", {}).get("content", {})
            assert "multipart/form-data" not in body, path
    # A sweep over zero operations is a sweep that cannot fail.
    assert checked >= 7, checked


def test_the_send_and_verify_endpoints_are_declared_in_the_openapi_document(
    logged_in: TestClient,
) -> None:
    """The SPA's client is generated from this document, so an endpoint missing from it
    is an endpoint the composer cannot call. Both are asserted because they are the two
    the rest of the slice is built around, and because «verifica» existing separately
    from «invia» is the design rather than a convenience."""
    paths = logged_in.get("/openapi.json").json()["paths"]

    assert "post" in paths["/api/email-drafts/{draft_id}/send"]
    assert "post" in paths["/api/email-drafts/{draft_id}/reconcile"]
