"""REB-1045 over HTTP: a payslip proposal read from the inbox and accepted by a person, and a
payroll month closed. Who sees a payslip item or its proposal, who decides it (a person, an
administrator, never a token) and who closes a month (an administrator, and a `full_access` token).
"""

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from test_inbox_api import PASSWORD, _listed, _login_as, _upload
from test_inbox_classify_api import _pdf
from test_staff_api import _bearer, _employee, _entity, open_space  # noqa: F401

PAYSLIP_FIELDS = {
    "period": "2026-01-15",
    "gross": "2142.86",
    "net": "1607.15",
    "withholding": "321.43",
}


def _staff(client: TestClient, session: Session) -> tuple[str, str]:
    entity = _entity(session, "it-standard", "payslip")
    member = client.post("/api/staff", json=_employee(entity, part_time_percent="100")).json()["id"]
    day = client.put(f"/api/staff/pay-days/{entity}", json={"pay_day": 27, "pay_month_offset": 0})
    assert day.status_code == 200, day.text
    return entity, member


def _propose(client: TestClient, item_id: str, member: str, **overrides: Any) -> Any:
    return client.post(
        "/api/proposals",
        json={
            "inbox_item_id": item_id,
            "target_type": "payslip",
            "excerpt": "Netto in busta 1.607,15",
            "confidence": "0.9",
            "proposed_fields": {"staff_member_id": member, **PAYSLIP_FIELDS, **overrides},
        },
    )


def _month(client: TestClient, entity: str) -> dict[str, Any]:
    response = client.get(f"/api/staff/payroll/{entity}", params={"month": "2026-01-01"})
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def test_a_payslip_from_the_inbox_is_received_then_accepted_by_a_person_in_place_of_the_estimate(
    logged_in: TestClient, api_session: Session
) -> None:
    entity, member = _staff(logged_in, api_session)
    item = _upload(logged_in, ("cedolino.pdf", _pdf("Cedolino di gennaio")))[0]

    created = _propose(logged_in, item["id"], member)
    assert created.status_code == 201, created.text
    proposal = created.json()
    assert (proposal["target_type"], proposal["status"]) == ("payslip", "pending")
    assert proposal["proposed_fields"]["period"] == "2026-01-01"
    assert logged_in.get(f"/api/inbox/items/{item['id']}").json()["kind"] == "payslip"

    # «Ricevuto»: the estimate stands and the month names the proposal waiting.
    row = _month(logged_in, entity)["members"][0]
    assert row["source"] == "estimate" and row["payslip"] is None
    assert row["received"]["proposal_id"] == proposal["id"]
    assert row["received"]["inbox_item_id"] == item["id"]
    assert row["received"]["net"] == "1607.15"

    accepted = logged_in.post(f"/api/proposals/{proposal['id']}/accept", json={})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "accepted"
    row = _month(logged_in, entity)["members"][0]
    assert row["source"] == "payslip" and row["received"] is None
    assert row["payslip"]["gross"] == "2142.86"
    assert row["payslip"]["inbox_item_id"] == item["id"]
    # The item is filed and keeps its file: «Apri il PDF».
    filed = logged_in.get(f"/api/inbox/items/{item['id']}").json()
    assert (filed["state"], filed["has_file"]) == ("filed", True)
    assert logged_in.get(f"/api/inbox/items/{item['id']}/file").status_code == 200


def test_a_rejected_payslip_keeps_the_document_and_the_estimate(
    logged_in: TestClient, api_session: Session
) -> None:
    entity, member = _staff(logged_in, api_session)
    item = _upload(logged_in, ("cedolino.pdf", _pdf("Cedolino di gennaio")))[0]
    proposal = _propose(logged_in, item["id"], member).json()

    rejected = logged_in.post(
        f"/api/proposals/{proposal['id']}/reject", json={"reason": "cifre sbagliate"}
    )

    assert rejected.status_code == 200, rejected.text
    row = _month(logged_in, entity)["members"][0]
    assert row["source"] == "estimate" and row["received"] is None
    kept = logged_in.get(f"/api/inbox/items/{item['id']}").json()
    assert (kept["state"], kept["kind"], kept["has_file"]) == ("new", "payslip", True)
    assert logged_in.get(f"/api/inbox/items/{item['id']}/file").status_code == 200


