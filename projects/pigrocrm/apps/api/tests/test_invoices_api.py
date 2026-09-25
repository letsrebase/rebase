"""The HTTP surface. Role enforcement lives in the services (`actor.require_admin`),
so these tests assert the status codes and the problem documents that come out of it,
not a router-level dependency that does not exist in this codebase.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from pigrocrm.core.clock import oggi_in_italia
from pigrocrm.core.invoices import pdf as invoice_pdf
from pigrocrm.core.invoices.service import InvoiceService

# `oggi_in_italia()`, not `date.today()`: `issue` and `mark_transmitted_externally`
# both compare against Europe/Rome's own calendar (see `clock.py`), and the two can
# disagree with the process's own system timezone -- exactly the class of defect that
# module exists to close. A bare `date.today()` here made
# `test_a_transmitted_invoice_refuses_annulment_and_says_where_to_go` flake with a 422
# ("una consegna non si registra con data futura") whenever the two clocks disagreed.
TODAY = oggi_in_italia().isoformat()

COLLABORATORE_EMAIL = "collaboratore-dual@pigro.it"
COLLABORATORE_PASSWORD = "supersegreta1"


def _second_actor(admin_client: TestClient, ruolo: str) -> TestClient:
    """A genuinely independent session, not `admin_client`'s own cookie jar.

    `logged_in` and `collaborator_client` (`apps/api/tests/conftest.py`) both build
    their `TestClient` from the same function-scoped `client` fixture, so requesting
    both in one test leaves exactly one login active on that shared cookie jar --
    whichever fixture's own `/api/auth/login` call happened to resolve last -- for
    every call made under *either* fixture's name for the rest of the test. Confirmed
    directly: with both requested together, `GET /api/auth/me` returns the same
    identity under both variables.

    `TestClient(admin_client.app)` is the fix already established in this suite
    (`test_auth_api.py`'s `bare = TestClient(logged_in.app)`): a fresh cookie jar over
    the same ASGI app, and therefore the same database session and storage overrides,
    so the two identities can be exercised concurrently within a single test.
    """
    email = f"{ruolo}-{id(admin_client)}@pigro.it"
    created = admin_client.post(
        "/api/users",
        json={"email": email, "password": COLLABORATORE_PASSWORD, "nome": "Test", "ruolo": ruolo},
    )
    assert created.status_code == 201, created.text
    client = TestClient(admin_client.app, base_url="https://testserver")
    response = client.post(
        "/api/auth/login", json={"email": email, "password": COLLABORATORE_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return client


@pytest.fixture
def fiscal_profile(logged_in: TestClient) -> dict[str, Any]:
    response = logged_in.put("/api/fiscal-profile", json={"codice_regime": "RF19"})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def emitter(logged_in: TestClient) -> dict[str, Any]:
    response = logged_in.put(
        "/api/emitter",
        json={
            "ragione_sociale": "Studio Rossi",
            "partita_iva": "01234567890",
            "codice_fiscale": "HMCRFT00A01H501K",
            "indirizzo": "Via Vittorio Veneto 12",
            "cap": "20124",
            "comune": "Milano",
            "provincia": "MI",
            "nazione": "IT",
            "email": "mario@example.com",
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
            "indirizzo": "Corso Italia 5",
            "cap": "00100",
            "comune": "Roma",
            "provincia": "RM",
            "nazione": "IT",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _draft(client: TestClient, customer_id: str, prezzo: str = "1000.00") -> dict[str, Any]:
    response = client.post(
        "/api/invoices",
        json={
            "customer_id": customer_id,
            "causale": "Consulenza",
            "righe": [{"descrizione": "Consulenza", "prezzo_unitario": prezzo}],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_the_fiscal_profile_round_trips(
    logged_in: TestClient, fiscal_profile: dict[str, Any]
) -> None:
    assert fiscal_profile["codice_regime"] == "RF19"
    assert fiscal_profile["soglia_bollo"] == "77.47"
    assert logged_in.get("/api/fiscal-profile").json()["codice_regime"] == "RF19"


def test_reading_a_missing_fiscal_profile_is_a_problem_document(
    logged_in: TestClient,
) -> None:
    response = logged_in.get("/api/fiscal-profile")
    if response.status_code == 404:
        assert response.headers["content-type"].startswith("application/problem+json")
        assert response.json()["code"] == "not_found"


def test_only_an_admin_may_write_the_fiscal_profile(collaborator_client: TestClient) -> None:
    response = collaborator_client.put("/api/fiscal-profile", json={"codice_regime": "RF19"})
    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"


def test_creating_a_draft_returns_201_with_no_number(
    logged_in: TestClient, customer: dict[str, Any], fiscal_profile: dict[str, Any]
) -> None:
    draft = _draft(logged_in, customer["id"])
    assert draft["stato"] == "bozza"
    assert draft["numero"] is None
    assert draft["totale"] == "1000.00"


def test_replacing_the_lines_recomputes_the_total(
    logged_in: TestClient, customer: dict[str, Any], fiscal_profile: dict[str, Any]
) -> None:
    draft = _draft(logged_in, customer["id"])
    response = logged_in.put(
        f"/api/invoices/{draft['id']}/lines",
        json={"righe": [{"descrizione": "Altro", "prezzo_unitario": "250.00"}]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["totale"] == "250.00"
    lines = logged_in.get(f"/api/invoices/{draft['id']}/lines").json()
    assert [line["numero_linea"] for line in lines] == [1]


def test_issuing_assigns_a_number_and_produces_both_artefacts(
    logged_in: TestClient,
    customer: dict[str, Any],
    fiscal_profile: dict[str, Any],
    emitter: dict[str, Any],
) -> None:
    draft = _draft(logged_in, customer["id"])
    response = logged_in.post(f"/api/invoices/{draft['id']}/issue", json={})
    assert response.status_code == 200, response.text
    issued = response.json()
    assert issued["stato"] == "emessa"
    assert issued["numero"] == 1
    assert issued["anno"] == oggi_in_italia().year
    # The row is read back after the render (REB-143), so the answer already says both
    # files exist and the web has no reason to render them a second time.
    assert issued["pdf_document_id"] is not None
    assert issued["xml_document_id"] is not None

    pdf = logged_in.get(f"/api/invoices/{issued['id']}/pdf")
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert "attachment; filename*=UTF-8''" in pdf.headers["content-disposition"]
    assert pdf.headers["x-content-type-options"] == "nosniff"

    xml = logged_in.get(f"/api/invoices/{issued['id']}/xml")
    assert xml.status_code == 200
    assert xml.headers["content-type"] == "application/xml"
    assert b"FatturaElettronica" in xml.content
    assert "IT" in xml.headers["content-disposition"]


def test_the_invoices_pdf_document_refuses_an_xml_version_with_a_422(
    logged_in: TestClient,
    customer: dict[str, Any],
    fiscal_profile: dict[str, Any],
    emitter: dict[str, Any],
) -> None:
    """REB-480, the reproduction of REB-463 at the HTTP surface: an XHTML file typed
    `application/xml`, posted as a new version of the invoice's PDF document, used to
    answer 201 and then came back from `GET /pdf` as `application/xml`. It is a 422 in
    Italian now, the PDF the invoice had is still what it serves, and a PDF still goes
    in."""
    draft = _draft(logged_in, customer["id"])
    issued = logged_in.post(f"/api/invoices/{draft['id']}/issue", json={}).json()
    document_id = issued["pdf_document_id"]
    before = logged_in.get(f"/api/invoices/{issued['id']}/pdf").content

    refused = logged_in.post(
        f"/api/documents/{document_id}/versions",
        files={
            "file": (
                "fattura.xml",
                b'<html xmlns="http://www.w3.org/1999/xhtml"><body>ciao</body></html>',
                "application/xml",
            )
        },
    )
    assert refused.status_code == 422, refused.text
    assert refused.headers["content-type"].startswith("application/problem+json")
    body = refused.json()
    assert body["code"] == "validation_failed"
    assert body["field"] == "content_type"
    assert body["expected"] == "application/pdf"
    assert "PDF di una fattura" in body["detail"]
    pdf = logged_in.get(f"/api/invoices/{issued['id']}/pdf")
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content == before

    accepted = logged_in.post(
        f"/api/documents/{document_id}/versions",
        files={"file": ("fattura.pdf", b"%PDF-1.7\nfinto\n", "application/pdf")},
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["numero"] == 2
    pdf = logged_in.get(f"/api/invoices/{issued['id']}/pdf")
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content == b"%PDF-1.7\nfinto\n"


def test_a_render_that_raises_after_the_commit_still_answers_the_issued_row(
    logged_in: TestClient,
    customer: dict[str, Any],
    fiscal_profile: dict[str, Any],
    emitter: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REB-143: `issue` commits, then the PDF render raises. The number is consumed and
    the invoice is a fiscal fact, so the answer is 200 with the issued row and no
    document ids, never the 500 that told the person the emission failed."""

    def typst_crashed(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("typst crashed")

    draft = _draft(logged_in, customer["id"])
    with monkeypatch.context() as patch:
        patch.setattr(invoice_pdf, "render_invoice_pdf", typst_crashed)
        response = logged_in.post(f"/api/invoices/{draft['id']}/issue", json={})
        assert response.status_code == 200, response.text
        issued = response.json()
        assert issued["id"] == draft["id"]
        assert issued["stato"] == "emessa"
        assert issued["numero"] == 1
        assert issued["pdf_document_id"] is None
        assert issued["xml_document_id"] is None

        # What the server answers from now on agrees with the response: the invoice is
        # issued, and the file that was never produced is a 404, not an empty download.
        assert logged_in.get(f"/api/invoices/{issued['id']}").json()["stato"] == "emessa"
        assert logged_in.get(f"/api/invoices/{issued['id']}/pdf").status_code == 404

    # And «Rigenera documenti» is the retry: once the render works, both files appear
    # under the same number.
    retried = logged_in.post(f"/api/invoices/{issued['id']}/artifacts")
    assert retried.status_code == 200, retried.text
    assert sorted(artifact["kind"] for artifact in retried.json()) == ["pdf", "xml"]
    again = logged_in.get(f"/api/invoices/{issued['id']}").json()
    assert again["numero"] == 1
    assert again["pdf_document_id"] is not None
    assert again["xml_document_id"] is not None


def test_an_xml_export_that_breaks_the_transaction_leaves_the_pdf_it_already_committed(
    logged_in: TestClient,
    customer: dict[str, Any],
    fiscal_profile: dict[str, Any],
    emitter: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The half-way case: `produce_artifacts` commits the PDF before it exports the XML,
    so a failure there leaves one file, and the answer names exactly that one.

    The failure is a statement PostgreSQL refuses, so the transaction is aborted when the
    endpoint catches it: without the rollback, the read-back would answer 500 on
    `InFailedSqlTransaction` and the person would see the failure this card removes."""

    def aborts_the_transaction(self: InvoiceService, *_args: object, **_kwargs: object) -> None:
        self.session.execute(text("SELECT 1/0"))

    monkeypatch.setattr(InvoiceService, "export_xml", aborts_the_transaction)
    draft = _draft(logged_in, customer["id"])
    response = logged_in.post(f"/api/invoices/{draft['id']}/issue", json={})
    assert response.status_code == 200, response.text
    issued = response.json()
    assert issued["stato"] == "emessa"
    assert issued["pdf_document_id"] is not None
    assert issued["xml_document_id"] is None
    assert logged_in.get(f"/api/invoices/{issued['id']}/pdf").status_code == 200
    assert logged_in.get(f"/api/invoices/{issued['id']}/xml").status_code == 404


def test_a_collaboratore_cannot_issue(
    logged_in: TestClient,
    customer: dict[str, Any],
    fiscal_profile: dict[str, Any],
    emitter: dict[str, Any],
) -> None:
    draft = _draft(logged_in, customer["id"])
    collaboratore = _second_actor(logged_in, "collaboratore")
    response = collaboratore.post(f"/api/invoices/{draft['id']}/issue", json={})
    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"


def test_a_collaboratore_may_record_a_payment(
    logged_in: TestClient,
    customer: dict[str, Any],
    fiscal_profile: dict[str, Any],
    emitter: dict[str, Any],
) -> None:
    draft = _draft(logged_in, customer["id"])
    issued = logged_in.post(f"/api/invoices/{draft['id']}/issue", json={}).json()
    collaboratore = _second_actor(logged_in, "collaboratore")
    response = collaboratore.patch(
        f"/api/invoices/{issued['id']}/payment",
        json={"stato_pagamento": "incassato", "data_incasso": TODAY},
    )
    assert response.status_code == 200, response.text
    assert response.json()["stato_pagamento"] == "incassato"


def test_deleting_an_issued_invoice_is_a_409(
    logged_in: TestClient,
    customer: dict[str, Any],
    fiscal_profile: dict[str, Any],
    emitter: dict[str, Any],
) -> None:
    """Criterion 4: `DELETE` is refused by the API."""
    draft = _draft(logged_in, customer["id"])
    issued = logged_in.post(f"/api/invoices/{draft['id']}/issue", json={}).json()
    response = logged_in.delete(f"/api/invoices/{issued['id']}")
    assert response.status_code == 409
    assert response.json()["code"] == "conflict"


def test_deleting_a_draft_is_204(
    logged_in: TestClient, customer: dict[str, Any], fiscal_profile: dict[str, Any]
) -> None:
    draft = _draft(logged_in, customer["id"])
    assert logged_in.delete(f"/api/invoices/{draft['id']}").status_code == 204


def test_annul_then_reissue_is_the_correction_route(
    logged_in: TestClient,
    customer: dict[str, Any],
    fiscal_profile: dict[str, Any],
    emitter: dict[str, Any],
) -> None:
    first = logged_in.post(
        f"/api/invoices/{_draft(logged_in, customer['id'])['id']}/issue", json={}
    ).json()
    annulled = logged_in.post(
        f"/api/invoices/{first['id']}/annul", json={"motivo": "importo errato"}
    )
    assert annulled.status_code == 200, annulled.text
    assert annulled.json()["stato"] == "annullata"
    assert annulled.json()["numero"] == 1

    second = logged_in.post(
        f"/api/invoices/{_draft(logged_in, customer['id'])['id']}/issue", json={}
    ).json()
    assert second["numero"] == 2


def test_a_transmitted_invoice_refuses_annulment_and_says_where_to_go(
    logged_in: TestClient,
    customer: dict[str, Any],
    fiscal_profile: dict[str, Any],
    emitter: dict[str, Any],
) -> None:
    issued = logged_in.post(
        f"/api/invoices/{_draft(logged_in, customer['id'])['id']}/issue", json={}
    ).json()
    assert (
        logged_in.post(
            f"/api/invoices/{issued['id']}/transmitted", json={"data": TODAY}
        ).status_code
        == 200
    )
    response = logged_in.post(
        f"/api/invoices/{issued['id']}/annul", json={"motivo": "importo errato"}
    )
    assert response.status_code == 409
    assert "nota di credito" in response.json()["detail"]


def test_a_proforma_refuses_to_produce_xml(
    logged_in: TestClient,
    customer: dict[str, Any],
    fiscal_profile: dict[str, Any],
    emitter: dict[str, Any],
) -> None:
    proforma = logged_in.post(
        "/api/invoices",
        json={
            "customer_id": customer["id"],
            "tipo": "proforma",
            "righe": [{"descrizione": "Consulenza", "prezzo_unitario": "500.00"}],
        },
    ).json()
    logged_in.post(f"/api/invoices/{proforma['id']}/confirm", json={})
    artifacts = logged_in.post(f"/api/invoices/{proforma['id']}/artifacts", json={})
    assert artifacts.status_code == 200, artifacts.text
    assert [a["kind"] for a in artifacts.json()] == ["pdf"]
    response = logged_in.get(f"/api/invoices/{proforma['id']}/xml")
    assert response.status_code in (404, 409)
    assert response.json()["code"] in ("not_found", "conflict")


def test_converting_a_confirmed_proforma_creates_a_new_numbered_row(
    logged_in: TestClient,
    customer: dict[str, Any],
    fiscal_profile: dict[str, Any],
    emitter: dict[str, Any],
) -> None:
    proforma = logged_in.post(
        "/api/invoices",
        json={
            "customer_id": customer["id"],
            "tipo": "proforma",
            "righe": [{"descrizione": "Consulenza", "prezzo_unitario": "500.00"}],
        },
    ).json()
    logged_in.post(f"/api/invoices/{proforma['id']}/confirm", json={})
    issued = logged_in.post(f"/api/invoices/{proforma['id']}/issue", json={}).json()
    assert issued["id"] != proforma["id"]
    assert issued["origine_proforma_id"] == proforma["id"]
    assert logged_in.get(f"/api/invoices/{proforma['id']}").json()["stato"] == "consumata"


def test_a_refusal_names_the_field_in_the_problem_document(
    logged_in: TestClient, fiscal_profile: dict[str, Any], emitter: dict[str, Any]
) -> None:
    """Criterion 9: an RFC 9457 problem document with `entity` and `field` populated,
    which is what `fieldErrorFrom` in the web client reads."""
    customer = logged_in.post(
        "/api/customers",
        json={
            "ragione_sociale": "Senza recapito",
            "nazione": "IT",
            "cap": "00100",
            "comune": "Roma",
            "provincia": "RM",
            "indirizzo": "Via Roma 1",
        },
    ).json()
    draft = _draft(logged_in, customer["id"])
    response = logged_in.post(f"/api/invoices/{draft['id']}/issue", json={})
    assert response.status_code == 422
    body = response.json()
    assert response.headers["content-type"].startswith("application/problem+json")
    assert body["entity"] == "customer"
    assert body["field"] == "codice_sdi"
    assert body["expected"]


def test_the_list_filters_and_paginates(
    logged_in: TestClient, customer: dict[str, Any], fiscal_profile: dict[str, Any]
) -> None:
    for _ in range(3):
        _draft(logged_in, customer["id"])
    page = logged_in.get("/api/invoices", params={"limit": 2}).json()
    assert len(page["items"]) == 2
    assert page["next_cursor"]
    filtered = logged_in.get("/api/invoices", params={"tipo": "proforma"}).json()
    assert filtered["items"] == []


def test_the_list_answers_the_fattura_a_consumed_proforma_became(
    logged_in: TestClient,
    customer: dict[str, Any],
    fiscal_profile: dict[str, Any],
    emitter: dict[str, Any],
) -> None:
    """After «Emetti» on a proforma the numbered row is a different one (spec 5), and the
    consumed proforma's page needs a way to it: `?origine_proforma_id=` answers exactly
    that row (ORB-134)."""
    proforma = logged_in.post(
        "/api/invoices",
        json={
            "customer_id": customer["id"],
            "tipo": "proforma",
            "righe": [{"descrizione": "Consulenza", "prezzo_unitario": "100.00"}],
        },
    ).json()
    assert logged_in.post(f"/api/invoices/{proforma['id']}/confirm").status_code == 200
    issued = logged_in.post(f"/api/invoices/{proforma['id']}/issue", json={}).json()
    assert issued["id"] != proforma["id"]
    assert issued["origine_proforma_id"] == proforma["id"]

    page = logged_in.get("/api/invoices", params={"origine_proforma_id": proforma["id"]}).json()
    assert [row["id"] for row in page["items"]] == [issued["id"]]
    assert logged_in.get("/api/invoices", params={"origine_proforma_id": "x"}).status_code == 422


def test_the_list_leaves_out_consumed_proformas_when_asked(
    logged_in: TestClient,
    customer: dict[str, Any],
    fiscal_profile: dict[str, Any],
    emitter: dict[str, Any],
) -> None:
    """The web's «Tutte» sends `escludi_consumate=true` so an issued proforma is one row,
    its fattura, and not two (ORB-169). Without the flag, and under `stato=consumata`,
    the proforma still answers."""
    proforma = logged_in.post(
        "/api/invoices",
        json={
            "customer_id": customer["id"],
            "tipo": "proforma",
            "righe": [{"descrizione": "Consulenza", "prezzo_unitario": "100.00"}],
        },
    ).json()
    assert logged_in.post(f"/api/invoices/{proforma['id']}/confirm").status_code == 200
    issued = logged_in.post(f"/api/invoices/{proforma['id']}/issue", json={}).json()

    def ids(response: Any) -> set[str]:
        return {row["id"] for row in response.json()["items"]}

    everything = ids(logged_in.get("/api/invoices"))
    assert {proforma["id"], issued["id"]} <= everything
    trimmed = ids(logged_in.get("/api/invoices", params={"escludi_consumate": "true"}))
    assert trimmed == everything - {proforma["id"]}
    consumed = ids(logged_in.get("/api/invoices", params={"stato": "consumata"}))
    assert consumed == {proforma["id"]}


def test_the_list_limit_is_bounded_at_the_http_layer(logged_in: TestClient) -> None:
    assert logged_in.get("/api/invoices", params={"limit": 201}).status_code == 422


def test_the_timeline_of_an_invoice_is_readable(
    logged_in: TestClient,
    customer: dict[str, Any],
    fiscal_profile: dict[str, Any],
    emitter: dict[str, Any],
) -> None:
    issued = logged_in.post(
        f"/api/invoices/{_draft(logged_in, customer['id'])['id']}/issue", json={}
    ).json()
    entries = logged_in.get(f"/api/invoices/{issued['id']}/timeline").json()
    assert {entry["kind"] for entry in entries} >= {"created", "issued"}
    assert {entry["actor_type"] for entry in entries} == {"user"}


def test_the_openapi_document_declares_invoice_as_an_entity_type(
    logged_in: TestClient,
) -> None:
    """The generated TypeScript client reads this; a contract change must break `tsc`."""
    schema = logged_in.get("/openapi.json").json()
    assert "/api/invoices" in schema["paths"]
    assert "InvoiceRead" in schema["components"]["schemas"]
    assert "FiscalProfileRead" in schema["components"]["schemas"]


# --- the accrual period and the proforma's own date on the wire (ORB-61, ORB-63) ------


def test_a_proforma_is_created_with_its_own_date_and_an_accrual_period(
    logged_in: TestClient, customer: dict[str, Any], fiscal_profile: dict[str, Any]
) -> None:
    """The contract the web draft editor is written against: `data_emissione`,
    `competenza_da` and `competenza_a` go in on POST and come back on every read."""
    response = logged_in.post(
        "/api/invoices",
        json={
            "customer_id": customer["id"],
            "tipo": "proforma",
            "causale": "FDE, agosto 2026",
            "data_emissione": "2026-09-05",
            "competenza_da": "2026-08-01",
            "competenza_a": "2026-08-31",
            "righe": [{"descrizione": "Consulenza", "prezzo_unitario": "1000.00"}],
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["data_emissione"] == "2026-09-05"
    assert body["competenza_da"] == "2026-08-01"
    assert body["competenza_a"] == "2026-08-31"

    read = logged_in.get(f"/api/invoices/{body['id']}").json()
    assert (read["competenza_da"], read["competenza_a"]) == ("2026-08-01", "2026-08-31")


def test_a_proforma_defaults_to_today_and_a_fattura_draft_has_no_date(
    logged_in: TestClient, customer: dict[str, Any], fiscal_profile: dict[str, Any]
) -> None:
    proforma = logged_in.post(
        "/api/invoices", json={"customer_id": customer["id"], "tipo": "proforma"}
    )
    assert proforma.status_code == 201, proforma.text
    assert proforma.json()["data_emissione"] == TODAY

    draft = _draft(logged_in, customer["id"])
    assert draft["data_emissione"] is None
    assert draft["competenza_da"] is None and draft["competenza_a"] is None
    refused = logged_in.post(
        "/api/invoices",
        json={"customer_id": customer["id"], "tipo": "fattura", "data_emissione": TODAY},
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["field"] == "data_emissione"


def test_the_period_and_the_proforma_date_are_patched_on_a_draft(
    logged_in: TestClient, customer: dict[str, Any], fiscal_profile: dict[str, Any]
) -> None:
    proforma = logged_in.post(
        "/api/invoices",
        json={
            "customer_id": customer["id"],
            "tipo": "proforma",
            "righe": [{"descrizione": "Consulenza", "prezzo_unitario": "1000.00"}],
        },
    ).json()
    patched = logged_in.patch(
        f"/api/invoices/{proforma['id']}",
        json={
            "data_emissione": "2026-09-05",
            "competenza_da": "2026-08-01",
            "competenza_a": "2026-08-31",
        },
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["data_emissione"] == "2026-09-05"
    assert patched.json()["competenza_da"] == "2026-08-01"

    # Half a period is a 422 naming the missing end, and the row is untouched.
    half = logged_in.patch(f"/api/invoices/{proforma['id']}", json={"competenza_da": None})
    assert half.status_code == 422, half.text
    assert half.json()["field"] == "competenza_da"
    assert logged_in.get(f"/api/invoices/{proforma['id']}").json()["competenza_da"] == (
        "2026-08-01"
    )
    # Both ends as null clear the period.
    cleared = logged_in.patch(
        f"/api/invoices/{proforma['id']}", json={"competenza_da": None, "competenza_a": None}
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["competenza_da"] is None


def test_the_period_is_frozen_once_issued_and_travels_to_the_xml(
    logged_in: TestClient,
    customer: dict[str, Any],
    fiscal_profile: dict[str, Any],
    emitter: dict[str, Any],
) -> None:
    draft = logged_in.post(
        "/api/invoices",
        json={
            "customer_id": customer["id"],
            "competenza_da": "2026-08-01",
            "competenza_a": "2026-08-31",
            "righe": [{"descrizione": "Consulenza", "prezzo_unitario": "1000.00"}],
        },
    ).json()
    issued = logged_in.post(f"/api/invoices/{draft['id']}/issue", json={})
    assert issued.status_code == 200, issued.text
    assert issued.json()["competenza_da"] == "2026-08-01"

    frozen = logged_in.patch(f"/api/invoices/{draft['id']}", json={"competenza_a": "2026-09-30"})
    assert frozen.status_code == 409, frozen.text
    assert frozen.json()["field"] == "competenza_a"

    assert logged_in.post(f"/api/invoices/{draft['id']}/artifacts").status_code == 200
    xml = logged_in.get(f"/api/invoices/{draft['id']}/xml")
    assert xml.status_code == 200, xml.text
    assert b"<DataInizioPeriodo>2026-08-01</DataInizioPeriodo>" in xml.content
    assert b"<DataFinePeriodo>2026-08-31</DataFinePeriodo>" in xml.content
