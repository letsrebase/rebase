"""The space's profile and modules over HTTP (spec 2026-11-09, B1, B2 and B8, REB-953).

Three things are held here: the guard (a route of a module that is off answers 404 with the
sentence naming it, and the records are as they were when it is on again), the two writes
(an unscoped admin's, an agent's only with a full-access token, and the profile change shows
its diff before it applies anything) and the dependency between modules.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.pipeline.service import PipelineService
from pigrocrm.core.space_settings import SpaceSetting
from pigrocrm_api.deps import invalidate_space_settings

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
    customer = client.post("/api/customers", json={"legal_name": "ACME Srl"})
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
    assert [m["key"] for m in body["modules"]] == ["deals", "contracts", "time", "costs", "taxes"]
    assert all(m["enabled"] and m["stored"] is None for m in body["modules"])


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
    refused = _switch(logged_in, off=["deals"])
    assert refused.status_code == 422, refused.text
    assert "richiede Deal" in refused.json()["detail"]

    assert _switch(logged_in, off=["time", "deals"]).status_code == 200
    refused_on = _switch(logged_in, on=["time"])
    assert refused_on.status_code == 422, refused_on.text
    assert _by_key(logged_in)["time"]["enabled"] is False


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
    }
    assert next(c for c in body["changes"] if c["module"] == "deals")["records"] == 1
    after = logged_in.get("/api/settings/modules").json()
    assert after["profile"] is None
    assert all(m["enabled"] and m["stored"] is None for m in after["modules"])


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
    }
    assert {k: m["enabled"] for k, m in modules.items()} == {
        "deals": False,
        "contracts": True,
        "time": False,
        "costs": True,
        "taxes": True,
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
    customer = f"/api/customers/{deal['customer_id']}/notes"
    assert logged_in.get(customer).status_code == 200