def test_a_collaborator_neither_sees_nor_decides_a_payslip_item_or_its_proposal(
    logged_in: TestClient, api_session: Session
) -> None:
    entity, member = _staff(logged_in, api_session)
    item = _upload(logged_in, ("cedolino.pdf", _pdf("Cedolino di gennaio")))[0]
    proposal = _propose(logged_in, item["id"], member).json()

    # The same browser, now a collaborator's.
    _login_as(logged_in, api_session, "collaboratore@pigro.it", "collaborator")
    assert _listed(logged_in) == []
    assert logged_in.get(f"/api/inbox/items/{item['id']}").status_code == 404
    assert logged_in.get(f"/api/inbox/items/{item['id']}/file").status_code == 404
    assert logged_in.get("/api/proposals", params={"status": "pending"}).json()["items"] == []
    assert logged_in.get(f"/api/proposals/{proposal['id']}").status_code == 404
    assert logged_in.post(f"/api/proposals/{proposal['id']}/accept", json={}).status_code == 404
    assert logged_in.post(f"/api/proposals/{proposal['id']}/reject", json={}).status_code == 404
    assert (
        logged_in.get(f"/api/staff/payroll/{entity}", params={"month": "2026-01-01"}).status_code
        == 403
    )
    # And a collaborator cannot say a file is a payslip, nor propose one.
    mine = _upload(logged_in, ("altro.pdf", _pdf("Un altro cedolino")))[0]
    said = logged_in.post(f"/api/inbox/items/{mine['id']}/kind", json={"kind": "payslip"})
    assert said.status_code == 403, said.text
    assert _propose(logged_in, mine["id"], member).status_code == 403

    logged_in.post("/api/auth/login", json={"email": "admin@pigro.it", "password": PASSWORD})
    assert logged_in.get(f"/api/proposals/{proposal['id']}").status_code == 200


def test_a_token_proposes_with_full_access_and_never_decides_a_payslip(
    open_space: TestClient,  # noqa: F811
    api_session: Session,
) -> None:
    entity, member = _staff(open_space, api_session)
    item = _upload(open_space, ("cedolino.pdf", _pdf("Cedolino di gennaio")))[0]
    plain = _bearer(open_space)
    full = _bearer(open_space, full_access=True)

    # Without `full_access` a proposal for a payslip is refused.
    refused = _propose(plain, item["id"], member)
    assert refused.status_code == 403, refused.text
    assert refused.json()["code"] == "agent_forbidden"

    created = _propose(full, item["id"], member)
    assert created.status_code == 201, created.text
    proposal_id = created.json()["id"]
    assert full.get(f"/api/proposals/{proposal_id}").status_code == 200
    # Once it is a payslip the item and its proposal are not there for a token without it.
    assert _listed(plain) == []
    assert plain.get(f"/api/inbox/items/{item['id']}").status_code == 404
    assert plain.get(f"/api/proposals/{proposal_id}").status_code == 404
    assert [i["id"] for i in _listed(full)] == [item["id"]]
    for agent in (plain, full):
        decided = agent.post(f"/api/proposals/{proposal_id}/accept", json={})
        assert decided.status_code in (403, 404), decided.text
    assert (
        full.post(f"/api/proposals/{proposal_id}/accept", json={}).json()["code"]
        == "agent_forbidden"
    )
    assert _month(open_space, entity)["members"][0]["payslip"] is None
    assert open_space.post(f"/api/proposals/{proposal_id}/accept", json={}).status_code == 200


def test_an_administrator_closes_a_month_and_a_payslip_for_it_is_a_409(
    logged_in: TestClient, api_session: Session
) -> None:
    entity, member = _staff(logged_in, api_session)
    recorded = logged_in.post(f"/api/staff/{member}/payslips", json={**PAYSLIP_FIELDS}).json()
    close = f"/api/staff/payroll/{entity}/closures"

    closed = logged_in.post(close, json={"month": "2026-01-20"})
    assert closed.status_code == 201, closed.text
    assert closed.json()["month"] == "2026-01-01" and closed.json()["closed_by"] == "Admin"
    month = _month(logged_in, entity)
    assert month["closed"]["month"] == "2026-01-01"
    assert month["members"][0]["payslip"]["id"] == recorded["id"]

    # A recording and a correction of that month are refused, in words the page shows.
    again = logged_in.post(f"/api/staff/{member}/payslips", json={**PAYSLIP_FIELDS})
    assert again.status_code == 409 and "chiuso" in again.json()["detail"], again.text
    body = {"gross": "2400.00", "net": "1700.00", "withholding": "360.00"}
    corrected = logged_in.patch(f"/api/staff/{member}/payslips/{recorded['id']}", json=body)
    assert corrected.status_code == 409 and "chiuso" in corrected.json()["detail"], corrected.text
    assert logged_in.post(close, json={"month": "2026-01-01"}).status_code == 409
    # Another month stays open, and one that has not ended cannot be closed.
    other = logged_in.post(
        f"/api/staff/{member}/payslips", json={**PAYSLIP_FIELDS, "period": "2026-02-01"}
    )
    assert other.status_code == 201, other.text
    assert logged_in.post(close, json={"month": "2999-01-01"}).status_code == 422
    assert logged_in.post(close, json={}).status_code == 422


def test_a_full_access_token_closes_a_month_and_the_others_are_refused(
    open_space: TestClient,  # noqa: F811
    api_session: Session,
) -> None:
    entity, _ = _staff(open_space, api_session)
    close = f"/api/staff/payroll/{entity}/closures"
    plain = _bearer(open_space)
    refused = plain.post(close, json={"month": "2026-01-01"})
    assert refused.status_code == 403 and refused.json()["code"] == "agent_forbidden"
    closed = _bearer(open_space, full_access=True).post(close, json={"month": "2026-01-01"})
    assert closed.status_code == 201, closed.text
