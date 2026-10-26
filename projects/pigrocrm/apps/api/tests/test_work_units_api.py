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


def _approved_day(client: TestClient, contract_id: str, giorno: str) -> dict[str, Any]:
    """A day an approval covers, the only way over REST to have an approved one."""
    day = _record(client, contract_id, giorno, stato="proposto")
    document = client.post(
        "/api/documents",
        json={"contract_id": contract_id, "tipo": "documento", "titolo": "Mail di conferma"},
    )
    assert document.status_code == 201, document.text
    recorded = client.post(
        "/api/approvals",
        json={
            "contract_id": contract_id,
            "canale": "email",
            "mittente": "giulia@cliente.example",
            "ricevuto_il": "2026-03-01T09:00:00Z",
            "estratto": "Confermo.",
            "document_id": document.json()["id"],
            "work_unit_ids": [day["id"]],
        },
    )
    assert recorded.status_code == 201, recorded.text
    return dict(recorded.json()["work_units"][0])


def test_a_day_is_moved_and_the_page_shows_both_moves_with_their_dates(
    logged_in: TestClient,
) -> None:
    contract_id = _contract(logged_in)
    day = _approved_day(logged_in, contract_id, "2026-03-02")

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
        (None, "proposto", "creazione"),
        ("proposto", "approvato", "Approvazione registrata"),
        ("approvato", "lavorato", "Fatta"),
        ("lavorato", "contestato", "Il cliente contesta le ore"),
    ]
    assert all(t["created_at"] for t in body["transizioni"])
    assert body["transizioni"][-1]["attore"]["kind"] == "human"
    assert body["azioni"] == ["lavorato", "revocato", "non_fatturabile"]


def test_a_person_cannot_write_the_edges_that_belong_to_an_approval_or_an_invoice(
    logged_in: TestClient,
) -> None:
    contract_id = _contract(logged_in)
    worked = _record(logged_in, contract_id, "2026-03-02")
    proposed = _record(logged_in, contract_id, "2026-03-03", stato="proposto")

    billed = logged_in.post(
        f"/api/work-units/{worked['id']}/transitions", json={"stato": "fatturato", "motivo": "x"}
    )
    approved = logged_in.post(
        f"/api/work-units/{proposed['id']}/transitions", json={"stato": "approvato", "motivo": "x"}
    )

    assert billed.status_code == 409, billed.text
    assert approved.status_code == 409, approved.text
    assert logged_in.get(f"/api/work-units/{worked['id']}").json()["stato"] == "lavorato"


def test_a_flagged_day_has_no_plain_move_back_to_worked(logged_in: TestClient) -> None:
    """The trigger would rewrite it straight back, so the page never offered it and the API
    does not pretend: the way out is an approval."""
    contract_id = _contract(logged_in, requires_prior_approval=True)
    flagged = _record(logged_in, contract_id, "2026-03-02")

    response = logged_in.post(
        f"/api/work-units/{flagged['id']}/transitions", json={"stato": "lavorato", "motivo": "x"}
    )

    assert response.status_code == 409, response.text


def test_a_day_is_recorded_proposed_or_worked_and_never_approved_or_with_an_approval(
    logged_in: TestClient,
) -> None:
    contract_id = _contract(logged_in)
    base = {
        "contract_id": contract_id,
        "data": "2026-03-02",
        "quantita": "1.00",
        "descrizione": "Giornata",
    }

    assert logged_in.post("/api/work-units", json={**base, "stato": "approvato"}).status_code == 422
    forged = {**base, "stato": "lavorato", "approval_id": "00000000-0000-0000-0000-000000000000"}
    assert logged_in.post("/api/work-units", json=forged).status_code == 422


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


# --- recording an approval (REB-747) -------------------------------------------------------


