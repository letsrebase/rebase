"""The space's profile and modules over HTTP (spec 2026-11-09, B1, B2 and B8, REB-953).

Three things are held here: the guard (a route of a module that is off answers 404 with the
sentence naming it, and the records are as they were when it is on again), the two writes
(an unscoped admin's, an agent's only with a full-access token, and the profile change shows
its diff before it applies anything) and the dependency between modules.
"""

from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from legal_entities_helpers import legal_entity_url
from sqlalchemy import Engine
from sqlalchemy.orm import Session
from test_analytics_api import PERIOD, YEAR, _log_hours, _seed_deal_and_user

from pigrocrm.core.actor import Actor
from pigrocrm.core.db import session_factory
from pigrocrm.core.pipeline.service import PipelineService
from pigrocrm.core.space_settings import SpaceSetting
from pigrocrm_api.deps import get_snapshot_session, invalidate_space_settings

SENTENCE = "Il modulo Deal è spento: un amministratore può accenderlo in Impostazioni → Moduli"


@pytest.fixture(autouse=True)
def _stages(api_session: Session) -> None:
    PipelineService(api_session).seed_defaults(Actor(id=None, type="system", role="admin"))
    api_session.commit()
    invalidate_space_settings(None)


def _by_key(client: TestClient) -> dict[str, dict[str, Any]]:
    response = client.get("/api/settings/modules")
    assert response.status_code == 200, response.text
    return {module["key"]: module for module in response.json()["modules"]}


def _deal(client: TestClient) -> dict[str, Any]:
    customer = client.post("/api/organizations", json={"legal_name": "ACME Srl"})
    assert customer.status_code == 201, customer.text
    created = client.post("/api/deals", json={"name": "Sito", "customer_id": customer.json()["id"]})
    assert created.status_code == 201, created.text
    return created.json()


def _switch(client: TestClient, **body: Any) -> Any:
    return client.put("/api/settings/modules", json=body)


def _agent(client: TestClient, *, full_access: bool, as_collaborator: bool = False) -> TestClient:
    """A bearer client for a token of `client`'s owner, on a space that opened the ceiling."""
    assert client.put("/api/settings/space", json={"mcp_full_access": True}).status_code == 200, (
        "the ceiling must be open for a token to carry full_access"
    )
    made = client.post("/api/tokens", json={"name": "agente", "full_access": full_access})
    assert made.status_code == 201, made.text
    agent = TestClient(client.app, base_url="https://testserver")
    agent.headers["Authorization"] = f"Bearer {made.json()['token']}"
    return agent


# -- defaults ---------------------------------------------------------------------------


def test_a_space_with_no_profile_has_every_module_on(logged_in: TestClient) -> None:
    body = logged_in.get("/api/settings/modules").json()

    assert body["profile"] is None
    assert [m["key"] for m in body["modules"]] == [
        "deals",
        "contracts",
        "time",
        "costs",
        "taxes",
        "staff",
        "collaborators",
    ]
    assert all(m["stored"] is None for m in body["modules"])
    # `collaborators` answers an onboarding question: it reads as off until somebody says so.
    assert {m["key"] for m in body["modules"] if not m["enabled"]} == {"collaborators"}


def test_every_member_reads_the_modules(
    collaborator_client: TestClient, readonly_client: TestClient
) -> None:
    assert collaborator_client.get("/api/settings/modules").status_code == 200
    assert readonly_client.get("/api/settings/modules").status_code == 200


# -- the guard, per surface -------------------------------------------------------------


