"""`/api/proposals`: REB-362's REST surface. `packages/core/tests/test_proposals_
service.py` owns the business-rule edge cases; this file owns "the route is wired,
returns the right status codes, and the JSON round-trips through the real HTTP
stack" -- the same division `test_role_matrix.py` draws for authorization.
"""

from typing import Any

from fastapi.testclient import TestClient


def _customer(client: TestClient) -> str:
    response = client.post("/api/customers", json={"ragione_sociale": "ACME S.r.l."})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _contract(client: TestClient, customer_id: str) -> str:
    response = client.post(
        "/api/contracts",
        json={
            "customer_id": customer_id,
            "titolo": "Consulenza",
            "inizio": "2026-01-01",
            "tipo_rinnovo": "nessuno",
            "preavviso_disdetta_giorni": 30,
            "cadenza_fatturazione": "mensile",
            "politica_spese": {"kind": "non_rimborsabile"},
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _document(client: TestClient, **owner: str) -> str:
    response = client.post(
        "/api/documents", json={**owner, "tipo": "contratto", "titolo": "Documento"}
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _giornata_body(document_id: str, contract_id: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "document_id": document_id,
        "contract_id": contract_id,
        "target_type": "giornata",
        "estratto": "Confermiamo la giornata del 5 marzo.",
        "confidenza": "0.90",
        "campi_proposti": {
            "contract_id": contract_id,
            "data": "2026-03-05",
            "quantita": "1.00",
            "descrizione": "Giornata di consulenza",
            "approvazione": {
                "canale": "email",
                "mittente": "cliente@example.it",
                "ricevuto_il": "2026-03-01T09:00:00Z",
            },
        },
    }
    body.update(overrides)
    return body


def _contratto_body(document_id: str, customer_id: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "document_id": document_id,
        "target_type": "contratto",
        "estratto": "Il presente contratto ha durata annuale.",
        "confidenza": "0.85",
        "campi_proposti": {
            "contract": {
                "customer_id": customer_id,
                "titolo": "Consulenza annuale",
                "inizio": "2026-01-01",
                "tipo_rinnovo": "nessuno",
                "preavviso_disdetta_giorni": 30,
                "cadenza_fatturazione": "mensile",
                "politica_spese": {"kind": "non_rimborsabile"},
            },
            "rate_card": {
                "valido_da": "2026-01-01",
                "tipo": "ricorrente_fisso",
                "importo": "1000.00",
                "unita": "mese",
                "periodo_erogazione": "mensile",
            },
        },
    }
    body.update(overrides)
    return body


def test_create_and_get_a_giornata_proposal(logged_in: TestClient) -> None:
    customer_id = _customer(logged_in)
    contract_id = _contract(logged_in, customer_id)
    document_id = _document(logged_in, contract_id=contract_id)

    created = logged_in.post("/api/proposals", json=_giornata_body(document_id, contract_id))
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["stato"] == "in_attesa"
    assert body["target_type"] == "giornata"

    fetched = logged_in.get(f"/api/proposals/{body['id']}")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["estratto"] == body["estratto"]


def test_create_rejects_a_giornata_with_no_contract_id(logged_in: TestClient) -> None:
    customer_id = _customer(logged_in)
    contract_id = _contract(logged_in, customer_id)
    document_id = _document(logged_in, contract_id=contract_id)

    body = _giornata_body(document_id, contract_id)
    body["contract_id"] = None
    response = logged_in.post("/api/proposals", json=body)
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "validation_failed"


def test_accept_a_giornata_proposal_creates_an_approved_work_unit(logged_in: TestClient) -> None:
    customer_id = _customer(logged_in)
    contract_id = _contract(logged_in, customer_id)
    document_id = _document(logged_in, contract_id=contract_id)
    created = logged_in.post("/api/proposals", json=_giornata_body(document_id, contract_id))
    proposal_id = created.json()["id"]

    accepted = logged_in.post(
        f"/api/proposals/{proposal_id}/accept", json={"deciso_da": "Lorenzo Fiore"}
    )
    assert accepted.status_code == 200, accepted.text
    body = accepted.json()
    assert body["stato"] == "accettata"
    assert body["id_risultato"] is not None
    assert body["deciso_da"] == "Lorenzo Fiore"


def test_accept_a_contratto_proposal_creates_a_contract_and_repoints_the_document(
    logged_in: TestClient,
) -> None:
    customer_id = _customer(logged_in)
    document_id = _document(logged_in, customer_id=customer_id)
    created = logged_in.post("/api/proposals", json=_contratto_body(document_id, customer_id))
    proposal_id = created.json()["id"]

    accepted = logged_in.post(
        f"/api/proposals/{proposal_id}/accept", json={"deciso_da": "Lorenzo Fiore"}
    )
    assert accepted.status_code == 200, accepted.text
    contract_id = accepted.json()["id_risultato"]
    assert contract_id is not None

    contract = logged_in.get(f"/api/contracts/{contract_id}")
    assert contract.status_code == 200, contract.text
    assert contract.json()["titolo"] == "Consulenza annuale"

    document = logged_in.get(f"/api/documents/{document_id}")
    assert document.status_code == 200, document.text
    assert document.json()["contract_id"] == contract_id


def test_reject_a_proposal_creates_nothing(logged_in: TestClient) -> None:
    customer_id = _customer(logged_in)
    contract_id = _contract(logged_in, customer_id)
    document_id = _document(logged_in, contract_id=contract_id)
    created = logged_in.post("/api/proposals", json=_giornata_body(document_id, contract_id))
    proposal_id = created.json()["id"]

    rejected = logged_in.post(
        f"/api/proposals/{proposal_id}/reject",
        json={"deciso_da": "Lorenzo Fiore", "motivo": "date sbagliate"},
    )
    assert rejected.status_code == 200, rejected.text
    body = rejected.json()
    assert body["stato"] == "rifiutata"
    assert body["id_risultato"] is None


def test_accept_of_an_already_decided_proposal_is_a_409(logged_in: TestClient) -> None:
    customer_id = _customer(logged_in)
    contract_id = _contract(logged_in, customer_id)
    document_id = _document(logged_in, contract_id=contract_id)
    created = logged_in.post("/api/proposals", json=_giornata_body(document_id, contract_id))
    proposal_id = created.json()["id"]
    logged_in.post(f"/api/proposals/{proposal_id}/reject", json={"deciso_da": "Lorenzo"})

    response = logged_in.post(f"/api/proposals/{proposal_id}/accept", json={"deciso_da": "Lorenzo"})
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "conflict"


def test_list_filters_by_stato(logged_in: TestClient) -> None:
    customer_id = _customer(logged_in)
    contract_id = _contract(logged_in, customer_id)
    document_id = _document(logged_in, contract_id=contract_id)
    first = logged_in.post(
        "/api/proposals", json=_giornata_body(document_id, contract_id, estratto="prima")
    ).json()
    second = logged_in.post(
        "/api/proposals",
        json=_giornata_body(
            document_id,
            contract_id,
            estratto="seconda",
            campi_proposti={
                **_giornata_body(document_id, contract_id)["campi_proposti"],
                "data": "2026-03-06",
            },
        ),
    ).json()
    logged_in.post(f"/api/proposals/{second['id']}/reject", json={"deciso_da": "Lorenzo"})

    response = logged_in.get("/api/proposals", params={"stato": "in_attesa"})
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()["items"]}
    assert first["id"] in ids
    assert second["id"] not in ids


def test_get_of_an_unknown_proposal_is_a_404(logged_in: TestClient) -> None:
    from uuid import uuid4

    response = logged_in.get(f"/api/proposals/{uuid4()}")
    assert response.status_code == 404
