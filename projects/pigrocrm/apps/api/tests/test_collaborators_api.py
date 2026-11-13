"""Engagements with freelancers over REST (REB-976): the module guard, the overlap warning and
the availability by week as an administrator reads them, the states, and who may do what.
"""

from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pigrocrm.core.legal_entities.repository import LegalEntityRepository
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.space_settings import SpaceSetting
from pigrocrm_api.deps import invalidate_space_settings


def _module(session: Session, *, on: bool = True) -> None:
    session.add(SpaceSetting(key="module_collaborators", value="true" if on else "false"))
    session.commit()
    invalidate_space_settings(None)


def _freelancer(session: Session, **overrides: Any) -> Organization:
    entity = LegalEntityRepository(session).default()
    assert entity is not None
    values: dict[str, Any] = {
        "legal_name": f"Ada {uuid4()}",
        "legal_entity_id": entity.id,
        "roles": ["freelancer"],
        "hub_freelancer_id": uuid4(),
    }
    values.update(overrides)
    who = Organization(**values)
    session.add(who)
    session.commit()
    return who


def _body(who: Organization, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "freelancer_id": str(who.id),
        "role": "Backend developer",
        "starts_on": "2026-11-02",
        "ends_on": "2026-11-29",
        "days_per_week": "3",
        "fee": "450.00",
        "fee_unit": "day",
        "payment_rule": "fixed_term",
        "payment_days": 30,
    }
    body.update(overrides)
    return body


def _create(client: TestClient, who: Organization, **overrides: Any) -> dict[str, Any]:
    response = client.post("/api/engagements", json=_body(who, **overrides))
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def _activate(client: TestClient, engagement_id: str) -> None:
    for state in ("awaiting_signature", "active"):
        done = client.post(f"/api/engagements/{engagement_id}/state", json={"state": state})
        assert done.status_code == 200, done.text


def test_every_route_answers_404_with_the_sentence_while_the_module_is_off(
    logged_in: TestClient, api_session: Session
) -> None:
    who = _freelancer(api_session)
    for call in (
        lambda: logged_in.get("/api/engagements"),
        lambda: logged_in.post("/api/engagements", json=_body(who)),
        lambda: logged_in.get(f"/api/engagements/{uuid4()}"),
        lambda: logged_in.get(
            "/api/freelancers/availability", params={"freelancer_id": str(who.id)}
        ),
        lambda: logged_in.put(f"/api/freelancers/{who.id}/tax-data", json={"tax_code": "X"}),
    ):
        response = call()
        assert response.status_code == 404, response.text
        assert response.json()["code"] == "module_off"
        assert "Collaboratori" in response.json()["detail"]


def test_two_overlapping_engagements_raise_the_warning_and_availability_shows_the_days(
    logged_in: TestClient, api_session: Session
) -> None:
    """The card's «done when», over the wire."""
    _module(api_session)
    who = _freelancer(api_session)

    first = _create(logged_in, who)
    assert first["state"] == "draft" and first["warnings"] == []
    second = _create(
        logged_in, who, starts_on="2026-11-16", ends_on="2026-12-13", days_per_week="2"
    )

    assert [w["code"] for w in second["warnings"]] == ["overlap"]
    assert second["warnings"][0]["engagement_ids"] == [first["id"]]
    assert second["warnings"][0]["peak_days_per_week"] == "5.0"
    assert "5 giorni a settimana" in second["warnings"][0]["message"]

    _activate(logged_in, first["id"])
    _activate(logged_in, second["id"])
    answer = logged_in.get(
        "/api/freelancers/availability",
        params={"freelancer_id": str(who.id), "date_from": "2026-11-02", "date_to": "2026-12-06"},
    )
    assert answer.status_code == 200, answer.text
    assert [(w["week"], w["committed_days"], w["status"]) for w in answer.json()["weeks"]] == [
        ("2026-W45", "3.0", "committed"),
        ("2026-W46", "3.0", "committed"),
        ("2026-W47", "5.0", "committed"),
        ("2026-W48", "5.0", "committed"),
        ("2026-W49", "2.0", "committed"),
    ]
    by_hub = logged_in.get(
        "/api/freelancers/availability",
        params={"hub_freelancer_id": str(who.hub_freelancer_id), "date_from": "2026-11-02"},
    )
    assert by_hub.status_code == 200 and len(by_hub.json()["weeks"]) == 12