def test_a_route_of_a_module_that_is_off_answers_404_with_the_sentence_and_data_survives(
    logged_in: TestClient,
) -> None:
    deal = _deal(logged_in)
    assert logged_in.get("/api/deals").status_code == 200

    # `time` needs `deals`, so both go; the deal is a record, so it is named.
    off = _switch(logged_in, off=["time", "deals"], confirm_off=["deals"])
    assert off.status_code == 200, off.text
    assert {m["key"]: m["enabled"] for m in off.json()["modules"]}["deals"] is False

    for path in (
        "/api/deals",
        f"/api/deals/{deal['id']}",
        f"/api/deals/{deal['id']}/contract-proposal",
        "/api/pipeline-stages",
        "/api/time-entries",
        f"/api/deals/{deal['id']}/time-summary",
        "/api/dashboard/sales",
    ):
        response = logged_in.get(path)
        assert response.status_code == 404, (path, response.text)
        problem = response.json()
        assert problem["code"] == "module_off", path
        assert problem["detail"].startswith("Il modulo "), path
        assert problem["detail"].endswith(
            "è spento: un amministratore può accenderlo in Impostazioni → Moduli"
        )
    assert logged_in.get("/api/deals").json()["detail"] == SENTENCE
    # A write is refused as well, not only a read.
    assert (
        logged_in.post("/api/deals", json={"name": "Altro", "customer_id": deal["customer_id"]})
    ).status_code == 404

    on = _switch(logged_in, on=["deals", "time"])
    assert on.status_code == 200, on.text
    again = logged_in.get(f"/api/deals/{deal['id']}")
    assert again.status_code == 200, again.text
    assert again.json()["name"] == "Sito"
    assert logged_in.get("/api/deals").json()["items"][0]["id"] == deal["id"]


def test_a_stale_credential_is_still_a_401_not_a_leak_of_which_modules_are_on(
    logged_in: TestClient,
) -> None:
    assert _switch(logged_in, off=["time", "deals"]).status_code == 200

    anonymous = TestClient(logged_in.app, base_url="https://testserver")

    assert anonymous.get("/api/deals").status_code == 401


@pytest.mark.parametrize(
    ("module", "paths"),
    [
        ("contracts", ["/api/contracts", "/api/work-units", "/api/analytics/days"]),
        ("costs", ["/api/costs", "/api/cost-categories"]),
        ("taxes", ["/api/analytics/fiscal", "/api/analytics/ceilings", "/api/analytics/year"]),
    ],
)
def test_each_module_owns_its_routes(logged_in: TestClient, module: str, paths: list[str]) -> None:
    assert _switch(logged_in, off=[module]).status_code == 200

    for path in paths:
        response = logged_in.get(path, params={"year": 2026, "date_from": "2026-01-01"})
        assert response.status_code == 404, (path, response.text)
        assert response.json()["code"] == "module_off", path


def test_a_route_across_modules_exists_only_with_all_of_them_on(logged_in: TestClient) -> None:
    deal = _deal(logged_in)
    assert _switch(logged_in, off=["contracts"]).status_code == 200

    # The deal's own pages stay; the conversion to a contract needs both.
    assert logged_in.get(f"/api/deals/{deal['id']}").status_code == 200
    proposal = logged_in.get(f"/api/deals/{deal['id']}/contract-proposal")
    assert proposal.status_code == 404
    assert proposal.json()["code"] == "module_off"
    assert logged_in.post(f"/api/deals/{deal['id']}/contract", json={}).status_code == 404
    # P&L of a deal needs deals, time and costs.
    assert _switch(logged_in, on=["contracts"], off=["costs"]).status_code == 200
    assert logged_in.get(f"/api/deals/{deal['id']}/pnl").status_code == 404
    assert _switch(logged_in, on=["costs"]).status_code == 200
    assert logged_in.get(f"/api/deals/{deal['id']}/pnl").status_code == 200


def test_search_leaves_the_deal_group_empty_when_deals_are_off(logged_in: TestClient) -> None:
    _deal(logged_in)
    deals = lambda: next(  # noqa: E731
        g
        for g in logged_in.get("/api/search", params={"q": "Sito"}).json()["groups"]
        if g["entity"] == "deal"
    )
    assert deals()["total"] == 1

    assert _switch(logged_in, off=["time", "deals"], confirm_off=["deals"]).status_code == 200

    assert deals() == {"entity": "deal", "hits": [], "total": 0, "total_is_minimum": False}


# -- the writes -------------------------------------------------------------------------


