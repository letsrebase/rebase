"""`/api/legal-entities` (REB-616, spec 2026-10-03 §3): the routes that replaced `/api/emitter`
and `/api/fiscal-profile` when the emitter profile became one row per azienda.

Every test space has its default azienda from the session fixture, the way a provisioned
space has it; a second one is written by row in the older tests and created through
`POST /api/aziende` in the newer ones, the route milestone 5 opened (§9, REB-631).
"""

from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient
from legal_entities_helpers import legal_entity_url
from sqlalchemy.orm import Session

from pigrocrm.core.legal_entities.models import LegalEntity


def _second_legal_entity(session: Session, **overrides: Any) -> str:
    values: dict[str, Any] = {
        "name": "rebase",
        "legal_name": "Rebase S.r.l.",
        "vat_number": "09876543210",
        "country": "IT",
    }
    values.update(overrides)
    row = LegalEntity(**values)
    session.add(row)
    session.flush()
    return str(row.id)


LTD = {
    "name": "rebase ltd",
    "legal_name": "Rebase Ltd",
    "vat_number": "GB123456789",
    "country": "GB",
    "address": "1 Poultry",
    "city": "London",
    "fiscal_profile": {"pack_id": "non-it", "default_vat_rate": "20.00"},
}


def test_post_creates_to_second_legal_entity_born_with_its_profile(logged_in: TestClient) -> None:
    """Spec §3 and §9 (REB-631): one request, the row and its fiscal profile, so no
    azienda ever exists that `issue` would refuse for want of a profile."""
    created = logged_in.post("/api/legal-entities", json=LTD)
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["name"], body["is_default"], body["active"]) == ("rebase ltd", False, True)
    assert body["vat_number"] == "GB123456789"
    profile = logged_in.get(f"/api/legal-entities/{body['id']}/fiscal-profile")
    assert profile.status_code == 200, profile.text
    assert (profile.json()["pack_id"], profile.json()["regime_code"]) == ("non-it", None)
    listed = [a["name"] for a in logged_in.get("/api/legal-entities").json()]
    assert listed == ["Spazio di prova", "rebase ltd"]