def test_an_engagement_without_days_per_week_is_sconosciuta_over_the_wire(
    logged_in: TestClient, api_session: Session
) -> None:
    _module(api_session)
    who = _freelancer(api_session)
    made = _create(logged_in, who, ends_on="2026-11-08")
    cleared = logged_in.patch(f"/api/engagements/{made['id']}", json={"days_per_week": None})
    assert cleared.status_code == 200 and cleared.json()["missing"] == ["days_per_week", "client"]
    _activate(logged_in, made["id"])

    week = logged_in.get(
        "/api/freelancers/availability",
        params={"freelancer_id": str(who.id), "date_from": "2026-11-02", "date_to": "2026-11-08"},
    ).json()["weeks"][0]

    assert (week["status"], week["label"], week["free_days"]) == ("unknown", "sconosciuta", None)


def test_validation_answers_in_italian_and_names_the_field(
    logged_in: TestClient, api_session: Session
) -> None:
    _module(api_session)
    who = _freelancer(api_session)
    for overrides, field in (
        ({"days_per_week": "0.7"}, "days_per_week"),
        ({"days_per_week": None}, "days_per_week"),
        ({"payment_days": 61}, "payment_days"),
        (
            {"payment_rule": "on_collection", "payment_days": None, "collection_days": 61},
            "collection_days",
        ),
        ({"payment_rule": "on_collection", "collection_days": 20}, "payment_days"),
        ({"fee": "0"}, "fee"),
        ({"fee_unit": "week"}, "fee_unit"),
    ):
        response = logged_in.post("/api/engagements", json=_body(who, **overrides))
        assert response.status_code == 422, (overrides, response.text)
        assert response.json()["field"] == field, (overrides, response.text)
    refused = logged_in.post(
        "/api/engagements",
        json=_body(_freelancer(api_session, roles=["customer"], hub_freelancer_id=None)),
    )
    assert refused.status_code == 422 and "ruolo di freelance" in refused.text


def test_a_draft_is_updated_and_a_signed_engagement_keeps_its_letter(
    logged_in: TestClient, api_session: Session
) -> None:
    _module(api_session)
    who = _freelancer(api_session)
    made = _create(logged_in, who)

    updated = logged_in.patch(f"/api/engagements/{made['id']}", json={"role": "Tech lead"})
    assert updated.status_code == 200 and updated.json()["role"] == "Tech lead"
    _activate(logged_in, made["id"])
    frozen = logged_in.patch(f"/api/engagements/{made['id']}", json={"fee": "1.00"})
    assert frozen.status_code == 409, frozen.text
    days = logged_in.patch(f"/api/engagements/{made['id']}", json={"days_per_week": "1.5"})
    assert days.status_code == 200 and days.json()["days_per_week"] == "1.5"
    assert logged_in.patch(f"/api/engagements/{uuid4()}", json={"role": "x"}).status_code == 404


def test_the_states_follow_the_table_and_a_close_ends_the_engagement(
    logged_in: TestClient, api_session: Session
) -> None:
    _module(api_session)
    who = _freelancer(api_session)
    made = _create(logged_in, who, ends_on=None)
    url = f"/api/engagements/{made['id']}/state"

    assert logged_in.post(url, json={"state": "active"}).status_code == 422
    assert logged_in.post(url, json={"state": "paid"}).status_code == 422
    _activate(logged_in, made["id"])
    closed = logged_in.post(url, json={"state": "concluded", "ended_on": "2026-12-04"})
    assert closed.status_code == 200, closed.text
    assert (closed.json()["state"], closed.json()["ends_on"]) == ("concluded", "2026-12-04")
    assert logged_in.post(url, json={"state": "cancelled"}).status_code == 422


