"""Chasing money over HTTP, and the endpoint that prepares a demand without sending it.

`POST /api/payment-reminders` writes a `payment_reminders` row and a draft, and **sends
nothing** (spec 8.3). The draft leaves through `/api/email-drafts/{id}/send` like any
other email, which is what lets spec 7.3 rely on 6.1's idempotence instead of
reimplementing it: one send path in the whole slice, and a second one is exactly where a
double send comes back.

`GET /api/payment-reminders/candidates` is a plain read of this installation's own
register -- no Google call, no quota, and it works with the mailbox disconnected. So it
is not role-gated: seeing which invoices are late is reading your own books. Preparing
the reminder is, because that writes a demand for payment in the owner's name.

The invoices here are issued through the real endpoint, not built as rows: it is the only
way to get a genuine `pdf_document_id`, and the courtesy copy the reminder attaches has to
be the artefact slice 3 stored rather than one this file invented. The due date is then
pushed into the past on the row, because `fiscal_profile.giorni_scadenza` makes every
freshly issued invoice current by construction.
"""

from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from aziende_helpers import azienda_url
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pigrocrm.core.clock import oggi_in_italia
from pigrocrm.core.gmail.models import EmailDraft, PaymentReminder
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.invoices.naming import numero_completo

REMINDERS = "/api/payment-reminders"
CANDIDATES = f"{REMINDERS}/candidates"
IBAN = "IT60X0542811101000000123456"


