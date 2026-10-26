"""REB-751 and REB-752 at the wire: the days a contract has ready, the draft that bills
them, and the register that goes with the invoice. The rules are the core's
(`test_work_units_invoicing.py`, `test_contract_register.py`); this proves the routes
answer them in the shapes the contract page reads, and that the gates hold."""

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from aziende_helpers import azienda_url
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pigrocrm.core.work_units.models import WorkUnit


@pytest.fixture
def fiscal_setup(logged_in: TestClient) -> None:
    assert (
        logged_in.put(
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
                "email": "mario@example.com",
            },
        ).status_code
        == 200
    )
    assert (
        logged_in.put(
            azienda_url(logged_in, "/fiscal-profile"), json={"codice_regime": "RF19"}
        ).status_code
        == 200
    )


@pytest.fixture
def contract(logged_in: TestClient, fiscal_setup: None) -> dict[str, Any]:
    customer = logged_in.post(
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
    assert customer.status_code == 201, customer.text
    created = logged_in.post(
        "/api/contracts",
        json={
            "customer_id": customer.json()["id"],
            "titolo": "Consulenza CTO",
            "inizio": "2026-01-01",
            "tipo_rinnovo": "nessuno",
            "preavviso_disdetta_giorni": 30,
            "cadenza_fatturazione": "mensile",
            "politica_spese": {"kind": "non_rimborsabile"},
        },
    )
    assert created.status_code == 201, created.text
    card = logged_in.post(
        f"/api/contracts/{created.json()['id']}/rate-cards",
        json={
            "valido_da": "2026-01-01",
            "tipo": "giornaliero",
            "importo": "500.00",
            "unita": "giorno",
        },
    )
    assert card.status_code == 201, card.text
    return created.json()


def _day(api_session: Session, contract: dict[str, Any], day: date, quantita: str = "1.00") -> UUID:
    row = WorkUnit(
        contract_id=UUID(contract["id"]),
        data=day,
        quantita=Decimal(quantita),
        descrizione="Giornata di consulenza",
        stato="lavorato",
    )
    api_session.add(row)
    api_session.flush()
    return row.id


def test_billable_days_reads_as_the_lines_the_draft_would_carry(
    logged_in: TestClient, api_session: Session, contract: dict[str, Any]
) -> None:
    _day(api_session, contract, date(2026, 10, 7))
    _day(api_session, contract, date(2026, 10, 8), "0.50")
    _day(api_session, contract, date(2026, 11, 3))

    response = logged_in.get(f"/api/contracts/{contract['id']}/billable-days")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["fatturabile"] is True
    assert body["mesi"] == ["2026-10", "2026-11"]
    assert [(r["mese"], r["giornate"], r["importo"]) for r in body["righe"]] == [
        ("2026-10", 2, "750.00"),
        ("2026-11", 1, "500.00"),
    ]
    assert body["imponibile"] == "1250.00"

    october = logged_in.get(
        f"/api/contracts/{contract['id']}/billable-days",
        params={"da": "2026-10-01", "a": "2026-10-31"},
    ).json()
    assert [r["mese"] for r in october["righe"]] == ["2026-10"]


def test_invoicing_a_month_makes_a_draft_and_the_days_stop_being_ready(
    logged_in: TestClient, api_session: Session, contract: dict[str, Any]
) -> None:
    october = _day(api_session, contract, date(2026, 10, 7))
    _day(api_session, contract, date(2026, 11, 3))

    response = logged_in.post(
        f"/api/contracts/{contract['id']}/invoice-days",
        json={"da": "2026-10-01", "a": "2026-10-31"},
    )

    assert response.status_code == 201, response.text
    invoice = response.json()
    assert invoice["stato"] == "bozza"
    assert invoice["imponibile"] == "500.00"
    assert invoice["customer_id"] == contract["customer_id"]
    api_session.expire_all()
    assert api_session.get(WorkUnit, october).stato == "fatturato"  # type: ignore[union-attr]
    ready = logged_in.get(f"/api/contracts/{contract['id']}/billable-days").json()
    assert [r["mese"] for r in ready["righe"]] == ["2026-11"]


def test_invoicing_with_nothing_ready_is_a_422_not_an_empty_invoice(
    logged_in: TestClient, contract: dict[str, Any]
) -> None:
    response = logged_in.post(f"/api/contracts/{contract['id']}/invoice-days", json={})
    assert response.status_code == 422, response.text


def test_a_period_that_ends_before_it_starts_is_refused(
    logged_in: TestClient, contract: dict[str, Any]
) -> None:
    response = logged_in.post(
        f"/api/contracts/{contract['id']}/invoice-days",
        json={"da": "2026-10-31", "a": "2026-10-01"},
    )
    assert response.status_code == 422, response.text


def test_a_collaborator_may_read_what_is_ready_but_not_bill_it(
    logged_in: TestClient, contract: dict[str, Any]
) -> None:
    # An independent cookie jar over the same app: `logged_in` and `collaborator_client`
    # share one, and the later login would answer for both.
    created = logged_in.post(
        "/api/users",
        json={
            "email": "collaboratore-fatturazione@pigro.it",
            "password": "supersegreta1",
            "nome": "Test",
            "ruolo": "collaboratore",
        },
    )
    assert created.status_code == 201, created.text
    collaborator = TestClient(logged_in.app, base_url="https://testserver")
    login = collaborator.post(
        "/api/auth/login",
        json={"email": "collaboratore-fatturazione@pigro.it", "password": "supersegreta1"},
    )
    assert login.status_code == 200, login.text

    assert collaborator.get(f"/api/contracts/{contract['id']}/billable-days").status_code == 200
    refused = collaborator.post(f"/api/contracts/{contract['id']}/invoice-days", json={})
    assert refused.status_code == 403, refused.text


def test_the_register_json_csv_and_pdf_agree_on_the_month(
    logged_in: TestClient, api_session: Session, contract: dict[str, Any]
) -> None:
    _day(api_session, contract, date(2026, 10, 7))
    _day(api_session, contract, date(2026, 10, 8), "0.50")
    invoice = logged_in.post(
        f"/api/contracts/{contract['id']}/invoice-days",
        json={"da": "2026-10-01", "a": "2026-10-31"},
    ).json()
    period = {"da": "2026-10-01", "a": "2026-10-31"}

    register = logged_in.get(f"/api/contracts/{contract['id']}/register", params=period)
    assert register.status_code == 200, register.text
    body = register.json()
    assert body["totale_quantita"] == "1.50"
    assert [v["data"] for v in body["voci"]] == ["2026-10-07", "2026-10-08"]
    assert invoice["imponibile"] == "750.00"

    csv = logged_in.get(f"/api/contracts/{contract['id']}/register/csv", params=period)
    assert csv.status_code == 200, csv.text
    assert csv.headers["content-type"].startswith("text/csv")
    assert (
        'filename="registro-giornate-2026-10-01-2026-10-31.csv"'
        in csv.headers["content-disposition"]
    )
    assert csv.content.startswith(b"\xef\xbb\xbf")
    assert "\r\n,1.50,Totale,," in csv.text

    pdf = logged_in.post(f"/api/contracts/{contract['id']}/register/pdf", params=period)
    assert pdf.status_code == 201, pdf.text
    document = pdf.json()
    assert document["customer_id"] == contract["customer_id"]
    download = logged_in.get(f"/api/documents/{document['id']}/download")
    assert download.status_code == 200, download.text
    assert download.content.startswith(b"%PDF")


def test_the_register_needs_a_period(logged_in: TestClient, contract: dict[str, Any]) -> None:
    assert logged_in.get(f"/api/contracts/{contract['id']}/register").status_code == 422


def test_the_register_of_a_contract_that_does_not_exist_is_a_404(logged_in: TestClient) -> None:
    response = logged_in.get(
        "/api/contracts/00000000-0000-4000-8000-000000000000/register",
        params={"da": "2026-10-01", "a": "2026-10-31"},
    )
    assert response.status_code == 404, response.text