def test_the_list_filters_by_freelancer_and_state(
    logged_in: TestClient, api_session: Session
) -> None:
    _module(api_session)
    who, other = _freelancer(api_session), _freelancer(api_session)
    mine = _create(logged_in, who)
    _create(logged_in, other)
    logged_in.post(f"/api/engagements/{mine['id']}/state", json={"state": "cancelled"})

    listed = logged_in.get("/api/engagements", params={"freelancer_id": str(who.id)}).json()
    assert [i["id"] for i in listed["items"]] == [mine["id"]]
    cancelled = logged_in.get("/api/engagements", params={"state": "cancelled"}).json()
    assert [i["id"] for i in cancelled["items"]] == [mine["id"]]
    assert logged_in.get("/api/engagements", params={"state": "paid"}).status_code == 422
    assert logged_in.get("/api/engagements", params={"limit": 0}).status_code == 422


def test_the_hub_match_id_creates_one_engagement_and_finds_it_again(
    logged_in: TestClient, api_session: Session
) -> None:
    """REB-1105: «Crea match» mints the id and the CRM's form creates the draft under it, so the
    form opened again from the same link finds that draft and cannot make a second."""
    _module(api_session)
    who = _freelancer(api_session)
    minted = str(uuid4())

    assert logged_in.get("/api/engagements", params={"hub_match_id": minted}).json()["items"] == []
    made = _create(logged_in, who, hub_match_id=minted)
    found = logged_in.get("/api/engagements", params={"hub_match_id": minted}).json()
    assert [i["id"] for i in found["items"]] == [made["id"]]
    assert found["items"][0]["hub_match_id"] == minted
    _create(logged_in, who)
    assert len(logged_in.get("/api/engagements").json()["items"]) == 2
    again = logged_in.post("/api/engagements", json=_body(who, hub_match_id=minted))
    assert again.status_code == 409, again.text
    assert (
        len(logged_in.get("/api/engagements", params={"hub_match_id": minted}).json()["items"]) == 1
    )


def test_the_tax_data_is_saved_on_the_freelancers_organization(
    logged_in: TestClient, api_session: Session
) -> None:
    _module(api_session)
    who = _freelancer(api_session)

    saved = logged_in.put(
        f"/api/freelancers/{who.id}/tax-data",
        json={
            "tax_code": "RSSMRA80A01H501U",
            "domicile": "Via Roma 1",
            "fiscal_kind": "occasional",
        },
    )

    assert saved.status_code == 200, saved.text
    assert (saved.json()["tax_code"], saved.json()["address"], saved.json()["supplier_kind"]) == (
        "RSSMRA80A01H501U",
        "Via Roma 1",
        "occasional",
    )
    assert logged_in.put(f"/api/freelancers/{who.id}/tax-data", json={}).status_code == 422
    assert (
        logged_in.put(
            f"/api/freelancers/{who.id}/tax-data", json={"fiscal_kind": "company"}
        ).status_code
        == 422
    )


@pytest.mark.parametrize("client_name", ["collaborator_client", "readonly_client"])
def test_only_an_administrator_reaches_the_engagements(
    client_name: str, request: pytest.FixtureRequest, api_session: Session
) -> None:
    _module(api_session)
    who = _freelancer(api_session)
    client: TestClient = request.getfixturevalue(client_name)

    for response in (
        client.get("/api/engagements"),
        client.get("/api/engagements/counts"),
        client.get(f"/api/engagements/{uuid4()}/payments"),
        client.post("/api/engagements", json=_body(who)),
        client.get("/api/freelancers/availability", params={"freelancer_id": str(who.id)}),
        client.put(f"/api/freelancers/{who.id}/tax-data", json={"tax_code": "X"}),
    ):
        assert response.status_code == 403, response.text
        assert response.json()["code"] == "permission_denied"


