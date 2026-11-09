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
    response = client.post("/api/organizations", json={"legal_name": "ACME S.r.l."})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _contract(client: TestClient, customer_id: str) -> str:
    response = client.post(
        "/api/contracts",
        json={
            "customer_id": customer_id,
            "title": "Consulenza",
            "start": "2026-01-01",
            "renewal_type": "none",
            "termination_notice_days": 30,
            "billing_cadence": "monthly",
            "expense_policy": {"kind": "not_reimbursable"},
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _document(client: TestClient, **owner: str) -> str:
    response = client.post(
        "/api/documents", json={**owner, "type": "contract", "title": "Documento"}
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _work_unit_body(document_id: str, contract_id: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "document_id": document_id,
        "contract_id": contract_id,
        "target_type": "work_unit",
        "excerpt": "Confermiamo la giornata del 5 marzo.",
        "confidence": "0.90",
        "proposed_fields": {
            "contract_id": contract_id,
            "date": "2026-03-05",
            "quantity": "1.00",
            "description": "Giornata di consulenza",
            "approval": {
                "channel": "email",
                "sender": "cliente@example.it",
                "received_at": "2026-03-01T09:00:00Z",
            },
        },
    }
    body.update(overrides)
    return body


def _contract_body(document_id: str, customer_id: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "document_id": document_id,
        "target_type": "contract",
        "excerpt": "Il presente contratto ha durata annuale.",
        "confidence": "0.85",
        "proposed_fields": {
            "contract": {
                "customer_id": customer_id,
                "title": "Consulenza annuale",
                "start": "2026-01-01",
                "renewal_type": "none",
                "termination_notice_days": 30,
                "billing_cadence": "monthly",
                "expense_policy": {"kind": "not_reimbursable"},
            },
            "rate_card": {
                "valid_from": "2026-01-01",
                "type": "fixed_recurring",
                "amount": "1000.00",
                "unit": "month",
                "delivery_period": "monthly",
            },
        },
    }
    body.update(overrides)
    return body


def test_create_and_get_to_work_unit_proposal(logged_in: TestClient) -> None:
    customer_id = _customer(logged_in)
    contract_id = _contract(logged_in, customer_id)
    document_id = _document(logged_in, contract_id=contract_id)

    created = logged_in.post("/api/proposals", json=_work_unit_body(document_id, contract_id))
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "pending"
    assert body["target_type"] == "work_unit"

    fetched = logged_in.get(f"/api/proposals/{body['id']}")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["excerpt"] == body["excerpt"]


def test_create_rejects_to_work_unit_with_no_contract_id(logged_in: TestClient) -> None:
    customer_id = _customer(logged_in)
    contract_id = _contract(logged_in, customer_id)
    document_id = _document(logged_in, contract_id=contract_id)

    body = _work_unit_body(document_id, contract_id)
    body["contract_id"] = None
    response = logged_in.post("/api/proposals", json=body)
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "validation_failed"


def test_accept_to_work_unit_proposal_creates_an_approved_work_unit(logged_in: TestClient) -> None:
    customer_id = _customer(logged_in)
    contract_id = _contract(logged_in, customer_id)
    document_id = _document(logged_in, contract_id=contract_id)
    created = logged_in.post("/api/proposals", json=_work_unit_body(document_id, contract_id))
    proposal_id = created.json()["id"]

    accepted = logged_in.post(f"/api/proposals/{proposal_id}/accept", json={})
    assert accepted.status_code == 200, accepted.text
    body = accepted.json()
    assert body["status"] == "accepted"
    assert body["result_id"] is not None
    assert body["decided_by"] == "Admin"


def test_accept_to_contract_proposal_creates_to_contract_and_repoints_the_document(
    logged_in: TestClient,
) -> None:
    customer_id = _customer(logged_in)
    document_id = _document(logged_in, customer_id=customer_id)
    created = logged_in.post("/api/proposals", json=_contract_body(document_id, customer_id))
    proposal_id = created.json()["id"]

    accepted = logged_in.post(f"/api/proposals/{proposal_id}/accept", json={})
    assert accepted.status_code == 200, accepted.text
    contract_id = accepted.json()["result_id"]
    assert contract_id is not None

    contract = logged_in.get(f"/api/contracts/{contract_id}")
    assert contract.status_code == 200, contract.text
    assert contract.json()["title"] == "Consulenza annuale"

    document = logged_in.get(f"/api/documents/{document_id}")
    assert document.status_code == 200, document.text
    assert document.json()["contract_id"] == contract_id


def test_reject_a_proposal_creates_nothing(logged_in: TestClient) -> None:
    customer_id = _customer(logged_in)
    contract_id = _contract(logged_in, customer_id)
    document_id = _document(logged_in, contract_id=contract_id)
    created = logged_in.post("/api/proposals", json=_work_unit_body(document_id, contract_id))
    proposal_id = created.json()["id"]

    rejected = logged_in.post(
        f"/api/proposals/{proposal_id}/reject",
        json={"reason": "date sbagliate"},
    )
    assert rejected.status_code == 200, rejected.text
    body = rejected.json()
    assert body["status"] == "rejected"
    assert body["result_id"] is None


def test_timeline_keeps_the_rejection_reason(logged_in: TestClient) -> None:
    customer_id = _customer(logged_in)
    contract_id = _contract(logged_in, customer_id)
    document_id = _document(logged_in, contract_id=contract_id)
    created = logged_in.post("/api/proposals", json=_work_unit_body(document_id, contract_id))
    proposal_id = created.json()["id"]
    logged_in.post(
        f"/api/proposals/{proposal_id}/reject",
        json={"reason": "date sbagliate"},
    )

    response = logged_in.get(f"/api/proposals/{proposal_id}/timeline")
    assert response.status_code == 200, response.text
    rejected = [row for row in response.json() if row["kind"] == "rejected"]
    assert len(rejected) == 1
    assert rejected[0]["payload"] == {"decided_by": "Admin", "reason": "date sbagliate"}
    assert {row["kind"] for row in response.json()} == {"created", "rejected"}


def test_timeline_of_an_unknown_proposal_is_a_404(logged_in: TestClient) -> None:
    from uuid import uuid4

    response = logged_in.get(f"/api/proposals/{uuid4()}/timeline")
    assert response.status_code == 404


def test_accept_of_an_already_decided_proposal_is_a_409(logged_in: TestClient) -> None:
    customer_id = _customer(logged_in)
    contract_id = _contract(logged_in, customer_id)
    document_id = _document(logged_in, contract_id=contract_id)
    created = logged_in.post("/api/proposals", json=_work_unit_body(document_id, contract_id))
    proposal_id = created.json()["id"]
    logged_in.post(f"/api/proposals/{proposal_id}/reject", json={})

    response = logged_in.post(f"/api/proposals/{proposal_id}/accept", json={})
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "conflict"


def test_list_filters_by_status(logged_in: TestClient) -> None:
    customer_id = _customer(logged_in)
    contract_id = _contract(logged_in, customer_id)
    document_id = _document(logged_in, contract_id=contract_id)
    first = logged_in.post(
        "/api/proposals", json=_work_unit_body(document_id, contract_id, excerpt="before")
    ).json()
    second = logged_in.post(
        "/api/proposals",
        json=_work_unit_body(
            document_id,
            contract_id,
            excerpt="seconda",
            proposed_fields={
                **_work_unit_body(document_id, contract_id)["proposed_fields"],
                "date": "2026-03-06",
            },
        ),
    ).json()
    logged_in.post(f"/api/proposals/{second['id']}/reject", json={})

    response = logged_in.get("/api/proposals", params={"status": "pending"})
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
        body = _work_unit_body(document_id, contract_id, excerpt=day)
        body["proposed_fields"]["date"] = day
        ids.append(logged_in.post("/api/proposals", json=body).json()["id"])
    # The first and the last to arrive are decided, the last one most recently; the middle
    # one is left waiting.
    logged_in.post(f"/api/proposals/{ids[0]}/reject", json={})
    logged_in.post(f"/api/proposals/{ids[2]}/reject", json={})

    arrival = logged_in.get("/api/proposals", params={"status": "rejected"})
    assert [item["id"] for item in arrival.json()["items"]] == [ids[0], ids[2]]

    decided = logged_in.get("/api/proposals", params={"status": "rejected", "order": "decision"})
    assert decided.status_code == 200, decided.text
    assert [item["id"] for item in decided.json()["items"]] == [ids[2], ids[0]]

    capped = logged_in.get(
        "/api/proposals", params={"status": "rejected", "order": "decision", "limit": 1}
    )
    assert [item["id"] for item in capped.json()["items"]] == [ids[2]]

    unknown = logged_in.get("/api/proposals", params={"order": "a-caso"})
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
        "/api/proposals", json=_work_unit_body(document_id, contract_id)
    ).json()["id"]

    response = logged_in.post(
        f"/api/proposals/{proposal_id}/accept", json={"decided_by": "Il Titolare"}
    )

    assert response.status_code == 422, response.text
    assert logged_in.get(f"/api/proposals/{proposal_id}").json()["status"] == "pending"


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
        "/api/proposals", json=_work_unit_body(document_id, contract_id)
    ).json()["id"]
    token = logged_in.post("/api/tokens", json={"name": "Claude"}).json()["token"]
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
    assert logged_in.get(f"/api/proposals/{proposal_id}").json()["status"] == "pending"