def test_a_module_with_records_is_switched_off_only_when_the_call_names_it(
    logged_in: TestClient,
) -> None:
    _deal(logged_in)

    refused = _switch(logged_in, off=["time", "deals"])

    assert refused.status_code == 409, refused.text
    assert "Deal (1 record)" in refused.json()["detail"]
    assert _by_key(logged_in)["deals"]["enabled"] is True
    assert _by_key(logged_in)["deals"]["records"] == 1
    # A module that holds nothing needs no confirmation.
    assert _switch(logged_in, off=["costs"]).status_code == 200


def test_a_module_cannot_be_on_while_one_it_needs_is_off(logged_in: TestClient) -> None:
    assert _switch(logged_in, off=["time", "deals"]).status_code == 200
    refused_on = _switch(logged_in, on=["time"])
    assert refused_on.status_code == 422, refused_on.text
    assert _by_key(logged_in)["time"]["enabled"] is False


def test_switching_a_module_off_lists_the_ones_that_need_it_and_goes_through_when_named(
    logged_in: TestClient,
) -> None:
    """Spec A2: `deals` off with `time` on lists `time` in the same confirmation. The answer
    names it, nothing is written, and the call that names both is the one that applies."""
    refused = _switch(logged_in, off=["deals"])

    assert refused.status_code == 409, refused.text
    assert refused.json()["modules"] == ["time"]
    assert "Ore" in refused.json()["detail"]
    assert _by_key(logged_in)["deals"]["enabled"] is True
    assert _by_key(logged_in)["time"]["enabled"] is True

    applied = _switch(logged_in, off=["deals", "time"])
    assert applied.status_code == 200, applied.text
    assert {k: m["enabled"] for k, m in _by_key(logged_in).items()}["deals"] is False


def test_describing_the_modules_counts_every_table_in_one_statement(
    logged_in: TestClient, api_session: Session
) -> None:
    from sqlalchemy import event

    from pigrocrm.core.config import get_settings
    from pigrocrm.core.modules.service import SpaceModulesService

    engine = api_session.get_bind()
    selects: list[str] = []

    def record(_c: Any, _cur: Any, statement: str, *_a: Any) -> None:
        if statement.lstrip().upper().startswith("SELECT") and "count(" in statement.lower():
            selects.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        SpaceModulesService(api_session, get_settings()).describe()
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert len(selects) == 1


def test_the_ceiling_simulator_needs_deals_only_for_a_saved_deal_and_contracts_for_a_cap(
    logged_in: TestClient,
) -> None:
    assert _switch(logged_in, off=["time", "deals"]).status_code == 200
    url = "/api/analytics/ceilings/simulate"
    # The raw figures are taxes' alone: whatever else the answer is, it is not a module refusal.
    raw = logged_in.get(url, params={"year": YEAR, "budgeted_value": "1000.00"})
    assert raw.json().get("code") != "module_off", raw.text

    saved = logged_in.get(
        url, params={"year": YEAR, "deal_id": "00000000-0000-0000-0000-000000000001"}
    )
    assert saved.status_code == 404
    assert saved.json()["code"] == "module_off"

    assert _switch(logged_in, off=["contracts"]).status_code == 200
    cap = logged_in.get(
        url,
        params={
            "year": YEAR,
            "budgeted_value": "1000.00",
            "contract_id": "00000000-0000-0000-0000-000000000001",
        },
    )
    assert cap.status_code == 404
    assert cap.json()["code"] == "module_off"


def test_a_contracts_expenses_and_proposals_need_the_contracts_module(
    logged_in: TestClient,
) -> None:
    ghost = "00000000-0000-0000-0000-000000000001"
    assert _switch(logged_in, off=["contracts"]).status_code == 200

    accepted = logged_in.post(f"/api/proposals/{ghost}/accept", json={})
    assert accepted.status_code == 404
    assert accepted.json()["code"] == "module_off"
    filed = logged_in.post("/api/proposals", json={})
    assert filed.status_code == 404
    assert filed.json()["code"] == "module_off"
    # Reading and rejecting the queue stay: they touch nothing of the module's.
    assert logged_in.get("/api/proposals").status_code == 200

    assert _switch(logged_in, on=["contracts"], off=["costs"]).status_code == 200
    expenses = logged_in.get(f"/api/contracts/{ghost}/expenses")
    assert expenses.status_code == 404
    assert expenses.json()["code"] == "module_off"


