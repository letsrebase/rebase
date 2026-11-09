"""Due items over REST (REB-967): reading, settling in instalments, undoing, and who may do
what to an incoming, an outgoing and a fiscal item, for a person and for a token."""

import datetime as dt
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pigrocrm.core.customers.models import Customer
from pigrocrm.core.due_items.schemas import DueItemCreate
from pigrocrm.core.due_items.service import DueItemService
from pigrocrm.core.legal_entities.repository import LegalEntityRepository
from pigrocrm_api.deps import invalidate_space_settings

D = dt.date


def _customer(session: Session) -> tuple[str, str]:
    """A customer written by hand: a reader's session cannot create one over the wire."""
    entity = LegalEntityRepository(session).default()
    assert entity is not None
    customer = Customer(legal_name="ACME", legal_entity_id=entity.id)
    session.add(customer)
    session.flush()
    return str(customer.id), str(entity.id)


def _item(session: Session, **overrides: Any) -> str:
    """A due item written the way a source's service writes it: through `DueItemService`,
    since nothing over the wire lets a person type one."""
    customer_id, entity_id = _customer(session)
    values: dict[str, Any] = {
        "legal_entity_id": UUID(entity_id),
        "direction": "in",
        "organization_id": UUID(customer_id),
        "amount": Decimal("100.00"),
        "source_kind": "sales_invoice",
        "source_id": uuid4(),
        "own_date": D(2026, 2, 28),
    }
    values.update(overrides)
    item = DueItemService(session).create(DueItemCreate(**values))
    session.commit()
    return str(item.id)


def _fiscal_item(session: Session, **overrides: Any) -> str:
    _, entity_id = _customer(session)
    values: dict[str, Any] = {
        "legal_entity_id": UUID(entity_id),
        "direction": "out",
        "counterparty_type": "tax_office",
        "tax": "vat",
        "amount": Decimal("50.00"),
        "source_kind": "iva_settlement",
        "own_date": D(2026, 3, 16),
    }
    values.update(overrides)
    item = DueItemService(session).create(DueItemCreate(**values))
    session.commit()
    return str(item.id)


def _pay(client: TestClient, item_id: str, amount: str, on: str = "2026-03-01") -> Any:
    return client.post(
        f"/api/due-items/{item_id}/settlements", json={"amount": amount, "settled_on": on}
    )


def test_an_item_settled_in_two_instalments_reads_settled_with_both(
    logged_in: TestClient, api_session: Session
) -> None:
    """The card's «done when», over the wire."""
    item_id = _item(api_session)

    first = _pay(logged_in, item_id, "40.00", "2026-03-01")
    assert first.status_code == 201, first.text
    assert (first.json()["state"], first.json()["remaining"]) == ("expected", "60.00")

    second = _pay(logged_in, item_id, "60.00", "2026-03-20")
    assert second.status_code == 201, second.text

    read = logged_in.get(f"/api/due-items/{item_id}").json()
    assert (read["state"], read["settled"], read["remaining"]) == ("settled", "100.00", "0.00")
    assert [(s["amount"], s["settled_on"]) for s in read["settlements"]] == [
        ("40.00", "2026-03-01"),
        ("60.00", "2026-03-20"),
    ]
    assert read["organization_name"] == "ACME"


def test_a_payment_beyond_the_remainder_is_a_conflict_and_changes_nothing(
    logged_in: TestClient, api_session: Session
) -> None:
    item_id = _item(api_session)
    assert _pay(logged_in, item_id, "70.00").status_code == 201
    over = _pay(logged_in, item_id, "30.01", "2026-03-02")
    assert over.status_code == 409, over.text
    assert logged_in.get(f"/api/due-items/{item_id}").json()["remaining"] == "30.00"
    assert _pay(logged_in, item_id, "30.00", "2026-03-02").status_code == 201


