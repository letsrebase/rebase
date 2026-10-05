"""`/api/aziende` (REB-616, spec 2026-10-03 §3): the routes that replaced `/api/emitter`
and `/api/fiscal-profile` when the emitter profile became one row per azienda.

Every test space has its default azienda from the session fixture, the way a provisioned
space has it; a second one is written by row in the older tests and created through
`POST /api/aziende` in the newer ones, the route milestone 5 opened (§9, REB-631).
"""

from typing import Any
from uuid import uuid4

from aziende_helpers import azienda_url
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pigrocrm.core.emitter.models import LegalEntity


def _second_azienda(session: Session, **overrides: Any) -> str:
    values: dict[str, Any] = {
        "nome": "rebase",
        "ragione_sociale": "Rebase S.r.l.",
        "partita_iva": "09876543210",
        "nazione": "IT",
    }
    values.update(overrides)
    row = LegalEntity(**values)
    session.add(row)
    session.flush()
    return str(row.id)


LTD = {
    "nome": "rebase ltd",
    "ragione_sociale": "Rebase Ltd",
    "partita_iva": "GB123456789",
    "nazione": "GB",
    "indirizzo": "1 Poultry",
    "comune": "London",
    "fiscal_profile": {"pack_id": "non-it", "aliquota_iva_default": "20.00"},
}


def test_post_creates_a_second_azienda_born_with_its_profile(logged_in: TestClient) -> None:
    """Spec §3 and §9 (REB-631): one request, the row and its fiscal profile, so no
    azienda ever exists that `issue` would refuse for want of a profile."""
    created = logged_in.post("/api/aziende", json=LTD)
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["nome"], body["predefinita"], body["attiva"]) == ("rebase ltd", False, True)
    assert body["partita_iva"] == "GB123456789"
    profile = logged_in.get(f"/api/aziende/{body['id']}/fiscal-profile")
    assert profile.status_code == 200, profile.text
    assert (profile.json()["pack_id"], profile.json()["codice_regime"]) == ("non-it", None)
    listed = [a["nome"] for a in logged_in.get("/api/aziende").json()]
    assert listed == ["Spazio di prova", "rebase ltd"]


