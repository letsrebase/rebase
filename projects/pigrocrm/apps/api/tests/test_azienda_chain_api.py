"""The azienda over HTTP (REB-624, spec 2026-10-03 §1.6, §3): the proposal route, the
customer routes that take an `azienda_id`, and every list that narrows to one azienda.

The second azienda is written by row, since no route creates one before milestone 5
(§9). The rows under test are made through the routes where a route exists and through
the core services where creating them over HTTP would need the fiscal set-up the test
is not about (a contract, an invoice).
"""

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.contracts.schemas import ContractCreate
from pigrocrm.core.contracts.service import ContractService
from pigrocrm.core.emitter.models import Azienda
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.invoices.schemas import InvoiceCreate, InvoiceLineIn
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.storage.local import LocalFileStorage

ADMIN = Actor(id=None, type="system", role="admin")


def _default_id(client: TestClient) -> str:
    aziende = client.get("/api/aziende").json()
    assert aziende and aziende[0]["predefinita"]
    return str(aziende[0]["id"])


def _second_azienda(session: Session, nome: str = "rebase ltd", nazione: str = "GB") -> str:
    row = Azienda(nome=nome, ragione_sociale=f"{nome.title()}", nazione=nazione)
    session.add(row)
    session.flush()
    return str(row.id)


def _customer(client: TestClient, nome: str, **extra: Any) -> dict[str, Any]:
    created = client.post("/api/customers", json={"ragione_sociale": nome, **extra})
    assert created.status_code == 201, created.text
    return dict(created.json())


def _deal(client: TestClient, customer_id: str, nome: str) -> str:
    assert client.post("/api/pipeline-stages/seed").status_code == 200
    created = client.post("/api/deals", json={"nome": nome, "customer_id": customer_id})
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


def _ids(client: TestClient, path: str, **params: Any) -> set[str]:
    response = client.get(path, params=params)
    assert response.status_code == 200, response.text
    return {item["id"] for item in response.json()["items"]}


# --- the proposal -----------------------------------------------------------------


def test_the_proposal_route_answers_the_azienda_the_nation_proposes(
    logged_in: TestClient, api_session: Session
) -> None:
    default = _default_id(logged_in)
    british = _second_azienda(api_session)

    def proposed(**params: Any) -> str:
        response = logged_in.get("/api/aziende/proposta", params=params)
        assert response.status_code == 200, response.text
        return str(response.json()["id"])

    assert proposed(nazione="GB") == british
    assert proposed(nazione="gb") == british
    # No azienda of that nation: the one foreign azienda for a foreign customer.
    assert proposed(nazione="DE") == british
    assert proposed(nazione="IT") == default
    assert proposed() == default


def test_the_proposal_is_readable_by_every_role_and_refuses_a_malformed_nation(
    readonly_client: TestClient,
) -> None:
    assert readonly_client.get("/api/aziende/proposta").status_code == 200
    assert (
        readonly_client.get("/api/aziende/proposta", params={"nazione": "ITA"}).status_code == 422
    )


# --- the customer -----------------------------------------------------------------


def test_a_customer_gets_the_proposal_or_keeps_the_azienda_named(
    logged_in: TestClient, api_session: Session
) -> None:
    default = _default_id(logged_in)
    british = _second_azienda(api_session)
    proposed = _customer(logged_in, "Overseas Ltd", nazione="GB")
    assert proposed["azienda_id"] == british
    named = _customer(logged_in, "Oltre Ltd", nazione="GB", azienda_id=default)
    assert named["azienda_id"] == default
    read_back = logged_in.get(f"/api/customers/{named['id']}").json()
    assert read_back["azienda_id"] == default

    moved = logged_in.patch(f"/api/customers/{named['id']}", json={"azienda_id": british})
    assert moved.status_code == 200, moved.text
    assert moved.json()["azienda_id"] == british

    unknown = logged_in.post(
        "/api/customers", json={"ragione_sociale": "Nessuna", "azienda_id": str(uuid4())}
    )
    assert unknown.status_code == 404, unknown.text
    assert unknown.json()["code"] == "not_found"