def test_to_refused_profile_leaves_no_legal_entity_behind(logged_in: TestClient) -> None:
    refused = logged_in.post(
        "/api/legal-entities",
        json={**LTD, "fiscal_profile": {**LTD["fiscal_profile"], "apply_stamp_duty": True}},
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["field"] == "apply_stamp_duty"
    assert len(logged_in.get("/api/legal-entities").json()) == 1
    # And `name` is required on creation, where the single profile derived it.
    unnamed = logged_in.post(
        "/api/legal-entities", json={k: v for k, v in LTD.items() if k != "name"}
    )
    assert unnamed.status_code == 422, unnamed.text


def test_only_an_admin_creates_an_legal_entity(readonly_client: TestClient) -> None:
    refused = readonly_client.post("/api/legal-entities", json=LTD)
    assert refused.status_code == 403, refused.text
    assert len(readonly_client.get("/api/legal-entities").json()) == 1


def test_a_deactivation_answers_how_many_organizations_still_point_at_the_row(
    logged_in: TestClient,
) -> None:
    second = logged_in.post("/api/legal-entities", json=LTD).json()["id"]
    for name in ("Uno Ltd", "Due Ltd"):
        customer = logged_in.post(
            "/api/organizations",
            json={"legal_name": name, "country": "GB", "legal_entity_id": second},
        )
        assert customer.status_code == 201, customer.text
    gone = logged_in.delete(f"/api/legal-entities/{second}")
    assert gone.status_code == 200, gone.text
    assert gone.json()["active"] is False
    assert gone.json()["linked_organizations"] == 2


def test_the_old_routes_are_gone_with_no_alias(logged_in: TestClient) -> None:
    assert logged_in.get("/api/emitter").status_code == 404
    assert logged_in.get("/api/fiscal-profile").status_code == 404


def test_the_list_has_the_default_first_and_hides_an_inactive_one(
    logged_in: TestClient, api_session: Session
) -> None:
    second = _second_legal_entity(api_session, name="alfa", active=False)
    listed = logged_in.get("/api/legal-entities").json()
    assert [a["name"] for a in listed] == ["Spazio di prova"]
    assert listed[0]["is_default"] is True
    everyone = logged_in.get("/api/legal-entities", params={"include_inactive": "true"}).json()
    assert [a["id"] for a in everyone] == [listed[0]["id"], second]


def test_an_unknown_id_is_404(logged_in: TestClient) -> None:
    assert logged_in.get(f"/api/legal-entities/{uuid4()}").status_code == 404
    assert (
        logged_in.put(f"/api/legal-entities/{uuid4()}", json={"legal_name": "X"}).status_code == 404
    )


def test_a_put_replaces_the_row_and_derives_the_short_name(logged_in: TestClient) -> None:
    saved = logged_in.put(
        legal_entity_url(logged_in),
        json={"legal_name": "Studio Rossi di Mario Rossi", "vat_number": "01234567890"},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["name"] == "Studio Rossi di Mario Rossi"
    assert saved.json()["vat_number"] == "01234567890"
    assert saved.json()["is_default"] is True


def test_a_readonly_member_reads_the_list_and_cannot_write(
    readonly_client: TestClient,
) -> None:
    assert readonly_client.get("/api/legal-entities").status_code == 200
    assert (
        readonly_client.put(legal_entity_url(readonly_client), json={"legal_name": "X"}).status_code
        == 403
    )


def test_the_default_moves_and_the_old_default_cannot_be_deactivated_first(
    logged_in: TestClient, api_session: Session
) -> None:
    first = logged_in.get("/api/legal-entities").json()[0]["id"]
    second = _second_legal_entity(api_session)
    refused = logged_in.delete(f"/api/legal-entities/{first}")
    assert refused.status_code == 422, refused.text
    assert refused.json()["field"] == "active"
    moved = logged_in.post(f"/api/legal-entities/{second}/default")
    assert moved.status_code == 200, moved.text
    assert moved.json()["is_default"] is True
    assert logged_in.get(f"/api/legal-entities/{first}").json()["is_default"] is False
    gone = logged_in.delete(f"/api/legal-entities/{first}")
    assert gone.status_code == 200, gone.text
    assert gone.json()["active"] is False
    assert [a["id"] for a in logged_in.get("/api/legal-entities").json()] == [second]


def test_each_legal_entity_keeps_its_own_fiscal_profile(
    logged_in: TestClient, api_session: Session
) -> None:
    first = logged_in.get("/api/legal-entities").json()[0]["id"]
    second = _second_legal_entity(api_session)
    assert logged_in.get(f"/api/legal-entities/{second}/fiscal-profile").status_code == 404
    saved = logged_in.put(
        f"/api/legal-entities/{first}/fiscal-profile", json={"regime_code": "RF19"}
    )
    assert saved.status_code == 200, saved.text
    other = logged_in.put(
        f"/api/legal-entities/{second}/fiscal-profile",
        json={"pack_id": "it-standard"},
    )
    assert other.status_code == 200, other.text
    assert other.json()["pack_id"] == "it-standard"
    assert other.json()["default_vat_rate"] == "22.00"
    assert (
        logged_in.get(f"/api/legal-entities/{first}/fiscal-profile").json()["regime_code"] == "RF19"
    )
    assert (
        logged_in.get(f"/api/legal-entities/{second}/fiscal-profile").json()["regime_code"]
        == "RF01"
    )


def test_to_vat_number_of_another_legal_entity_is_to_conflict(
    logged_in: TestClient, api_session: Session
) -> None:
    second = _second_legal_entity(api_session)
    taken = logged_in.put(
        f"/api/legal-entities/{second}", json={"legal_name": "Doppia", "vat_number": "01234567890"}
    )
    assert taken.status_code == 200, taken.text
    clash = logged_in.put(
        legal_entity_url(logged_in), json={"legal_name": "Studio", "vat_number": "01234567890"}
    )
    assert clash.status_code == 409, clash.text


def test_a_foreign_fiscal_profile_has_no_regime_code_and_an_italian_one_requires_it(
    logged_in: TestClient, api_session: Session
) -> None:
    """REB-620, spec §1.3: the `non-it` pack is saved with no RF code; the Italian pack
    without one is refused naming the field, by the service and not by the schema."""
    foreign = _second_legal_entity(api_session, name="ltd", legal_name="Rebase Ltd", country="GB")
    saved = logged_in.put(
        f"/api/legal-entities/{foreign}/fiscal-profile",
        json={
            "pack_id": "non-it",
            "default_vat_rate": "20.00",
            "default_vat_nature": None,
            "legal_reference": None,
        },
    )
    assert saved.status_code == 200, saved.text
    assert (saved.json()["pack_id"], saved.json()["regime_code"]) == ("non-it", None)
    assert (
        logged_in.get(f"/api/legal-entities/{foreign}/fiscal-profile").json()["regime_code"] is None
    )

    refused = logged_in.put(legal_entity_url(logged_in, "/fiscal-profile"), json={})
    assert refused.status_code == 422, refused.text
    assert refused.json()["field"] == "regime_code"
    mixed = logged_in.put(
        f"/api/legal-entities/{foreign}/fiscal-profile",
        json={"pack_id": "non-it", "regime_code": "RF19"},
    )
    assert mixed.status_code == 422, mixed.text
    assert mixed.json()["field"] == "regime_code"
    unknown = logged_in.put(
        legal_entity_url(logged_in, "/fiscal-profile"),
        json={"pack_id": "fr", "regime_code": "RF19"},
    )
    assert unknown.status_code == 422, unknown.text
