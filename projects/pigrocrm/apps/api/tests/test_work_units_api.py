"""`/api/work-units`: record a day, read it, move it through its lifecycle.

What the router adds to `WorkUnitService` is the wire shape, the status a refusal
becomes (409 for an edge the graph does not allow, 422 for an unbounded list, 404 for a
day that is not there), and the ban: a personal access token is accepted on every REST
route, so the four ledger writes have to refuse an agent credential here and not only at
the service. The state machine itself is `packages/core/tests/test_work_units_*.py`.
"""

from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient

from pigrocrm.core.config import Settings


def _contract(client: TestClient, *, requires_prior_approval: bool = False) -> str:
    customer = client.post("/api/customers", json={"ragione_sociale": "ACME Srl"})
    assert customer.status_code == 201, customer.text
    response = client.post(
        "/api/contracts",
        json={
            "customer_id": customer.json()["id"],
            "titolo": "Consulenza",
            "inizio": "2026-01-01",
            "tipo_rinnovo": "nessuno",
            "preavviso_disdetta_giorni": 30,
            "cadenza_fatturazione": "mensile",
            "politica_spese": {"kind": "non_rimborsabile"},
            "requires_prior_approval": requires_prior_approval,
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _record(client: TestClient, contract_id: str, giorno: str, **extra: Any) -> dict[str, Any]:
    response = client.post(
        "/api/work-units",
        json={
            "contract_id": contract_id,
            "data": giorno,
            "quantita": "0.50",
            "descrizione": "Revisione architettura",
            "stato": "lavorato",
            **extra,
        },
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def test_a_half_day_is_recorded_and_listed_with_its_contract(logged_in: TestClient) -> None:
    contract_id = _contract(logged_in)

    day = _record(logged_in, contract_id, "2026-03-02")

    assert day["stato"] == "lavorato"
    # A string, like every Decimal in this API: the browser reads it digit by digit.
    assert day["quantita"] == "0.50"
    listed = logged_in.get("/api/work-units", params={"contract_id": contract_id})
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["troncato"] is False
    assert [(row["id"], row["contract_titolo"], row["customer_nome"]) for row in body["items"]] == [
        (day["id"], "Consulenza", "ACME Srl")
    ]


def test_a_day_without_the_required_approval_is_flagged_not_refused(
    logged_in: TestClient,
) -> None:
    contract_id = _contract(logged_in, requires_prior_approval=True)

    day = _record(logged_in, contract_id, "2026-03-02")

    assert day["stato"] == "lavorato_senza_approvazione"
    detail = logged_in.get(f"/api/work-units/{day['id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["requires_prior_approval"] is True
    assert detail.json()["approval"] is None


def test_a_second_day_on_the_same_date_is_a_409(logged_in: TestClient) -> None:
    contract_id = _contract(logged_in)
    _record(logged_in, contract_id, "2026-03-02")

    response = logged_in.post(
        "/api/work-units",
        json={
            "contract_id": contract_id,
            "data": "2026-03-02",
            "quantita": "1.00",
            "descrizione": "Altro",
            "stato": "lavorato",
        },
    )

    assert response.status_code == 409, response.text


def test_a_day_is_moved_and_the_page_shows_both_moves_with_their_dates(
    logged_in: TestClient,
) -> None:
    contract_id = _contract(logged_in)
    day = _record(logged_in, contract_id, "2026-03-02", stato="approvato")

    worked = logged_in.post(
        f"/api/work-units/{day['id']}/transitions",
        json={"stato": "lavorato", "motivo": "Fatta"},
    )
    assert worked.status_code == 200, worked.text
    contested = logged_in.post(
        f"/api/work-units/{day['id']}/transitions",
        json={"stato": "contestato", "motivo": "Il cliente contesta le ore"},
    )
    assert contested.status_code == 200, contested.text

    body = contested.json()
    assert body["stato"] == "contestato"
    moves = [(t["stato_precedente"], t["stato_nuovo"], t["motivo"]) for t in body["transizioni"]]
    assert moves == [
        (None, "approvato", "creazione"),
        ("approvato", "lavorato", "Fatta"),
        ("lavorato", "contestato", "Il cliente contesta le ore"),
    ]
    assert all(t["created_at"] for t in body["transizioni"])
    assert body["transizioni"][-1]["attore"]["kind"] == "human"
    assert body["azioni"] == ["lavorato", "revocato", "non_fatturabile"]


def test_an_edge_the_graph_does_not_allow_is_a_409(logged_in: TestClient) -> None:
    contract_id = _contract(logged_in)
    day = _record(logged_in, contract_id, "2026-03-02")

    response = logged_in.post(
        f"/api/work-units/{day['id']}/transitions",
        json={"stato": "approvato", "motivo": "Troppo tardi"},
    )

    assert response.status_code == 409, response.text


def test_a_list_without_a_contract_or_a_date_is_a_422(logged_in: TestClient) -> None:
    response = logged_in.get("/api/work-units")

    assert response.status_code == 422, response.text


def test_the_window_lists_every_contracts_days_between_two_dates(logged_in: TestClient) -> None:
    first = _contract(logged_in)
    second = _contract(logged_in)
    _record(logged_in, first, "2026-03-02")
    _record(logged_in, second, "2026-03-03")
    _record(logged_in, second, "2026-04-03")

    response = logged_in.get("/api/work-units", params={"da": "2026-03-01", "a": "2026-03-31"})

    assert response.status_code == 200, response.text
    assert sorted(row["data"] for row in response.json()["items"]) == ["2026-03-02", "2026-03-03"]


def test_an_unknown_day_is_a_404(logged_in: TestClient) -> None:
    response = logged_in.get("/api/work-units/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404, response.text


def test_the_month_carries_each_days_state_on_its_date(logged_in: TestClient) -> None:
    contract_id = _contract(logged_in, requires_prior_approval=True)
    giorno = date(2026, 3, 2)
    _record(logged_in, contract_id, giorno.isoformat())

    month = logged_in.get("/api/calendar", params={"mese": "2026-03"})

    assert month.status_code == 200, month.text
    day = next(d for d in month.json()["giorni"] if d["giorno"] == giorno.isoformat())
    assert [
        (g["contract_titolo"], g["customer_nome"], g["quantita"], g["stato"])
        for g in day["giornate"]
    ] == [("Consulenza", "ACME Srl", "0.50", "lavorato_senza_approvazione")]


def _agent_client(admin_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A personal access token on an installation that has not opted in, the credential
    an agent is handed (`test_drive_api.py::_agent_client` says why the switch is forced
    off at the one name that reads it)."""
    raw = admin_client.post("/api/tokens", json={"nome": "Claude"})
    assert raw.status_code == 201, raw.text
    monkeypatch.setattr(
        "pigrocrm.core.auth.pat_service.get_settings",
        lambda: Settings(_env_file=None),  # type: ignore[call-arg]
    )
    agent = TestClient(admin_client.app, base_url="https://testserver")
    agent.headers["Authorization"] = f"Bearer {raw.json()['token']}"
    return agent


def test_an_agent_token_cannot_record_or_move_a_day(
    logged_in: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    contract_id = _contract(logged_in)
    day = _record(logged_in, contract_id, "2026-03-02")
    agent = _agent_client(logged_in, monkeypatch)

    recorded = agent.post(
        "/api/work-units",
        json={
            "contract_id": contract_id,
            "data": "2026-03-03",
            "quantita": "1.00",
            "descrizione": "Giornata",
            "stato": "lavorato",
        },
    )
    moved = agent.post(
        f"/api/work-units/{day['id']}/transitions",
        json={"stato": "contestato", "motivo": "Contesto io"},
    )

    assert recorded.status_code == 403, recorded.text
    assert recorded.json()["code"] == "agent_forbidden"
    assert moved.status_code == 403, moved.text
    assert moved.json()["code"] == "agent_forbidden"
    # Reading is not closed to it: a day is a fact, and an assistant asked about one reads it.
    assert agent.get(f"/api/work-units/{day['id']}").status_code == 200
    unchanged = logged_in.get(f"/api/work-units/{day['id']}").json()
    assert unchanged["stato"] == "lavorato"