def test_confirm_off_must_name_a_module_that_is_being_switched_off(logged_in: TestClient) -> None:
    refused = _switch(logged_in, off=["costs"], confirm_off=["deals"])

    assert refused.status_code == 422, refused.text


def test_the_profile_answers_the_diff_and_applies_nothing_without_confirmation(
    logged_in: TestClient,
) -> None:
    _deal(logged_in)

    proposal = logged_in.put("/api/settings/modules/profile", json={"profile": "solo"})

    assert proposal.status_code == 200, proposal.text
    body = proposal.json()
    assert body["applied"] is False
    assert {c["module"]: (c["was"], c["becomes"]) for c in body["changes"]} == {
        "deals": (True, False),
        "time": (True, False),
        "staff": (True, False),
    }
    assert next(c for c in body["changes"] if c["module"] == "deals")["records"] == 1
    after = logged_in.get("/api/settings/modules").json()
    assert after["profile"] is None
    assert all(m["enabled"] for m in after["modules"] if m["key"] != "collaborators")
    assert all(m["stored"] is None for m in after["modules"])


def test_confirming_a_profile_writes_a_row_for_every_module_and_touches_no_record(
    logged_in: TestClient,
) -> None:
    deal = _deal(logged_in)

    applied = logged_in.put(
        "/api/settings/modules/profile", json={"profile": "solo", "confirm": True}
    )

    assert applied.status_code == 200, applied.text
    modules = _by_key(logged_in)
    assert logged_in.get("/api/settings/modules").json()["profile"] == "solo"
    assert {k: m["stored"] for k, m in modules.items()} == {
        "deals": False,
        "contracts": True,
        "time": False,
        "costs": True,
        "taxes": True,
        "staff": False,
        "collaborators": False,
    }
    assert {k: m["enabled"] for k, m in modules.items()} == {
        "deals": False,
        "contracts": True,
        "time": False,
        "costs": True,
        "taxes": True,
        "staff": False,
        "collaborators": False,
    }
    # Switched on again by the admin, the deal is exactly as it was.
    assert _switch(logged_in, on=["deals"]).status_code == 200
    assert logged_in.get(f"/api/deals/{deal['id']}").json()["name"] == "Sito"


def test_an_explicit_switch_outranks_the_profile_default(logged_in: TestClient) -> None:
    logged_in.put("/api/settings/modules/profile", json={"profile": "solo", "confirm": True})
    assert _by_key(logged_in)["deals"]["default"] is False

    assert _switch(logged_in, on=["deals"]).status_code == 200

    deals = _by_key(logged_in)["deals"]
    assert deals["enabled"] is True and deals["default"] is False and deals["stored"] is True
    assert logged_in.get("/api/deals").status_code == 200


def test_the_profile_diff_can_be_edited_and_the_edit_must_respect_dependencies(
    logged_in: TestClient,
) -> None:
    kept = logged_in.put(
        "/api/settings/modules/profile",
        json={"profile": "solo", "confirm": True, "modules": {"deals": True}},
    )
    assert kept.status_code == 200, kept.text
    assert _by_key(logged_in)["deals"]["enabled"] is True

    # `time` on under a profile that turns `deals` off.
    refused = logged_in.put(
        "/api/settings/modules/profile",
        json={"profile": "solo", "confirm": True, "modules": {"time": True}},
    )
    assert refused.status_code == 422, refused.text


def test_the_generic_settings_page_cannot_write_the_profile_or_a_module(
    logged_in: TestClient,
) -> None:
    for body in ({"module_deals": False}, {"space_profile": "solo"}):
        refused = logged_in.put("/api/settings/space", json=body)
        assert refused.status_code == 422, refused.text

    assert logged_in.get("/api/settings/modules").json()["profile"] is None