@pytest.fixture
def fiscal_profile(logged_in: TestClient) -> dict[str, Any]:
    """With an IBAN: a reminder with nowhere to pay is a reminder nobody can act on, and
    `SollecitiService` refuses to prepare one."""
    response = logged_in.put(
        azienda_url(logged_in, "/fiscal-profile"), json={"codice_regime": "RF19", "iban": IBAN}
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def emitter(logged_in: TestClient) -> dict[str, Any]:
    response = logged_in.put(
        azienda_url(logged_in),
        json={
            "ragione_sociale": "Studio Rossi",
            "partita_iva": "01234567890",
            "codice_fiscale": "HMCRFT00A01H501K",
            "indirizzo": "Via Vittorio Veneto 12",
            "cap": "20124",
            "comune": "Milano",
            "provincia": "MI",
            "nazione": "IT",
            "email": "mario@studiorossi.it",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def customer(logged_in: TestClient) -> dict[str, Any]:
    response = logged_in.post(
        "/api/customers",
        json={
            "ragione_sociale": "Acme S.r.l.",
            "partita_iva": "12345678901",
            "codice_sdi": "ABCDEFG",
            "email": "ada@acme.it",
            "indirizzo": "Corso Italia 5",
            "cap": "00100",
            "comune": "Roma",
            "provincia": "RM",
            "nazione": "IT",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def overdue_invoice(
    logged_in: TestClient,
    api_session: Session,
    customer: dict[str, Any],
    fiscal_profile: dict[str, Any],
    emitter: dict[str, Any],
) -> dict[str, Any]:
    """An issued invoice, forty days past its due date.

    Issued through the endpoint so it has a number, a frozen total and a real rendered
    PDF -- the artefact the reminder attaches. The due date is moved afterwards, on the
    row: a freshly issued invoice is current by construction, and the alternative would
    be a literal year in a test.
    """
    draft = logged_in.post(
        "/api/invoices",
        json={
            "customer_id": customer["id"],
            "causale": "Consulenza",
            "righe": [{"descrizione": "Consulenza", "prezzo_unitario": "1000.00"}],
        },
    )
    assert draft.status_code == 201, draft.text
    issued = logged_in.post(f"/api/invoices/{draft.json()['id']}/issue", json={})
    assert issued.status_code == 200, issued.text

    row = api_session.get(Invoice, issued.json()["id"])
    assert row is not None
    row.data_scadenza = oggi_in_italia() - timedelta(days=40)
    api_session.flush()
    return issued.json()


# --- the list ---------------------------------------------------------------------------


def test_the_candidate_list_names_the_overdue_invoice_and_its_frozen_figure(
    logged_in: TestClient, overdue_invoice: dict[str, Any]
) -> None:
    """`importo` is the invoice's own `totale`, exact and as a decimal string. A demand
    naming a figure the client's copy does not carry is a demand they are right to
    ignore."""
    response = logged_in.get(CANDIDATES)

    assert response.status_code == 200, response.text
    page = response.json()
    mine = [item for item in page["items"] if item["invoice_id"] == overdue_invoice["id"]]
    assert len(mine) == 1
    assert mine[0]["importo"] == overdue_invoice["totale"]
    assert mine[0]["cliente"] == "Acme S.r.l."
    assert mine[0]["giorni_di_ritardo"] == 40
    assert mine[0]["solleciti_inviati"] == 0
    assert mine[0]["prossimo_livello"] == 1
    # No mailbox is connected, so "has this client replied" is unknown -- and `null` says
    # unknown, which is not the same claim as "nobody replied".
    assert mine[0]["ultima_risposta_il"] is None
    assert page["total"] == len(page["items"])


def test_a_readonly_actor_may_see_who_is_late(
    logged_in: TestClient, overdue_invoice: dict[str, Any], readonly_client: TestClient
) -> None:
    """Reading your own register is not a privileged act. What is privileged is writing
    the letter, which the next test asserts from the other side."""
    response = readonly_client.get(CANDIDATES)

    assert response.status_code == 200, response.text
    assert [item["invoice_id"] for item in response.json()["items"]] == [overdue_invoice["id"]]


def test_an_invoice_still_inside_its_grace_period_is_not_a_candidate(
    logged_in: TestClient, api_session: Session, overdue_invoice: dict[str, Any]
) -> None:
    """The grace window is what separates «in ritardo» from «scaduta ieri». The previous system had
    no due date in the condition at all -- it chased on `emailSentCount > 0`."""
    row = api_session.get(Invoice, overdue_invoice["id"])
    assert row is not None
    row.data_scadenza = oggi_in_italia() - timedelta(days=1)
    api_session.flush()

    assert logged_in.get(CANDIDATES).json()["items"] == []


# --- preparing one, and sending nothing ---------------------------------------------------


def test_creating_a_reminder_prepares_a_draft_and_sends_nothing(
    logged_in: TestClient, api_session: Session, overdue_invoice: dict[str, Any]
) -> None:
    """Spec 8.3. `sent_at` is `None` on everything this endpoint creates, and the draft is
    `bozza` -- a state only `EmailSendService` can leave, and this endpoint never builds
    one. The socket guard asserts the silence: nothing here reaches Google."""
    response = logged_in.post(REMINDERS, json={"invoice_id": overdue_invoice["id"]})

    assert response.status_code == 201, response.text
    reminder = response.json()
    assert reminder["sequence"] == 1
    assert reminder["sent_at"] is None
    assert reminder["email_draft_id"] is not None

    draft = api_session.get(EmailDraft, reminder["email_draft_id"])
    assert draft is not None
    assert draft.send_state == "bozza"
    assert draft.google_account_id is None
    assert draft.to_addresses == ["ada@acme.it"]
    assert numero_completo(overdue_invoice["anno"], overdue_invoice["numero"]) in draft.subject
    # The letter carries the invoice's own frozen figure and somewhere to pay it.
    assert IBAN in draft.body_markdown
    # And it attaches the invoice's own stored PDF -- never an upload (spec 6.4) -- so
    # the sentence that promises a courtesy copy is telling the truth.
    assert len(draft.attachment_version_ids) == 1
    assert "In allegato trova copia di cortesia della fattura." in draft.body_markdown


def test_the_prepared_draft_is_sendable_only_through_the_one_send_path(
    logged_in: TestClient, overdue_invoice: dict[str, Any]
) -> None:
    """A reminder service with a send of its own would be the second send path, and the
    second path is where the double send comes back. So the reminder endpoint hands back
    a draft id and nothing else: the only way to make it leave is the endpoint every
    other email uses."""
    reminder = logged_in.post(REMINDERS, json={"invoice_id": overdue_invoice["id"]}).json()

    paths = logged_in.get("/openapi.json").json()["paths"]
    assert not [path for path in paths if path.startswith(REMINDERS) and path.endswith("/send")]
    # And the draft it made is a draft like any other, reachable on the send surface.
    assert logged_in.get(f"/api/email-drafts/{reminder['email_draft_id']}").status_code == 200


def test_a_readonly_actor_cannot_prepare_a_reminder(
    logged_in: TestClient,
    api_session: Session,
    overdue_invoice: dict[str, Any],
    readonly_client: TestClient,
) -> None:
    response = readonly_client.post(REMINDERS, json={"invoice_id": overdue_invoice["id"]})

    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"
    assert api_session.query(PaymentReminder).count() == 0


def test_a_second_reminder_inside_the_interval_is_refused(
    logged_in: TestClient, overdue_invoice: dict[str, Any]
) -> None:
    """The interval is what makes a reminder bearable, and the refusal has to name it:
    The previous system had no interval anywhere, which is how a client gets chased twice in a
    morning."""
    assert logged_in.post(REMINDERS, json={"invoice_id": overdue_invoice["id"]}).status_code == 201

    second = logged_in.post(REMINDERS, json={"invoice_id": overdue_invoice["id"]})

    assert second.status_code == 409
    assert second.json()["code"] == "conflict"
    # And the invoice leaves the list, so the page cannot offer what the endpoint refuses.
    assert logged_in.get(CANDIDATES).json()["items"] == []


def test_the_body_takes_one_field_and_refuses_a_second(
    logged_in: TestClient, overdue_invoice: dict[str, Any]
) -> None:
    """Everything the letter says is derived from the register. A field that let a caller
    name the amount would produce a demand the client's own copy of the invoice does not
    support -- so `extra="forbid"` makes it a 422 rather than a silently ignored key."""
    response = logged_in.post(
        REMINDERS, json={"invoice_id": overdue_invoice["id"], "importo": "1.00"}
    )

    assert response.status_code == 422


def test_a_reminder_for_an_invoice_that_does_not_exist_is_a_404(
    logged_in: TestClient, overdue_invoice: dict[str, Any]
) -> None:
    response = logged_in.post(REMINDERS, json={"invoice_id": str(uuid4())})

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"
