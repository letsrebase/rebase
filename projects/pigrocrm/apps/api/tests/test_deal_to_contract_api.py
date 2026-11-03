"""REB-933 over HTTP: the proposal for converting a won deal, the conversion, and the lock."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.pipeline.service import PipelineService

UPFRONT = {
    "unit": "flat",
    "delivery_period": "one_off",
    "unit_price": "20000.00",
    "start": "2026-02-01",
}
DAY_RATE = {
    "unit": "day",
    "delivery_period": "monthly",
    "unit_price": "600.00",
    "quantity": "4.00",
    "start": "2026-02-01",
    "duration_months": 12,
}
CONFIRMED = {
    "title": "Sito e assistenza",
    "start": "2026-02-01",
    "end": "2027-01-31",
    "renewal_type": "none",
    "termination_notice_days": 30,
    "billing_cadence": "monthly",
    "expense_policy": {"kind": "not_reimbursable"},
}


@pytest.fixture(autouse=True)
def _stages(api_session: Session) -> None:
    PipelineService(api_session).seed_defaults(Actor(id=None, type="system", role="admin"))
    api_session.commit()


def _won_deal(client: TestClient, *lines: dict[str, object]) -> str:
    customer = client.post("/api/customers", json={"legal_name": "ACME Srl"}).json()["id"]
    deal = client.post(
        "/api/deals", json={"name": "Sito", "customer_id": customer, "value_lines": list(lines)}
    )
    assert deal.status_code == 201, deal.text
    stages = client.get("/api/pipeline-stages").json()
    won = next(stage["id"] for stage in stages if stage["type"] == "won")
    moved = client.patch(f"/api/deals/{deal.json()['id']}/stage", json={"stage_id": won})
    assert moved.status_code == 200, moved.text
    return deal.json()["id"]


def test_the_proposal_then_the_conversion_then_the_lock(collaborator_client: TestClient) -> None:
    deal_id = _won_deal(collaborator_client, UPFRONT, DAY_RATE)

    proposal = collaborator_client.get(f"/api/deals/{deal_id}/contract-proposal")
    assert proposal.status_code == 200, proposal.text
    body = proposal.json()
    assert (body["title"], body["start"], body["end"]) == ("Sito", "2026-02-01", "2027-01-31")
    assert [card["type"] for card in body["cards"]] == ["one_off", "daily"]

    created = collaborator_client.post(f"/api/deals/{deal_id}/contract", json=CONFIRMED)
    assert created.status_code == 201, created.text
    contract = created.json()
    assert (contract["status"], contract["deal_id"]) == ("draft", deal_id)

    cards = collaborator_client.get(f"/api/contracts/{contract['id']}/rate-cards").json()
    assert sorted(card["type"] for card in cards) == ["daily", "one_off"]
    assert collaborator_client.get(f"/api/deals/{deal_id}").json()["contract_id"] == contract["id"]

    again = collaborator_client.post(f"/api/deals/{deal_id}/contract", json=CONFIRMED)
    assert again.status_code == 409, again.text
    locked = collaborator_client.patch(
        f"/api/deals/{deal_id}", json={"value_lines": [{**UPFRONT, "unit_price": "1.00"}]}
    )
    assert locked.status_code == 409, locked.text
    assert "Sito e assistenza" in locked.text


def test_a_deal_not_won_is_a_409_and_a_body_missing_the_end_is_a_422(
    collaborator_client: TestClient,
) -> None:
    customer = collaborator_client.post("/api/customers", json={"legal_name": "ACME"}).json()["id"]
    deal = collaborator_client.post(
        "/api/deals", json={"name": "Aperto", "customer_id": customer, "value_lines": [UPFRONT]}
    ).json()

    refused = collaborator_client.post(f"/api/deals/{deal['id']}/contract", json=CONFIRMED)
    assert refused.status_code == 409, refused.text

    won = _won_deal(collaborator_client, UPFRONT)
    without_end = {key: value for key, value in CONFIRMED.items() if key != "end"}
    missing = collaborator_client.post(f"/api/deals/{won}/contract", json=without_end)
    assert missing.status_code == 422, missing.text