def test_a_payment_can_be_reversed_and_the_row_stays(
    logged_in: TestClient, api_session: Session
) -> None:
    item_id = _item(api_session)
    paid = _pay(logged_in, item_id, "100.00").json()
    settlement_id = paid["settlements"][0]["id"]

    reversed_ = logged_in.post(
        f"/api/due-items/settlements/{settlement_id}/reverse", json={"reason": "wrong account"}
    )
    assert reversed_.status_code == 200, reversed_.text
    body = reversed_.json()
    assert (body["state"], body["remaining"]) == ("expected", "100.00")
    assert body["settlements"][0]["reversed_at"] is not None
    assert body["settlements"][0]["reversal_reason"] == "wrong account"
    again = logged_in.post(f"/api/due-items/settlements/{settlement_id}/reverse", json={})
    assert again.status_code == 409


def test_an_unknown_item_or_settlement_is_a_404(logged_in: TestClient) -> None:
    assert logged_in.get(f"/api/due-items/{uuid4()}").status_code == 404
    assert _pay(logged_in, str(uuid4()), "1.00").status_code == 404
    assert (
        logged_in.post(f"/api/due-items/settlements/{uuid4()}/reverse", json={}).status_code == 404
    )


def test_the_list_filters_pages_and_validates(logged_in: TestClient, api_session: Session) -> None:
    first = _item(api_session, own_date=D(2026, 1, 5))
    second = _item(api_session, own_date=D(2026, 9, 1))
    outgoing = _item(api_session, direction="out", source_kind="purchase")

    page = logged_in.get("/api/due-items", params={"direction": "in", "limit": 1}).json()
    assert [i["id"] for i in page["items"]] == [first]
    assert page["next_cursor"] is not None
    rest = logged_in.get(
        "/api/due-items", params={"direction": "in", "limit": 5, "cursor": page["next_cursor"]}
    ).json()
    assert [i["id"] for i in rest["items"]] == [second]
    assert rest["next_cursor"] is None
    out = logged_in.get("/api/due-items", params={"direction": "out"}).json()
    assert [i["id"] for i in out["items"]] == [outgoing]
    window = logged_in.get(
        "/api/due-items", params={"due_from": "2026-01-01", "due_to": "2026-01-31"}
    ).json()
    assert [i["id"] for i in window["items"]] == [first]

    assert logged_in.get("/api/due-items", params={"limit": 0}).status_code == 422
    assert logged_in.get("/api/due-items", params={"limit": 201}).status_code == 422
    assert logged_in.get("/api/due-items", params={"state": "paid"}).status_code == 422
    assert logged_in.get("/api/due-items", params={"cursor": str(uuid4())}).status_code == 422


# --- people -------------------------------------------------------------------------------


def test_a_collaborator_settles_a_clients_payment_and_not_money_going_out(
    collaborator_client: TestClient, api_session: Session
) -> None:
    incoming = _item(api_session)
    outgoing = _item(api_session, direction="out", source_kind="purchase")
    assert _pay(collaborator_client, incoming, "10.00").status_code == 201
    refused = _pay(collaborator_client, outgoing, "10.00")
    assert refused.status_code == 403, refused.text
    assert refused.json()["code"] == "permission_denied"
    assert collaborator_client.get(f"/api/due-items/{outgoing}").json()["settlements"] == []


def test_a_reader_reads_and_settles_nothing(
    readonly_client: TestClient, api_session: Session
) -> None:
    incoming = _item(api_session)
    assert readonly_client.get(f"/api/due-items/{incoming}").status_code == 200
    refused = _pay(readonly_client, incoming, "10.00")
    assert refused.status_code == 403
    assert refused.json()["code"] == "permission_denied"


