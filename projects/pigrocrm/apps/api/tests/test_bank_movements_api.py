"""Matching a bank movement to the due item it pays over REST (REB-1042, cash design § 3): the
statement's import proposes, an administrator confirms and undoes, a token only proposes, and
everything answers 404 while the `cash` module is off. The ranking and the settlement are proven
in `packages/core/tests/test_bank_matching.py`."""

import datetime as dt
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pigrocrm.core.due_items.schemas import DueItemCreate
from pigrocrm.core.due_items.service import DueItemService
from pigrocrm.core.legal_entities.repository import LegalEntityRepository
from pigrocrm.core.organizations.models import Organization
from pigrocrm_api.deps import invalidate_space_settings

IBAN = "IT60X0542811101000000123456"
CAMT = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "core"
    / "tests"
    / "fixtures"
    / "camt053"
    / "statement_001_02.xml"
)
MISSING = "00000000-0000-0000-0000-000000000000"


def _invoice_item(session: Session, amount: str = "1220.00") -> str:
    """A due item for a customer named like the statement's payer, as an invoice's emission
    writes it."""
    entity = LegalEntityRepository(session).default()
    assert entity is not None
    customer = Organization(legal_name="Acme S.r.l.", legal_entity_id=entity.id)
    session.add(customer)
    session.flush()
    item = DueItemService(session).create(
        DueItemCreate(
            legal_entity_id=entity.id,
            direction="in",
            organization_id=customer.id,
            amount=Decimal(amount),
            source_kind="sales_invoice",
            source_id=uuid4(),
            own_date=dt.date(2026, 10, 20),
        )
    )
    session.commit()
    return str(item.id)


def _world(client: TestClient, session: Session) -> dict[str, str]:
    """An account, an item and the imported statement: the movement of 1220.00 from Acme."""
    account = client.post(
        "/api/bank-accounts",
        json={
            "name": "Main",
            "bank": "Banco BPM",
            "iban": IBAN,
            "opening_balance": "1000.00",
            "opening_balance_date": "2026-09-30",
        },
    ).json()
    item = _invoice_item(session)
    uploaded = client.post(
        f"/api/bank-accounts/{account['id']}/statements",
        files={"file": ("estratto.xml", CAMT.read_bytes(), "application/octet-stream")},
    )
    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json()["proposed"] == 1
    movements = client.get(f"/api/bank-accounts/{account['id']}/movements").json()["items"]
    movement = next(m for m in movements if m["amount"] == "1220.00")
    return {"account": account["id"], "item": item, "movement": movement["id"]}


def _bearer(client: TestClient, **body: Any) -> TestClient:
    made = client.post("/api/tokens", json={"name": "agente", **body})
    assert made.status_code == 201, made.text
    agent = TestClient(client.app, base_url="https://testserver")
    agent.headers["Authorization"] = f"Bearer {made.json()['token']}"
    return agent