def _document(client: TestClient, contract_id: str) -> str:
    response = client.post(
        "/api/documents",
        json={"contract_id": contract_id, "tipo": "documento", "titolo": "Mail di conferma"},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _approval_body(contract_id: str, document_id: str, days: list[str]) -> dict[str, Any]:
    return {
        "contract_id": contract_id,
        "canale": "email",
        "mittente": "giulia@cliente.example",
        "ricevuto_il": "2026-03-01T09:00:00Z",
        "estratto": "Confermo le giornate del 2 e del 3 marzo.",
        "document_id": document_id,
        "work_unit_ids": days,
    }


def test_an_approval_moves_the_days_it_names_and_the_page_shows_what_it_rests_on(
    logged_in: TestClient,
) -> None:
    contract_id = _contract(logged_in, requires_prior_approval=True)
    proposed = _record(logged_in, contract_id, "2026-03-02", stato="proposto")
    flagged = _record(logged_in, contract_id, "2026-03-03")
    assert flagged["stato"] == "lavorato_senza_approvazione"
    document_id = _document(logged_in, contract_id)

    response = logged_in.post(
        "/api/approvals",
        json=_approval_body(contract_id, document_id, [proposed["id"], flagged["id"]]),
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert {day["id"]: day["stato"] for day in body["work_units"]} == {
        proposed["id"]: "approvato",
        flagged["id"]: "lavorato",
    }
    assert body["approval"]["documento_titolo"] == "Mail di conferma"
    page = logged_in.get(f"/api/work-units/{flagged['id']}").json()
    assert page["approval"]["id"] == body["approval"]["id"]
    assert page["approval"]["mittente"] == "giulia@cliente.example"


def test_an_approval_over_a_day_that_cannot_use_it_is_a_409_and_changes_nothing(
    logged_in: TestClient,
) -> None:
    contract_id = _contract(logged_in)
    fine = _record(logged_in, contract_id, "2026-03-02", stato="proposto")
    paid_for = _record(
        logged_in, contract_id, "2026-03-03"
    )  # worked on a contract that asks for none
    document_id = _document(logged_in, contract_id)

    response = logged_in.post(
        "/api/approvals",
        json=_approval_body(contract_id, document_id, [fine["id"], paid_for["id"]]),
    )

    assert response.status_code == 409, response.text
    assert logged_in.get(f"/api/work-units/{fine['id']}").json()["stato"] == "proposto"


def test_an_approval_with_two_proofs_or_none_is_a_422(logged_in: TestClient) -> None:
    contract_id = _contract(logged_in)
    day = _record(logged_in, contract_id, "2026-03-02", stato="proposto")
    body = _approval_body(contract_id, _document(logged_in, contract_id), [day["id"]])
    neither = {k: v for k, v in body.items() if k != "document_id"}
    both = {**body, "gmail_message_id": "00000000-0000-0000-0000-000000000000"}

    assert logged_in.post("/api/approvals", json=neither).status_code == 422
    assert logged_in.post("/api/approvals", json=both).status_code == 422


def test_an_agent_token_cannot_record_an_approval(
    logged_in: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    contract_id = _contract(logged_in)
    day = _record(logged_in, contract_id, "2026-03-02", stato="proposto")
    body = _approval_body(contract_id, _document(logged_in, contract_id), [day["id"]])
    agent = _agent_client(logged_in, monkeypatch)

    response = agent.post("/api/approvals", json=body)

    assert response.status_code == 403, response.text
    assert response.json()["code"] == "agent_forbidden"
    assert logged_in.get(f"/api/work-units/{day['id']}").json()["stato"] == "proposto"


# --- asking a client for approval (REB-749) ------------------------------------------------


def _request_body(*dates: str, to: str = "amministrazione@acme.example") -> dict[str, Any]:
    return {
        "days": [
            {"data": giorno, "quantita": "1.00", "descrizione": "Workshop con il cliente"}
            for giorno in dates
        ],
        "to_addresses": [to],
        "oggetto": "Richiesta di approvazione delle giornate: Consulenza",
        "corpo": "Buongiorno, le chiedo l'approvazione.",
    }


def test_the_preview_writes_the_request_and_stores_nothing(logged_in: TestClient) -> None:
    contract_id = _contract(logged_in, requires_prior_approval=True)

    response = logged_in.post(
        f"/api/contracts/{contract_id}/approval-request/preview",
        json={"days": _request_body("2026-11-04", "2026-11-02")["days"]},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["oggetto"] == "Richiesta di approvazione delle giornate: Consulenza"
    assert "- lunedì 2 novembre 2026: giornata intera, Workshop con il cliente" in body["corpo"]
    assert (
        logged_in.get("/api/work-units", params={"contract_id": contract_id}).json()["items"] == []
    )


def test_preparing_a_request_proposes_the_days_and_files_the_draft_without_sending(
    logged_in: TestClient,
) -> None:
    contract_id = _contract(logged_in, requires_prior_approval=True)

    response = logged_in.post(
        f"/api/contracts/{contract_id}/approval-request",
        json=_request_body("2026-11-02", "2026-11-03", "2026-11-04"),
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert [day["stato"] for day in body["work_units"]] == ["proposto"] * 3
    assert body["draft"]["send_state"] == "bozza"
    assert body["draft"]["to_addresses"] == ["amministrazione@acme.example"]
    days = logged_in.get("/api/work-units", params={"contract_id": contract_id}).json()["items"]
    assert sorted(day["data"] for day in days) == ["2026-11-02", "2026-11-03", "2026-11-04"]


def test_a_request_for_a_date_already_held_is_a_409_and_records_nothing(
    logged_in: TestClient,
) -> None:
    contract_id = _contract(logged_in, requires_prior_approval=True)
    _record(logged_in, contract_id, "2026-11-03")

    response = logged_in.post(
        f"/api/contracts/{contract_id}/approval-request",
        json=_request_body("2026-11-02", "2026-11-03"),
    )

    assert response.status_code == 409, response.text
    days = logged_in.get("/api/work-units", params={"contract_id": contract_id}).json()["items"]
    assert [day["data"] for day in days] == ["2026-11-03"]


def test_a_request_with_no_recipient_is_a_422(logged_in: TestClient) -> None:
    contract_id = _contract(logged_in, requires_prior_approval=True)
    body = {**_request_body("2026-11-02"), "to_addresses": []}

    response = logged_in.post(f"/api/contracts/{contract_id}/approval-request", json=body)

    assert response.status_code == 422, response.text


def test_an_agent_token_cannot_prepare_a_request(
    logged_in: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    contract_id = _contract(logged_in, requires_prior_approval=True)
    agent = _agent_client(logged_in, monkeypatch)

    response = agent.post(
        f"/api/contracts/{contract_id}/approval-request", json=_request_body("2026-11-02")
    )

    assert response.status_code == 403, response.text
    assert response.json()["code"] == "agent_forbidden"
    assert (
        logged_in.get("/api/work-units", params={"contract_id": contract_id}).json()["items"] == []
    )
