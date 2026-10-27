"""`/api/proposals`: REB-362's REST surface. `packages/core/tests/test_proposals_
service.py` owns the business-rule edge cases; this file owns "the route is wired,
returns the right status codes, and the JSON round-trips through the real HTTP
stack" -- the same division `test_role_matrix.py` draws for authorization.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from pigrocrm.core.config import Settings


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

    accepted = logged_in.post(f"/api/proposals/{proposal_id}/accept", json={})
    assert accepted.status_code == 200, accepted.text
    body = accepted.json()
    assert body["stato"] == "accettata"
    assert body["id_risultato"] is not None
    assert body["deciso_da"] == "Admin"


def test_accept_a_contratto_proposal_creates_a_contract_and_repoints_the_document(
    logged_in: TestClient,
) -> None:
    customer_id = _customer(logged_in)
    document_id = _document(logged_in, customer_id=customer_id)
    created = logged_in.post("/api/proposals", json=_contratto_body(document_id, customer_id))
    proposal_id = created.json()["id"]

    accepted = logged_in.post(f"/api/proposals/{proposal_id}/accept", json={})
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
        json={"motivo": "date sbagliate"},
    )
    assert rejected.status_code == 200, rejected.text
    body = rejected.json()
    assert body["stato"] == "rifiutata"
    assert body["id_risultato"] is None


def test_timeline_keeps_the_rejection_reason(logged_in: TestClient) -> None:
    customer_id = _customer(logged_in)
    contract_id = _contract(logged_in, customer_id)
    document_id = _document(logged_in, contract_id=contract_id)
    created = logged_in.post("/api/proposals", json=_giornata_body(document_id, contract_id))
    proposal_id = created.json()["id"]
    logged_in.post(
        f"/api/proposals/{proposal_id}/reject",
        json={"motivo": "date sbagliate"},
    )

    response = logged_in.get(f"/api/proposals/{proposal_id}/timeline")
    assert response.status_code == 200, response.text
    rejected = [row for row in response.json() if row["kind"] == "rejected"]
    assert len(rejected) == 1
    assert rejected[0]["payload"] == {"deciso_da": "Admin", "motivo": "date sbagliate"}
    assert {row["kind"] for row in response.json()} == {"created", "rejected"}


def test_timeline_of_an_unknown_proposal_is_a_404(logged_in: TestClient) -> None:
    from uuid import uuid4

    response = logged_in.get(f"/api/proposals/{uuid4()}/timeline")
    assert response.status_code == 404


def test_accept_of_an_already_decided_proposal_is_a_409(logged_in: TestClient) -> None:
    customer_id = _customer(logged_in)
    contract_id = _contract(logged_in, customer_id)
    document_id = _document(logged_in, contract_id=contract_id)
    created = logged_in.post("/api/proposals", json=_giornata_body(document_id, contract_id))
    proposal_id = created.json()["id"]
    logged_in.post(f"/api/proposals/{proposal_id}/reject", json={})

    response = logged_in.post(f"/api/proposals/{proposal_id}/accept", json={})
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
    logged_in.post(f"/api/proposals/{second['id']}/reject", json={})

    response = logged_in.get("/api/proposals", params={"stato": "in_attesa"})
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()["items"]}
    assert first["id"] in ids
    assert second["id"] not in ids


def test_list_orders_decided_proposals_newest_decision_first(logged_in: TestClient) -> None:
    customer_id = _customer(logged_in)
    contract_id = _contract(logged_in, customer_id)
    document_id = _document(logged_in, contract_id=contract_id)
    ids = []
    for day in ("2026-03-05", "2026-03-06", "2026-03-07"):
        body = _giornata_body(document_id, contract_id, estratto=day)
        body["campi_proposti"]["data"] = day
        ids.append(logged_in.post("/api/proposals", json=body).json()["id"])
    # The first and the last to arrive are decided, the last one most recently; the middle
    # one is left waiting.
    logged_in.post(f"/api/proposals/{ids[0]}/reject", json={})
    logged_in.post(f"/api/proposals/{ids[2]}/reject", json={})

    arrival = logged_in.get("/api/proposals", params={"stato": "rifiutata"})
    assert [item["id"] for item in arrival.json()["items"]] == [ids[0], ids[2]]

    decided = logged_in.get("/api/proposals", params={"stato": "rifiutata", "ordine": "decisione"})
    assert decided.status_code == 200, decided.text
    assert [item["id"] for item in decided.json()["items"]] == [ids[2], ids[0]]

    capped = logged_in.get(
        "/api/proposals", params={"stato": "rifiutata", "ordine": "decisione", "limit": 1}
    )
    assert [item["id"] for item in capped.json()["items"]] == [ids[2]]

    unknown = logged_in.get("/api/proposals", params={"ordine": "a-caso"})
    assert unknown.status_code == 422


def test_get_of_an_unknown_proposal_is_a_404(logged_in: TestClient) -> None:
    from uuid import uuid4

    response = logged_in.get(f"/api/proposals/{uuid4()}")
    assert response.status_code == 404


def test_a_decider_name_in_the_body_is_refused_not_stored(logged_in: TestClient) -> None:
    """REB-806: who decided is the session, never a field."""
    customer_id = _customer(logged_in)
    contract_id = _contract(logged_in, customer_id)
    document_id = _document(logged_in, contract_id=contract_id)
    proposal_id = logged_in.post(
        "/api/proposals", json=_giornata_body(document_id, contract_id)
    ).json()["id"]

    response = logged_in.post(
        f"/api/proposals/{proposal_id}/accept", json={"deciso_da": "Il Titolare"}
    )

    assert response.status_code == 422, response.text
    assert logged_in.get(f"/api/proposals/{proposal_id}").json()["stato"] == "in_attesa"


@pytest.mark.parametrize("full_access", [False, True])
def test_an_agent_token_can_never_decide_a_proposal(
    logged_in: TestClient, monkeypatch: pytest.MonkeyPatch, full_access: bool
) -> None:
    """REB-806: the credential an agent holds proposes and cannot decide, with the
    full-access switch on as much as off, and with an administrator's role."""
    customer_id = _customer(logged_in)
    contract_id = _contract(logged_in, customer_id)
    document_id = _document(logged_in, contract_id=contract_id)
    proposal_id = logged_in.post(
        "/api/proposals", json=_giornata_body(document_id, contract_id)
    ).json()["id"]
    token = logged_in.post("/api/tokens", json={"nome": "Claude"}).json()["token"]
    monkeypatch.setattr(
        "pigrocrm.core.auth.pat_service.get_settings",
        lambda: Settings(_env_file=None, mcp_full_access=full_access),  # type: ignore[call-arg]
    )
    agent = TestClient(logged_in.app, base_url="https://testserver")
    agent.headers["Authorization"] = f"Bearer {token}"

    accepted = agent.post(f"/api/proposals/{proposal_id}/accept", json={})
    rejected = agent.post(f"/api/proposals/{proposal_id}/reject", json={})

    assert accepted.status_code == 403, accepted.text
    assert accepted.json()["code"] == "agent_forbidden"
    assert rejected.status_code == 403, rejected.text
    assert logged_in.get(f"/api/proposals/{proposal_id}").json()["stato"] == "in_attesa"