def test_an_agent_token_reads_and_updates_a_draft_and_needs_full_access_for_the_rest(
    logged_in: TestClient, api_session: Session
) -> None:
    _module(api_session)
    who = _freelancer(api_session)
    made = _create(logged_in, who)
    assert logged_in.put("/api/settings/space", json={"mcp_full_access": True}).status_code == 200

    def token(*, full_access: bool) -> TestClient:
        issued = logged_in.post("/api/tokens", json={"name": "agente", "full_access": full_access})
        assert issued.status_code == 201, issued.text
        agent = TestClient(logged_in.app, base_url="https://testserver")
        agent.headers["Authorization"] = f"Bearer {issued.json()['token']}"
        return agent

    plain, full = token(full_access=False), token(full_access=True)
    assert plain.get("/api/engagements").status_code == 200
    assert plain.get(f"/api/engagements/{made['id']}").status_code == 200
    assert plain.patch(f"/api/engagements/{made['id']}", json={"role": "Lead"}).status_code == 200
    availability = plain.get("/api/freelancers/availability", params={"freelancer_id": str(who.id)})
    assert availability.status_code == 200
    for response in (
        plain.post("/api/engagements", json=_body(who)),
        plain.post(f"/api/engagements/{made['id']}/state", json={"state": "cancelled"}),
        plain.put(f"/api/freelancers/{who.id}/tax-data", json={"tax_code": "X"}),
    ):
        assert response.status_code == 403 and response.json()["code"] == "agent_forbidden", (
            response.text
        )
    assert full.post("/api/engagements", json=_body(who)).status_code == 201
    assert (
        full.post(f"/api/engagements/{made['id']}/state", json={"state": "cancelled"}).status_code
        == 200
    )
    # The signature that makes an engagement active is a person's, whatever the token carries.
    sent = _create(logged_in, who)
    assert (
        logged_in.post(f"/api/engagements/{sent['id']}/state", json={"state": "awaiting_signature"})
    ).status_code == 200
    refused = full.post(f"/api/engagements/{sent['id']}/state", json={"state": "active"})
    assert refused.status_code == 403 and refused.json()["code"] == "agent_forbidden"


def test_a_fee_too_long_for_its_column_is_a_422_and_not_a_500(
    logged_in: TestClient, api_session: Session
) -> None:
    _module(api_session)
    who = _freelancer(api_session)
    for overrides in ({"fee": "1" + "0" * 12}, {"fee": "1E+20"}, {"expected_days": "1" + "0" * 8}):
        response = logged_in.post("/api/engagements", json=_body(who, **overrides))
        assert response.status_code == 422, (overrides, response.text)