def test_the_fiscal_kinds_are_an_administrators_to_read_and_to_settle(
    collaborator_client: TestClient, api_session: Session
) -> None:
    ordinary = _item(api_session)
    tax = _fiscal_item(api_session)
    listed = {i["id"] for i in collaborator_client.get("/api/due-items").json()["items"]}
    assert ordinary in listed and tax not in listed
    assert collaborator_client.get(f"/api/due-items/{tax}").status_code == 403
    assert (
        collaborator_client.get("/api/due-items", params={"source_kind": "iva_settlement"})
    ).status_code == 403
    assert _pay(collaborator_client, tax, "10.00").status_code == 403


def test_an_administrator_reads_and_settles_the_fiscal_kinds(
    logged_in: TestClient, api_session: Session
) -> None:
    tax = _fiscal_item(api_session)
    listed = {i["id"] for i in logged_in.get("/api/due-items").json()["items"]}
    assert tax in listed
    assert _pay(logged_in, tax, "50.00").status_code == 201
    assert logged_in.get(f"/api/due-items/{tax}").json()["state"] == "settled"


# --- tokens -------------------------------------------------------------------------------


@pytest.fixture
def open_space(logged_in: TestClient) -> TestClient:
    invalidate_space_settings(None)
    response = logged_in.put("/api/settings/space", json={"mcp_full_access": True})
    assert response.status_code == 200, response.text
    return logged_in


def _bearer(client: TestClient, raw: str) -> TestClient:
    agent = TestClient(client.app, base_url="https://testserver")
    agent.headers["Authorization"] = f"Bearer {raw}"
    return agent


def _token(client: TestClient, name: str, **body: Any) -> str:
    response = client.post("/api/tokens", json={"name": name, **body})
    assert response.status_code == 201, response.text
    return str(response.json()["token"])


def test_a_token_without_the_right_permission_is_refused_on_an_outgoing_item(
    open_space: TestClient, api_session: Session
) -> None:
    """The card's «done when»: the same administrator, two tokens. A client's payment is
    open to both; money going out is `agent_forbidden` for the one without `full_access`."""
    incoming = _item(api_session)
    outgoing = _item(api_session, direction="out", source_kind="purchase")
    plain = _bearer(open_space, _token(open_space, "chiacchiere"))
    full = _bearer(open_space, _token(open_space, "pagamenti", full_access=True))

    assert _pay(plain, incoming, "10.00").status_code == 201
    refused = _pay(plain, outgoing, "10.00")
    assert refused.status_code == 403, refused.text
    assert refused.json()["code"] == "agent_forbidden"
    assert open_space.get(f"/api/due-items/{outgoing}").json()["settlements"] == []

    allowed = _pay(full, outgoing, "10.00")
    assert allowed.status_code == 201, allowed.text
    settlement_id = allowed.json()["settlements"][0]["id"]
    assert allowed.json()["settlements"][0]["recorded_by_type"] == "mcp"

    undo_refused = plain.post(f"/api/due-items/settlements/{settlement_id}/reverse", json={})
    assert undo_refused.status_code == 403
    assert undo_refused.json()["code"] == "agent_forbidden"
    undo = full.post(f"/api/due-items/settlements/{settlement_id}/reverse", json={})
    assert undo.status_code == 200, undo.text


def test_a_token_reads_the_ordinary_kinds_and_the_fiscal_ones_only_with_full_access(
    open_space: TestClient, api_session: Session
) -> None:
    ordinary = _item(api_session)
    tax = _fiscal_item(api_session)
    plain = _bearer(open_space, _token(open_space, "chiacchiere"))
    full = _bearer(open_space, _token(open_space, "fisco", full_access=True))

    plain_ids = {i["id"] for i in plain.get("/api/due-items").json()["items"]}
    assert ordinary in plain_ids and tax not in plain_ids
    assert plain.get(f"/api/due-items/{tax}").json()["code"] == "agent_forbidden"
    full_ids = {i["id"] for i in full.get("/api/due-items").json()["items"]}
    assert {ordinary, tax} <= full_ids
    assert full.get(f"/api/due-items/{tax}").status_code == 200
