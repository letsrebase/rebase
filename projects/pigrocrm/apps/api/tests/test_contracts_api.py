"""The wire shape of what the contract page calls (REB-741..744): the edit, the state
change, the timeline, a rate card's edit and close-the-previous create. The services
carry the rules and `test_contracts_service.py` proves them; the role gate is
`test_role_matrix.py`'s. This file proves the routes exist, answer in the shapes the
generated types promise, and map a refusal to the status the page reads."""

from decimal import Decimal
from typing import Any

from aziende_helpers import azienda_url
from fastapi.testclient import TestClient


def _contract(logged_in: TestClient, **overrides: Any) -> str:
    customer = logged_in.post("/api/customers", json={"ragione_sociale": "ACME Srl"})
    assert customer.status_code == 201, customer.text
    body: dict[str, Any] = {
        "customer_id": customer.json()["id"],
        "titolo": "Consulenza",
        "inizio": "2026-01-01",
        "tipo_rinnovo": "nessuno",
        "preavviso_disdetta_giorni": 30,
        "cadenza_fatturazione": "mensile",
        "politica_spese": {"kind": "non_rimborsabile"},
        **overrides,
    }
    response = logged_in.post("/api/contracts", json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_a_contract_is_edited_activated_and_the_timeline_says_so(logged_in: TestClient) -> None:
    contract_id = _contract(logged_in)

    edited = logged_in.patch(
        f"/api/contracts/{contract_id}", json={"preavviso_disdetta_giorni": 90}
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["preavviso_disdetta_giorni"] == 90
    assert edited.json()["stato"] == "bozza"

    activated = logged_in.post(f"/api/contracts/{contract_id}/state", json={"stato": "attivo"})
    assert activated.status_code == 200, activated.text
    assert activated.json()["stato"] == "attivo"

    timeline = logged_in.get(f"/api/contracts/{contract_id}/timeline")
    assert timeline.status_code == 200, timeline.text
    assert [row["kind"] for row in timeline.json()][:3] == ["state_changed", "updated", "created"]


def test_the_state_route_refuses_a_repeat_with_409_and_an_unknown_state_with_422(
    logged_in: TestClient,
) -> None:
    contract_id = _contract(logged_in)

    repeat = logged_in.post(f"/api/contracts/{contract_id}/state", json={"stato": "bozza"})
    assert repeat.status_code == 409, repeat.text

    unknown = logged_in.post(f"/api/contracts/{contract_id}/state", json={"stato": "sospeso"})
    assert unknown.status_code == 422, unknown.text


def test_an_edit_that_breaks_a_cross_field_rule_is_a_422(logged_in: TestClient) -> None:
    contract_id = _contract(logged_in)

    response = logged_in.patch(f"/api/contracts/{contract_id}", json={"tipo_rinnovo": "tacito"})
    assert response.status_code == 422, response.text


def test_the_timeline_of_an_unknown_contract_is_a_404(logged_in: TestClient) -> None:
    response = logged_in.get("/api/contracts/00000000-0000-0000-0000-000000000000/timeline")
    assert response.status_code == 404, response.text


def test_a_new_rate_closes_the_open_one_the_day_before_and_an_edit_overlap_is_a_409(
    logged_in: TestClient,
) -> None:
    contract_id = _contract(logged_in)
    first = logged_in.post(
        f"/api/contracts/{contract_id}/rate-cards",
        json={
            "valido_da": "2026-01-01",
            "tipo": "giornaliero",
            "importo": "400.00",
            "unita": "giorno",
        },
    )
    assert first.status_code == 201, first.text

    refused = logged_in.post(
        f"/api/contracts/{contract_id}/rate-cards",
        json={
            "valido_da": "2027-01-01",
            "tipo": "giornaliero",
            "importo": "450.00",
            "unita": "giorno",
        },
    )
    assert refused.status_code == 409, refused.text

    second = logged_in.post(
        f"/api/contracts/{contract_id}/rate-cards",
        params={"chiudi_precedente": True},
        json={
            "valido_da": "2027-01-01",
            "tipo": "giornaliero",
            "importo": "450.00",
            "unita": "giorno",
        },
    )
    assert second.status_code == 201, second.text

    cards = logged_in.get(f"/api/contracts/{contract_id}/rate-cards").json()
    assert [(c["valido_da"], c["valido_a"]) for c in cards] == [
        ("2026-01-01", "2026-12-31"),
        ("2027-01-01", None),
    ]

    overlap = logged_in.patch(
        f"/api/contracts/{contract_id}/rate-cards/{cards[0]['id']}",
        json={"valido_a": "2027-02-28"},
    )
    assert overlap.status_code == 409, overlap.text

    edited = logged_in.patch(
        f"/api/contracts/{contract_id}/rate-cards/{cards[1]['id']}", json={"importo": "475.00"}
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["importo"] == "475.00"


# ---- the cap on one customer's share (REB-761) ----------------------------------------


def test_a_cap_is_written_read_back_and_refused_outside_zero_and_a_hundred(
    logged_in: TestClient,
) -> None:
    contract_id = _contract(logged_in, tetto_quota_cliente="50.00")
    assert logged_in.get(f"/api/contracts/{contract_id}").json()["tetto_quota_cliente"] == "50.00"

    changed = logged_in.patch(f"/api/contracts/{contract_id}", json={"tetto_quota_cliente": "40"})
    assert changed.status_code == 200, changed.text
    assert Decimal(changed.json()["tetto_quota_cliente"]) == Decimal("40")

    cleared = logged_in.patch(f"/api/contracts/{contract_id}", json={"tetto_quota_cliente": None})
    assert cleared.json()["tetto_quota_cliente"] is None

    for value in ("0", "100", "150"):
        refused = logged_in.patch(
            f"/api/contracts/{contract_id}", json={"tetto_quota_cliente": value}
        )
        assert refused.status_code == 422, (value, refused.text)


def test_the_concentration_answers_against_the_contracts_own_cap(logged_in: TestClient) -> None:
    contract_id = _contract(logged_in, tetto_quota_cliente="50.00")

    body = logged_in.get(
        f"/api/contracts/{contract_id}/concentration", params={"as_of": "2026-06-01"}
    ).json()

    assert body["soglia"] == 0.5
    assert body["soglia_da_contratto"] is True
    assert [row["superata"] for row in body["valute"]] == [False]


def test_the_simulator_answers_the_contracts_cap(logged_in: TestClient) -> None:
    """Nothing invoiced, so the customer alone would be all of the revenue: any addition
    takes it past a 50% cap, and the answer names the contract and the window."""
    logged_in.put(
        azienda_url(logged_in, "/fiscal-profile"), json={"pack_id": "it-standard"}
    ).raise_for_status()
    contract_id = _contract(logged_in, tetto_quota_cliente="50.00")

    response = logged_in.get(
        "/api/analytics/ceilings/simulate",
        params={
            "anno": 2026,
            "valore_preventivato": "1000.00",
            "contract_id": contract_id,
            "data_prevista": "2026-06-01",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["soglie"] == []
    assert body["tetto_contratto"]["contract_id"] == contract_id
    assert body["tetto_contratto"]["rientra"] is False
    assert body["tetto_contratto"]["periodo_da"] == "2026-01-01"


# ---- clause notes (REB-762) -----------------------------------------------------------


def test_a_clause_note_is_written_corrected_listed_and_read_with_the_contract(
    logged_in: TestClient,
) -> None:
    contract_id = _contract(logged_in)
    created = logged_in.post(
        f"/api/contracts/{contract_id}/clause-notes",
        json={
            "riferimento_clausola": "Art. 8.3 Rinnovo",
            "testo_letterale": "Il contratto si rinnova salvo disdetta.",
            "interpretazione_adottata": "Rinnovo tacito, disdetta a 60 giorni.",
        },
    )
    assert created.status_code == 201, created.text
    note_id = created.json()["id"]

    corrected = logged_in.patch(
        f"/api/contracts/{contract_id}/clause-notes/{note_id}",
        json={"note": "Confermato dal cliente il 12 ottobre"},
    )
    assert corrected.status_code == 200, corrected.text

    listed = logged_in.get(f"/api/contracts/{contract_id}/clause-notes").json()
    assert [n["id"] for n in listed] == [note_id]
    read = logged_in.get(f"/api/contracts/{contract_id}").json()
    assert [n["riferimento_clausola"] for n in read["note_clausole"]] == ["Art. 8.3 Rinnovo"]
    assert read["note_clausole"][0]["note"] == "Confermato dal cliente il 12 ottobre"
    assert read["contratto_successivo_id"] is None


def test_a_clause_note_missing_its_interpretation_is_a_422_and_an_unknown_one_a_404(
    logged_in: TestClient,
) -> None:
    contract_id = _contract(logged_in)
    refused = logged_in.post(
        f"/api/contracts/{contract_id}/clause-notes",
        json={"riferimento_clausola": "Art. 1", "testo_letterale": "Testo."},
    )
    assert refused.status_code == 422, refused.text
    missing = logged_in.patch(
        f"/api/contracts/{contract_id}/clause-notes/00000000-0000-0000-0000-000000000000",
        json={"note": "x"},
    )
    assert missing.status_code == 404, missing.text