def test_an_inactive_azienda_is_refused_on_a_customer_naming_the_field(
    logged_in: TestClient, api_session: Session
) -> None:
    closed = Azienda(nome="chiusa", ragione_sociale="Chiusa S.r.l.", attiva=False)
    api_session.add(closed)
    api_session.flush()
    refused = logged_in.post(
        "/api/customers", json={"ragione_sociale": "X", "azienda_id": str(closed.id)}
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["field"] == "azienda_id"


# --- the lists, the calendar and the search -----------------------------------------


def test_every_list_route_narrows_to_one_azienda(
    logged_in: TestClient, api_session: Session, tmp_path: Path
) -> None:
    default = _default_id(logged_in)
    second = _second_azienda(api_session, nome="rebase", nazione="IT")
    fiscal = FiscalProfileService(api_session)
    fiscal.upsert(FiscalProfileUpsert(codice_regime="RF19"), ADMIN)
    fiscal.upsert(FiscalProfileUpsert(codice_regime="RF19"), ADMIN, azienda_id=UUID(second))

    acme = _customer(logged_in, "Acme S.r.l.")["id"]
    beta = _customer(logged_in, "Beta S.r.l.", azienda_id=second)["id"]
    deal_a = _deal(logged_in, acme, "Acme deal")
    deal_b = _deal(logged_in, beta, "Beta deal")
    for customer in (acme, beta):
        ContractService(api_session).create(
            ContractCreate(
                customer_id=UUID(customer),
                titolo="Consulenza",
                inizio=date(2026, 1, 1),
                tipo_rinnovo="nessuno",
                preavviso_disdetta_giorni=30,
                cadenza_fatturazione="mensile",
                politica_spese={"tipo": "non_rimborsabile"},
            ),
            ADMIN,
        )
        InvoiceService(api_session, LocalFileStorage(tmp_path)).create(
            InvoiceCreate(
                customer_id=UUID(customer),
                righe=[InvoiceLineIn(descrizione="Consulenza", prezzo_unitario=Decimal("1.00"))],
            ),
            ADMIN,
        )
    for customer, titolo in ((acme, "Acme brief"), (beta, "Beta brief")):
        created = logged_in.post(
            "/api/documents", json={"customer_id": customer, "tipo": "documento", "titolo": titolo}
        )
        assert created.status_code == 201, created.text
    category = logged_in.post("/api/cost-categories", json={"nome": "Viaggi"}).json()["id"]
    me = str(logged_in.get("/api/auth/me").json()["id"])
    for deal, giorno in ((deal_a, "2026-03-02"), (deal_b, "2026-03-03")):
        cost = logged_in.post(
            "/api/costs",
            json={
                "deal_id": deal,
                "category_id": category,
                "data": giorno,
                "importo": "10.00",
                "descrizione": "Treno",
            },
        )
        assert cost.status_code == 201, cost.text
        hours = logged_in.post(
            "/api/time-entries",
            json={
                "deal_id": deal,
                "user_id": me,
                "data": giorno,
                "ore": "1.00",
                "descrizione": "Sviluppo",
            },
        )
        assert hours.status_code == 201, hours.text
    shared = logged_in.post(
        "/api/costs",
        json={
            "category_id": category,
            "data": "2026-03-04",
            "importo": "5.00",
            "descrizione": "Software",
        },
    )
    assert shared.status_code == 201, shared.text
    assert shared.json()["azienda_id"] is None

    for path in (
        "/api/customers",
        "/api/deals",
        "/api/contracts",
        "/api/documents",
        "/api/invoices",
        "/api/costs",
        "/api/time-entries",
    ):
        on_default = _ids(logged_in, path, azienda_id=default)
        on_second = _ids(logged_in, path, azienda_id=second)
        everything = _ids(logged_in, path)
        assert on_default and on_second, path
        assert on_default.isdisjoint(on_second), path
        assert on_default | on_second <= everything, path
        assert _ids(logged_in, path, azienda_id=str(uuid4())) == set(), path
        assert logged_in.get(path, params={"azienda_id": "non-un-id"}).status_code == 422, path
    # The shared cost is in the whole list and in neither azienda's.
    assert shared.json()["id"] in _ids(logged_in, "/api/costs")
    assert shared.json()["id"] not in _ids(logged_in, "/api/costs", azienda_id=default)

    # The calendar: the hours through their deal, the activities untouched.
    whole = logged_in.get("/api/calendar", params={"mese": "2026-03", "tutti": "true"}).json()
    narrowed = logged_in.get(
        "/api/calendar", params={"mese": "2026-03", "tutti": "true", "azienda_id": second}
    ).json()
    assert [g["giorno"] for g in whole["giorni"] if g["ore"]] == ["2026-03-02", "2026-03-03"]
    assert [g["giorno"] for g in narrowed["giorni"] if g["ore"]] == ["2026-03-03"]

    # The search: «brief» finds both documents, and one under the second azienda.
    everyone = logged_in.get("/api/search", params={"q": "brief"}).json()
    narrowed_search = logged_in.get(
        "/api/search", params={"q": "brief", "azienda_id": second}
    ).json()
    documents = next(g for g in everyone["gruppi"] if g["entity"] == "document")
    assert documents["totale"] == 2
    narrowed_documents = next(g for g in narrowed_search["gruppi"] if g["entity"] == "document")
    assert [h["etichetta"] for h in narrowed_documents["hits"]] == ["Beta brief"]


def test_the_openapi_document_declares_the_azienda_on_every_list_and_the_proposal(
    logged_in: TestClient,
) -> None:
    schema = logged_in.get("/openapi.json").json()
    for path in (
        "/api/customers",
        "/api/deals",
        "/api/contracts",
        "/api/documents",
        "/api/invoices",
        "/api/costs",
        "/api/time-entries",
        "/api/calendar",
        "/api/search",
    ):
        names = {p["name"] for p in schema["paths"][path]["get"].get("parameters", [])}
        assert "azienda_id" in names, path
    assert "get" in schema["paths"]["/api/aziende/proposta"]
    for model in ("CustomerRead", "DealRead", "ContractRead", "DocumentRead", "CostRead"):
        assert "azienda_id" in schema["components"]["schemas"][model]["properties"], model