# -- who may write ----------------------------------------------------------------------


# -- the onboarding (spec 2026-11-09, B3, REB-957) ---------------------------------------


def test_the_onboarding_is_pending_until_skipped_and_done_once_a_profile_is_chosen(
    logged_in: TestClient,
) -> None:
    assert logged_in.get("/api/settings/modules").json()["onboarding"] == "pending"

    skipped = logged_in.post("/api/settings/modules/onboarding/skip")
    assert skipped.status_code == 200, skipped.text
    body = skipped.json()
    assert body["onboarding"] == "skipped" and body["profile"] is None
    # Skipping chooses nothing: every module stays on and no switch is written.
    assert all(m["enabled"] for m in body["modules"] if m["key"] != "collaborators")
    assert all(m["stored"] is None for m in body["modules"])
    assert logged_in.get("/api/settings/modules").json()["onboarding"] == "skipped"

    chosen = logged_in.put(
        "/api/settings/modules/profile", json={"profile": "company", "confirm": True}
    )
    assert chosen.status_code == 200, chosen.text
    assert chosen.json()["state"]["onboarding"] == "done"
    # A later skip changes nothing once there is a profile.
    again = logged_in.post("/api/settings/modules/onboarding/skip")
    assert again.json()["onboarding"] == "done" and again.json()["profile"] == "company"


def test_a_collaborator_cannot_skip_the_onboarding(collaborator_client: TestClient) -> None:
    refused = collaborator_client.post("/api/settings/modules/onboarding/skip")

    assert refused.status_code == 403 and refused.json()["code"] == "permission_denied"


def test_the_onboarding_is_skipped_by_a_full_access_token_and_not_by_one_without(
    logged_in: TestClient,
) -> None:
    closed = _agent(logged_in, full_access=False)
    opened = _agent(logged_in, full_access=True)
    assert closed.post("/api/settings/modules/onboarding/skip").status_code == 403
    assert logged_in.get("/api/settings/modules").json()["onboarding"] == "pending"
    assert opened.post("/api/settings/modules/onboarding/skip").status_code == 200
    assert logged_in.get("/api/settings/modules").json()["onboarding"] == "skipped"


def test_a_collaborator_cannot_write_modules_or_the_profile(
    collaborator_client: TestClient,
) -> None:
    modules = collaborator_client.put("/api/settings/modules", json={"off": ["costs"]})
    profile = collaborator_client.put(
        "/api/settings/modules/profile", json={"profile": "solo", "confirm": True}
    )

    assert modules.status_code == 403 and modules.json()["code"] == "permission_denied"
    assert profile.status_code == 403 and profile.json()["code"] == "permission_denied"


def test_an_admin_token_without_full_access_is_refused_and_one_with_it_is_not(
    logged_in: TestClient,
) -> None:
    closed = _agent(logged_in, full_access=False)
    opened = _agent(logged_in, full_access=True)

    refused = closed.put("/api/settings/modules", json={"off": ["costs"]})
    refused_profile = closed.put(
        "/api/settings/modules/profile", json={"profile": "solo", "confirm": True}
    )
    assert refused.status_code == 403 and refused.json()["code"] == "agent_forbidden"
    assert refused_profile.status_code == 403
    assert refused_profile.json()["code"] == "agent_forbidden"
    assert _by_key(logged_in)["costs"]["enabled"] is True

    allowed = opened.put("/api/settings/modules", json={"off": ["costs"]})
    assert allowed.status_code == 200, allowed.text
    assert _by_key(logged_in)["costs"]["enabled"] is False
    diff = opened.put("/api/settings/modules/profile", json={"profile": "company"})
    assert diff.status_code == 200, diff.text
    assert diff.json()["applied"] is False