def test_an_admin_records_and_approves_a_months_monthly_report_and_no_agent_token_can(
    logged_in: TestClient, api_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REB-1002: a month's report is recorded and approved by a person; an agent token, with or
    without `full_access`, reads the list and is refused both writes."""
    _module(api_session)
    who = _freelancer(api_session)
    made = _create(logged_in, who, starts_on="2026-08-01", ends_on="2026-09-30")
    _activate(logged_in, made["id"])
    base = f"/api/engagements/{made['id']}/monthly-reports"
    # Outside the space that holds the engagements nothing is recorded or approved (REB-1034).
    for refused in (
        logged_in.post(base, json={"month": "2026-08", "received_on": "2026-09-01"}),
        logged_in.post(f"{base}/2026-08/approval", json={}),
    ):
        assert refused.status_code == 409, refused.text
        assert "PIGROCRM_LEADS_SPACE" in refused.json()["detail"]
    from pigrocrm.core.collaborators import rules

    monkeypatch.setattr(rules, "is_leads_space", lambda *_: True)
    assert logged_in.put("/api/settings/space", json={"mcp_full_access": True}).status_code == 200

    def token(*, full_access: bool) -> TestClient:
        issued = logged_in.post("/api/tokens", json={"name": "agente", "full_access": full_access})
        assert issued.status_code == 201, issued.text
        agent = TestClient(logged_in.app, base_url="https://testserver")
        agent.headers["Authorization"] = f"Bearer {issued.json()['token']}"
        return agent

    for agent in (token(full_access=False), token(full_access=True)):
        refused = agent.post(base, json={"month": "2026-10"})
        assert refused.status_code == 403 and refused.json()["code"] == "agent_forbidden"
        assert agent.get(base).status_code == 200

    recorded = logged_in.post(base, json={"month": "2026-08", "received_on": "2026-09-01"})
    assert recorded.status_code == 201, recorded.text
    assert (recorded.json()["label"], recorded.json()["tacit_on"]) == ("2026-08", "2026-09-10")
    assert logged_in.post(base, json={"month": "2026-08"}).status_code == 409
    assert logged_in.post(base, json={"month": "2026-9"}).status_code == 422
    # Received today: its tacit date is still ahead, so a part can be held.
    assert logged_in.post(base, json={"month": "2026-09"}).status_code == 201

    for agent in (token(full_access=False), token(full_access=True)):
        refused = agent.post(f"{base}/2026-08/approval", json={})
        assert refused.status_code == 403 and refused.json()["code"] == "agent_forbidden"

    late = logged_in.post(
        f"{base}/2026-08/approval",
        json={"held_dates": ["2026-08-06"], "objection": "Il cliente non li riconosce"},
    )
    # The tacit date (10 September) is past: nothing can be held any more.
    assert late.status_code == 409, late.text
    approved = logged_in.post(f"{base}/2026-08/approval", json={})
    assert approved.status_code == 200, approved.text
    assert (approved.json()["state"], approved.json()["held_dates"]) == ("approved", [])
    held = logged_in.post(
        f"{base}/2026-09/approval",
        json={"held_dates": ["2026-09-07"], "objection": "Il cliente non li riconosce"},
    )
    assert held.status_code == 200, held.text
    assert held.json()["held_dates"] == ["2026-09-07"]
    assert logged_in.post(f"{base}/2026-07/approval", json={}).status_code == 404
    listed = logged_in.get(base).json()
    assert [item["label"] for item in listed["items"]] == ["2026-08", "2026-09"]


def test_the_days_report_is_read_only_from_the_space_that_holds_the_engagements(
    logged_in: TestClient, api_session: Session
) -> None:
    _module(api_session)
    made = _create(logged_in, _freelancer(api_session))
    response = logged_in.get(f"/api/engagements/{made['id']}/report")
    assert response.status_code == 409, response.text
    assert "PIGROCRM_LEADS_SPACE" in response.json()["detail"]
    assert logged_in.get(f"/api/engagements/{uuid4()}/report").status_code in (404, 409)


def test_the_days_report_answers_with_the_days_and_what_is_payable(
    logged_in: TestClient, api_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The route's 200: the door and the leads-space check are stood in for, the rest (the
    engagement, the month's report, the payable marks) is the real service."""
    from datetime import date
    from decimal import Decimal

    import pigrocrm.core.collaborators.days_report as days_report
    from pigrocrm.core.collaborators import rules
    from pigrocrm.core.engagements.schemas import EngagementReport, ReportDeal, ReportWorkUnit

    class Door:
        def __init__(self, *_: Any) -> None: ...

        def report(self, *_: Any) -> EngagementReport:
            return EngagementReport(
                slug="ada",
                deal_url="https://pigro.test/ada/deals/x",
                deal=ReportDeal(
                    id=uuid4(), name="L", hourly_rate=None, budgeted_hours=None, status="ok"
                ),
                days=[],
                total_hours=Decimal(0),
                billed_hours=Decimal(0),
                unbilled_hours=Decimal(0),
                invoices=[],
                work_units=[
                    ReportWorkUnit(
                        date=date(2026, 8, 5),
                        quantity=Decimal("1"),
                        status="worked",
                        description="lavoro",
                        billed=False,
                    )
                ],
            )

    monkeypatch.setattr(days_report, "EngagementService", Door)
    monkeypatch.setattr(rules, "is_leads_space", lambda *_: True)
    monkeypatch.setattr(days_report.DaysReportService, "_check_owner", lambda *_: None)
    _module(api_session)
    made = _create(
        logged_in, _freelancer(api_session), starts_on="2026-08-01", ends_on="2026-08-30"
    )
    _activate(logged_in, made["id"])
    base = f"/api/engagements/{made['id']}"
    before = logged_in.get(f"{base}/report")
    assert before.status_code == 200, before.text
    assert [(d["date"], d["payable"]) for d in before.json()["days"]] == [("2026-08-05", False)]

    assert (
        logged_in.post(
            f"{base}/monthly-reports", json={"month": "2026-08", "received_on": "2026-09-01"}
        ).status_code
        == 201
    )
    after = logged_in.get(f"{base}/report").json()
    assert after["days"][0]["payable"] is True
    assert (after["payable_quantity"], after["payable_amount"]) == ("1", "450.00")


def test_the_list_names_its_people_counts_what_waits_and_filters_by_tab(
    logged_in: TestClient, api_session: Session
) -> None:
    """REB-1035: «Incarichi» draws names, a count for the entry and the three tabs."""
    _module(api_session)
    who = _freelancer(api_session, legal_name="Giulia Martini")
    draft = _create(logged_in, who)
    active = _create(logged_in, who, starts_on="2026-12-07", ends_on="2026-12-20")
    _activate(logged_in, active["id"])

    counts = logged_in.get("/api/engagements/counts")
    assert counts.status_code == 200, counts.text
    # Both lack a client link: a draft and a live engagement missing it wait for a person.
    assert counts.json() == {"todo": 2, "ongoing": 0, "closed": 0}
    todo = logged_in.get("/api/engagements", params={"bucket": "todo"}).json()["items"]
    assert {item["id"] for item in todo} == {draft["id"], active["id"]}
    assert {item["freelancer_name"] for item in todo} == {"Giulia Martini"}
    assert [w["code"] for w in next(i for i in todo if i["id"] == active["id"])["waiting"]] == [
        "missing_client"
    ]
    assert logged_in.get("/api/engagements", params={"bucket": "closed"}).json()["items"] == []
    assert logged_in.get("/api/engagements", params={"bucket": "later"}).status_code == 422


def test_an_engagements_payments_are_read_over_rest_and_unknown_ones_are_404(
    logged_in: TestClient, api_session: Session
) -> None:
    _module(api_session)
    made = _create(logged_in, _freelancer(api_session))

    page = logged_in.get(f"/api/engagements/{made['id']}/payments")
    assert page.status_code == 200, page.text
    assert page.json()["engagement_id"] == made["id"]
    assert logged_in.get(f"/api/engagements/{uuid4()}/payments").status_code == 404


def test_the_availability_strip_can_leave_one_engagement_out(
    logged_in: TestClient, api_session: Session
) -> None:
    _module(api_session)
    who = _freelancer(api_session)
    first = _create(logged_in, who, days_per_week="3")
    second = _create(logged_in, who, days_per_week="1")

    both = logged_in.get(
        "/api/freelancers/availability",
        params={"freelancer_id": str(who.id), "date_from": "2026-11-02", "date_to": "2026-11-08"},
    ).json()
    alone = logged_in.get(
        "/api/freelancers/availability",
        params={
            "freelancer_id": str(who.id),
            "date_from": "2026-11-02",
            "date_to": "2026-11-08",
            "excluding_engagement_id": second["id"],
        },
    ).json()

    assert both["weeks"][0]["committed_days"] == "4.0"
    assert alone["weeks"][0]["committed_days"] == "3.0"
    assert alone["weeks"][0]["engagement_ids"] == [first["id"]]


def test_a_freelancers_own_engagement_is_a_persons_read_of_a_contract_the_door_wrote(
    logged_in: TestClient, api_session: Session
) -> None:
    missing = logged_in.get(f"/api/contracts/{uuid4()}/rebase-engagement")
    assert missing.status_code == 404, missing.text

    issued = logged_in.post("/api/tokens", json={"name": "agente", "full_access": False})
    agent = TestClient(logged_in.app, base_url="https://testserver")
    agent.headers["Authorization"] = f"Bearer {issued.json()['token']}"
    refused = agent.get(f"/api/contracts/{uuid4()}/rebase-engagement")
    assert refused.status_code == 403 and refused.json()["code"] == "agent_forbidden"