def test_the_import_proposes_the_item_and_a_person_confirms_and_undoes_it(
    logged_in: TestClient, api_session: Session
) -> None:
    world = _world(logged_in, api_session)
    movement, item = world["movement"], world["item"]

    detail = logged_in.get(f"/api/bank-movements/{movement}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    match = body["movement"]["match"]
    assert match["state"] == "proposed" and match["proposed_by_type"] == "system"
    assert [row["due_item_id"] for row in match["items"]] == [item]
    assert {"exact_amount", "counterparty_name"} <= set(match["reasons"])
    assert body["candidates"][0]["items"][0]["due_item_id"] == item
    assert body["candidates"][0]["difference"] == "0.00"
    # The queue says the same.
    listed = logged_in.get(f"/api/bank-accounts/{world['account']}/movements").json()["items"]
    assert next(m for m in listed if m["id"] == movement)["match"]["state"] == "proposed"

    preview = logged_in.post(
        f"/api/bank-movements/{movement}/match-preview", json={"due_item_ids": [item]}
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["can_confirm"] is True and preview.json()["difference"] == "0.00"

    confirmed = logged_in.post(
        f"/api/bank-movements/{movement}/match", json={"due_item_ids": [item]}
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["movement"]["state"] == "matched"
    paid = logged_in.get(f"/api/due-items/{item}").json()
    assert paid["state"] == "settled"
    assert paid["settlements"][0]["bank_movement_id"] == movement
    # The payment a movement proves is not reversed from the item's side.
    side = logged_in.post(
        f"/api/due-items/settlements/{paid['settlements'][0]['id']}/reverse", json={}
    )
    assert side.status_code == 409, side.text

    undone = logged_in.post(f"/api/bank-movements/{movement}/unmatch", json={"reason": "errore"})
    assert undone.status_code == 200, undone.text
    assert undone.json()["movement"]["state"] == "unmatched"
    assert [m["state"] for m in undone.json()["history"]] == ["undone"]
    assert logged_in.get(f"/api/due-items/{item}").json()["state"] == "expected"
    again = logged_in.post(f"/api/bank-movements/{movement}/unmatch", json={})
    assert again.status_code == 409


def test_a_selection_that_does_not_cover_the_movement_is_a_422_and_open_items_are_searched(
    logged_in: TestClient, api_session: Session
) -> None:
    world = _world(logged_in, api_session)
    movement, item = world["movement"], world["item"]
    small = _invoice_item(api_session, "100.00")

    refused = logged_in.post(
        f"/api/bank-movements/{movement}/match", json={"due_item_ids": [small]}
    )
    assert refused.status_code == 422, refused.text
    assert logged_in.get(f"/api/due-items/{small}").json()["state"] == "expected"
    unknown = logged_in.post(
        f"/api/bank-movements/{movement}/match", json={"due_item_ids": [MISSING]}
    )
    assert unknown.status_code == 404
    duplicate = logged_in.post(
        f"/api/bank-movements/{movement}/match-preview", json={"due_item_ids": [item, item]}
    )
    assert duplicate.status_code == 422
    assert (
        logged_in.post(
            f"/api/bank-movements/{movement}/match", json={"due_item_ids": []}
        ).status_code
        == 422
    )

    found = logged_in.get(f"/api/bank-movements/{movement}/open-items", params={"search": "acme"})
    assert found.status_code == 200, found.text
    assert {row["due_item_id"] for row in found.json()} == {item, small}
    assert (
        logged_in.get(
            f"/api/bank-movements/{movement}/open-items", params={"search": "nobody"}
        ).json()
        == []
    )
    assert logged_in.get(f"/api/bank-movements/{MISSING}").status_code == 404


def test_a_token_proposes_and_reads_and_never_confirms_or_undoes(
    logged_in: TestClient, api_session: Session
) -> None:
    invalidate_space_settings(None)
    assert logged_in.put("/api/settings/space", json={"mcp_full_access": True}).status_code == 200
    world = _world(logged_in, api_session)
    movement, item = world["movement"], world["item"]
    plain = _bearer(logged_in)
    full = _bearer(logged_in, full_access=True)

    assert plain.get(f"/api/bank-movements/{movement}").json()["code"] == "agent_forbidden"
    proposal = full.post(
        f"/api/bank-movements/{movement}/match-proposal",
        json={"due_item_ids": [item], "note": "stesso importo"},
    )
    assert proposal.status_code == 200, proposal.text
    match = proposal.json()["movement"]["match"]
    assert match["proposed_by_type"] == "mcp" and match["note"] == "stesso importo"
    assert full.get(f"/api/bank-movements/{movement}").status_code == 200
    for path, body in (("match", {"due_item_ids": [item]}), ("unmatch", {})):
        for agent in (plain, full):
            response = agent.post(f"/api/bank-movements/{movement}/{path}", json=body)
            assert response.status_code == 403, (path, response.text)
            assert response.json()["code"] == "agent_forbidden"
    assert logged_in.get(f"/api/due-items/{item}").json()["state"] == "expected"


def _every_route(movement: str) -> list[tuple[str, str, dict[str, Any] | None]]:
    ids = {"due_item_ids": [MISSING]}
    base = f"/api/bank-movements/{movement}"
    return [
        ("GET", base, None),
        ("GET", f"{base}/open-items", None),
        ("POST", f"{base}/match-preview", ids),
        ("POST", f"{base}/match-proposal", ids),
        ("POST", f"{base}/match", ids),
        ("POST", f"{base}/unmatch", {}),
    ]


def test_a_collaborator_and_a_readonly_member_are_refused_every_route(
    collaborator_client: TestClient, readonly_client: TestClient
) -> None:
    for client in (collaborator_client, readonly_client):
        for method, path, body in _every_route(MISSING):
            response = client.request(method, path, json=body)
            assert response.status_code == 403, (method, path, response.text)
            assert response.json()["code"] == "permission_denied"


def test_a_space_with_the_cash_module_off_answers_404(
    logged_in: TestClient, api_session: Session
) -> None:
    world = _world(logged_in, api_session)
    off = logged_in.put("/api/settings/modules", json={"off": ["cash"], "confirm_off": ["cash"]})
    assert off.status_code == 200, off.text
    for method, path, body in _every_route(world["movement"]):
        response = logged_in.request(method, path, json=body)
        assert response.status_code == 404, (method, path, response.text)
        assert response.json()["code"] == "module_off"