def test_a_refused_profile_leaves_no_azienda_behind(logged_in: TestClient) -> None:
    refused = logged_in.post(
        "/api/aziende",
        json={**LTD, "fiscal_profile": {**LTD["fiscal_profile"], "applica_bollo": True}},
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["field"] == "applica_bollo"
    assert len(logged_in.get("/api/aziende").json()) == 1
    # And `nome` is required on creation, where the single profile derived it.
    unnamed = logged_in.post("/api/aziende", json={k: v for k, v in LTD.items() if k != "nome"})
    assert unnamed.status_code == 422, unnamed.text


def test_only_an_admin_creates_an_azienda(readonly_client: TestClient) -> None:
    refused = readonly_client.post("/api/aziende", json=LTD)
    assert refused.status_code == 403, refused.text
    assert len(readonly_client.get("/api/aziende").json()) == 1


def test_a_deactivation_answers_how_many_customers_still_point_at_the_row(
    logged_in: TestClient,
) -> None:
    second = logged_in.post("/api/aziende", json=LTD).json()["id"]
    for nome in ("Uno Ltd", "Due Ltd"):
        customer = logged_in.post(
            "/api/customers",
            json={"ragione_sociale": nome, "nazione": "GB", "azienda_id": second},
        )
        assert customer.status_code == 201, customer.text
    gone = logged_in.delete(f"/api/aziende/{second}")
    assert gone.status_code == 200, gone.text
    assert gone.json()["attiva"] is False
    assert gone.json()["clienti_collegati"] == 2


def test_the_old_routes_are_gone_with_no_alias(logged_in: TestClient) -> None:
    assert logged_in.get("/api/emitter").status_code == 404
    assert logged_in.get("/api/fiscal-profile").status_code == 404


def test_the_list_has_the_default_first_and_hides_an_inactive_one(
    logged_in: TestClient, api_session: Session
) -> None:
    second = _second_azienda(api_session, nome="alfa", attiva=False)
    listed = logged_in.get("/api/aziende").json()
    assert [a["nome"] for a in listed] == ["Spazio di prova"]
    assert listed[0]["predefinita"] is True
    everyone = logged_in.get("/api/aziende", params={"include_inactive": "true"}).json()
    assert [a["id"] for a in everyone] == [listed[0]["id"], second]


def test_an_unknown_id_is_404(logged_in: TestClient) -> None:
    assert logged_in.get(f"/api/aziende/{uuid4()}").status_code == 404
    assert (
        logged_in.put(f"/api/aziende/{uuid4()}", json={"ragione_sociale": "X"}).status_code == 404
    )


def test_a_put_replaces_the_row_and_derives_the_short_name(logged_in: TestClient) -> None:
    saved = logged_in.put(
        azienda_url(logged_in),
        json={"ragione_sociale": "Studio Rossi di Mario Rossi", "partita_iva": "01234567890"},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["nome"] == "Studio Rossi di Mario Rossi"
    assert saved.json()["partita_iva"] == "01234567890"
    assert saved.json()["predefinita"] is True


def test_a_readonly_member_reads_the_list_and_cannot_write(
    readonly_client: TestClient,
) -> None:
    assert readonly_client.get("/api/aziende").status_code == 200
    assert (
        readonly_client.put(azienda_url(readonly_client), json={"ragione_sociale": "X"}).status_code
        == 403
    )


def test_the_default_moves_and_the_old_default_cannot_be_deactivated_first(
    logged_in: TestClient, api_session: Session
) -> None:
    first = logged_in.get("/api/aziende").json()[0]["id"]
    second = _second_azienda(api_session)
    refused = logged_in.delete(f"/api/aziende/{first}")
    assert refused.status_code == 422, refused.text
    assert refused.json()["field"] == "attiva"
    moved = logged_in.post(f"/api/aziende/{second}/predefinita")
    assert moved.status_code == 200, moved.text
    assert moved.json()["predefinita"] is True
    assert logged_in.get(f"/api/aziende/{first}").json()["predefinita"] is False
    gone = logged_in.delete(f"/api/aziende/{first}")
    assert gone.status_code == 200, gone.text
    assert gone.json()["attiva"] is False
    assert [a["id"] for a in logged_in.get("/api/aziende").json()] == [second]


def test_each_azienda_keeps_its_own_fiscal_profile(
    logged_in: TestClient, api_session: Session
) -> None:
    first = logged_in.get("/api/aziende").json()[0]["id"]
    second = _second_azienda(api_session)
    assert logged_in.get(f"/api/aziende/{second}/fiscal-profile").status_code == 404
    saved = logged_in.put(f"/api/aziende/{first}/fiscal-profile", json={"codice_regime": "RF19"})
    assert saved.status_code == 200, saved.text
    other = logged_in.put(
        f"/api/aziende/{second}/fiscal-profile",
        json={"codice_regime": "RF01", "aliquota_iva_default": "22.00", "natura_default": None},
    )
    assert other.status_code == 200, other.text
    assert logged_in.get(f"/api/aziende/{first}/fiscal-profile").json()["codice_regime"] == "RF19"
    assert logged_in.get(f"/api/aziende/{second}/fiscal-profile").json()["codice_regime"] == "RF01"


def test_a_partita_iva_of_another_azienda_is_a_conflict(
    logged_in: TestClient, api_session: Session
) -> None:
    second = _second_azienda(api_session)
    taken = logged_in.put(
        f"/api/aziende/{second}", json={"ragione_sociale": "Doppia", "partita_iva": "01234567890"}
    )
    assert taken.status_code == 200, taken.text
    clash = logged_in.put(
        azienda_url(logged_in), json={"ragione_sociale": "Studio", "partita_iva": "01234567890"}
    )
    assert clash.status_code == 409, clash.text


def test_a_foreign_fiscal_profile_has_no_regime_code_and_an_italian_one_requires_it(
    logged_in: TestClient, api_session: Session
) -> None:
    """REB-620, spec §1.3: the `non-it` pack is saved with no RF code; the Italian pack
    without one is refused naming the field, by the service and not by the schema."""
    foreign = _second_azienda(api_session, nome="ltd", ragione_sociale="Rebase Ltd", nazione="GB")
    saved = logged_in.put(
        f"/api/aziende/{foreign}/fiscal-profile",
        json={
            "pack_id": "non-it",
            "aliquota_iva_default": "20.00",
            "natura_default": None,
            "riferimento_normativo": None,
        },
    )
    assert saved.status_code == 200, saved.text
    assert (saved.json()["pack_id"], saved.json()["codice_regime"]) == ("non-it", None)
    assert logged_in.get(f"/api/aziende/{foreign}/fiscal-profile").json()["codice_regime"] is None

    refused = logged_in.put(azienda_url(logged_in, "/fiscal-profile"), json={})
    assert refused.status_code == 422, refused.text
    assert refused.json()["field"] == "codice_regime"
    mixed = logged_in.put(
        f"/api/aziende/{foreign}/fiscal-profile",
        json={"pack_id": "non-it", "codice_regime": "RF19"},
    )
    assert mixed.status_code == 422, mixed.text
    assert mixed.json()["field"] == "codice_regime"
    unknown = logged_in.put(
        azienda_url(logged_in, "/fiscal-profile"), json={"pack_id": "fr", "codice_regime": "RF19"}
    )
    assert unknown.status_code == 422, unknown.text
