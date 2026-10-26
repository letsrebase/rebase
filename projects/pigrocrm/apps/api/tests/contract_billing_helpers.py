"""The azienda, the regime, a customer, a contract with a rate card: what the contract billing
tests (REB-751, REB-752, REB-753) start from, as plain functions so each test file declares
its own fixtures over them (`aziende_helpers.py` is the same shape)."""

from typing import Any

from aziende_helpers import azienda_url
from fastapi.testclient import TestClient


def set_up_fiscal(logged_in: TestClient) -> None:
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


def create_contract(logged_in: TestClient) -> dict[str, Any]:
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