def test_a_collaborators_full_access_token_is_still_refused(
    collaborator_client: TestClient, api_session: Session
) -> None:
    # The ceiling is the space's, written the way its admin's page would have.
    api_session.add(SpaceSetting(key="mcp_full_access", value="true"))
    api_session.commit()
    invalidate_space_settings(None)
    made = collaborator_client.post("/api/tokens", json={"name": "c", "full_access": True})
    assert made.status_code == 201, made.text
    agent = TestClient(collaborator_client.app, base_url="https://testserver")
    agent.headers["Authorization"] = f"Bearer {made.json()['token']}"

    refused = agent.put("/api/settings/modules", json={"off": ["costs"]})

    assert refused.status_code == 403, refused.text
    assert refused.json()["code"] == "permission_denied"


def test_every_agent_reads_the_modules_and_the_generic_settings_stay_person_only(
    logged_in: TestClient,
) -> None:
    closed = _agent(logged_in, full_access=False)

    assert closed.get("/api/settings/modules").status_code == 200
    refused = closed.put("/api/settings/space", json={"payment_reminders_grace_days": 9})
    assert refused.status_code == 403
    assert refused.json()["code"] == "agent_forbidden"


# -- what the first review found ----------------------------------------------------------


def test_the_offer_state_and_a_deals_notes_belong_to_the_deals_module(
    logged_in: TestClient,
) -> None:
    deal = _deal(logged_in)
    thread = f"/api/deals/{deal['id']}/notes"
    assert logged_in.get(thread).status_code == 200
    assert _switch(logged_in, off=["time", "deals"], confirm_off=["deals"]).status_code == 200

    for refused in (
        logged_in.get(thread),
        logged_in.post(thread, json={"body": "ciao"}),
        logged_in.post(
            "/api/documents/00000000-0000-0000-0000-000000000001/status", json={"status": "sent"}
        ),
    ):
        assert refused.status_code == 404, refused.text
        assert refused.json()["code"] == "module_off"
    # A customer's own thread is the core's.
    customer = f"/api/organizations/{deal['customer_id']}/notes"
    assert logged_in.get(customer).status_code == 200


# -- what a core report leaves out of a module that is off ---------------------------------


def test_the_economic_overview_carries_no_estimate_while_taxes_is_off(
    logged_in: TestClient,
) -> None:
    profile = logged_in.put(
        legal_entity_url(logged_in, "/fiscal-profile"),
        json={
            "regime_code": "RF19",
            "profitability_coefficient": "67.00",
            "substitute_tax_rate": "5.00",
            "social_security_rate": "26.07",
        },
    )
    assert profile.status_code == 200, profile.text
    params = {"year": YEAR}
    before = logged_in.get("/api/analytics/overview", params=params).json()
    assert before["tax"] is not None and before["projected_tax"] is not None

    assert _switch(logged_in, off=["taxes"]).status_code == 200
    off = logged_in.get("/api/analytics/overview", params=params)

    # The cash is the core's and stays; the fiscal block and the nets that follow are gone.
    assert off.status_code == 200, off.text
    body = off.json()
    assert body["pension_fund"]["year"] == YEAR
    assert (body["tax"], body["projected_tax"]) == (None, None)
    assert (body["actual_net"], body["projected_net"]) == (None, None)

    assert _switch(logged_in, on=["taxes"]).status_code == 200
    assert logged_in.get("/api/analytics/overview", params=params).json()["tax"] is not None


def test_the_period_reports_carry_no_labour_or_hours_while_time_is_off(
    logged_in: TestClient, api_engine: Engine
) -> None:
    # The dashboard reads in a transaction of its own, so it gets its own session here.
    factory = session_factory(api_engine)

    def snapshot() -> Iterator[Session]:
        with factory() as session:
            yield session

    logged_in.app.dependency_overrides[get_snapshot_session] = snapshot
    try:
        _the_period_reports(logged_in)
    finally:
        logged_in.app.dependency_overrides.pop(get_snapshot_session, None)


def _the_period_reports(logged_in: TestClient) -> None:
    deal_id, user_id = _seed_deal_and_user(logged_in)
    _log_hours(logged_in, deal_id, user_id)
    hours = ("billable_uninvoiced_hours",)

    def reports() -> list[dict[str, Any]]:
        pnl = logged_in.get("/api/analytics/pnl", params=PERIOD)
        economic = logged_in.get("/api/dashboard/economic", params=PERIOD)
        assert (pnl.status_code, economic.status_code) == (200, 200), (pnl.text, economic.text)
        return [pnl.json(), economic.json()["pnl"]]

    first, second = reports()
    assert first["in_progress"]["work_cost"] == "100.00"
    assert all(
        report["in_progress"]["work_cost"] is not None
        and all(report[field] is not None for field in hours)
        for report in (first, second)
    )

    assert _switch(logged_in, off=["time"], confirm_off=["time"]).status_code == 200
    for report in reports():
        for column in ("closed", "in_progress"):
            totals = report[column]
            assert totals["work_cost"] is None
            # Without labour the margin is the revenue less the direct costs.
            assert totals["gross_margin"] == str(
                Decimal(totals["revenue"]) - Decimal(totals["direct_costs"])
            )
        assert all(report[field] is None for field in hours)
        assert report["written_entries_overdue"] is None

    assert _switch(logged_in, on=["time"]).status_code == 200
    assert reports()[0]["in_progress"]["work_cost"] == "100.00"


def test_the_reminder_types_and_preferences_leave_out_a_module_that_is_off(
    logged_in: TestClient,
) -> None:
    def listed() -> tuple[set[str], set[str], set[str]]:
        types = {item["key"] for item in logged_in.get("/api/reminders/types").json()}
        read = logged_in.get("/api/reminders/preferences").json()
        return (
            types,
            {item["key"] for item in read["types"]},
            {item["type"] for item in read["effective"]},
        )

    assert logged_in.patch(
        "/api/reminders/preferences/space/deal_stale", json={"enabled": False}
    ).status_code in (200, 204)
    for names in listed():
        assert {"deal_stale", "timer_running_long", "task_due"} <= names

    assert _switch(logged_in, off=["time", "deals"], confirm_off=["deals"]).status_code == 200
    for names in listed():
        assert "deal_stale" not in names and "timer_running_long" not in names
        assert "task_due" in names
    # The space's stored choice for a type that is off is not offered either, and stays.
    assert all(
        row["type"] != "deal_stale"
        for row in logged_in.get("/api/reminders/preferences").json()["space"]
    )

    assert _switch(logged_in, on=["deals", "time"]).status_code == 200
    for names in listed():
        assert "deal_stale" in names


def test_the_backlog_splits_between_time_and_contracts_and_a_gmail_kind_follows_deals(
    logged_in: TestClient,
) -> None:
    deal = _deal(logged_in)
    assert logged_in.get("/api/analytics/backlog").json()["billable_uninvoiced_hours"] is not None

    assert _switch(logged_in, off=["time", "deals"], confirm_off=["deals"]).status_code == 200
    # `time` is off but the route is the core's: the approved contract days are still there.
    backlog = logged_in.get("/api/analytics/backlog")
    assert backlog.status_code == 200, backlog.text
    assert backlog.json()["billable_uninvoiced_hours"] is None
    assert backlog.json()["accrued_value"] == "0.00"

    # The correspondence and the drafts of a deal are the deal's.
    for path in ("/api/gmail/messages", "/api/email-drafts"):
        refused = logged_in.get(path, params={"entity_type": "deal", "entity_id": deal["id"]})
        assert refused.status_code == 404, (path, refused.text)
        assert refused.json()["code"] == "module_off"
    created = logged_in.post(
        "/api/email-drafts",
        json={
            "entity_type": "deal",
            "entity_id": deal["id"],
            "to_addresses": ["a@example.test"],
            "subject": "Ciao",
            "body_markdown": "Ciao",
        },
    )
    assert created.status_code == 404, created.text
    ok = logged_in.get(
        "/api/gmail/messages",
        params={"entity_type": "organization", "entity_id": deal["customer_id"]},
    )
    assert ok.status_code != 404, ok.text
