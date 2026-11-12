"""`/api/legal-entities/{id}/sites` (REB-1092): the places of business beside the registered
office, the regions they can be in, and a staff member's workplace over the staff routes."""

from fastapi.testclient import TestClient
from legal_entities_helpers import legal_entity_url
from sqlalchemy.orm import Session

from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.legal_entities.models import LegalEntity


def _company(session: Session) -> str:
    entity = LegalEntity(name="srl sedi", legal_name="Sedi S.r.l.", country="IT")
    session.add(entity)
    session.flush()
    session.add(FiscalProfile(legal_entity_id=entity.id, pack_id="it-company", regime_code="RF01"))
    session.commit()
    return str(entity.id)


def test_the_regions_a_site_can_be_in_are_the_pack_s_twenty_one(logged_in: TestClient) -> None:
    regions = logged_in.get("/api/legal-entities/regions")
    assert regions.status_code == 200, regions.text
    by_key = {row["key"]: row["name"] for row in regions.json()}
    assert len(by_key) == 21 and by_key["05"] == "Veneto" and "04" not in by_key


def test_a_site_is_added_listed_changed_and_removed(
    logged_in: TestClient, api_session: Session
) -> None:
    entity = _company(api_session)
    base = f"/api/legal-entities/{entity}/sites"
    created = logged_in.post(base, json={"region": "05", "city": "Padova"})
    assert created.status_code == 201, created.text
    site = created.json()
    assert (site["region"], site["region_name"], site["city"], site["staff_count"]) == (
        "05",
        "Veneto",
        "Padova",
        0,
    )

    listed = logged_in.get(base)
    assert [row["id"] for row in listed.json()] == [site["id"]]
    changed = logged_in.patch(f"{base}/{site['id']}", json={"address": "Via Roma 1", "city": None})
    assert changed.status_code == 200, changed.text
    assert (changed.json()["address"], changed.json()["city"]) == ("Via Roma 1", None)

    assert logged_in.delete(f"{base}/{site['id']}").status_code == 204
    assert logged_in.get(base).json() == []
    assert logged_in.delete(f"{base}/{site['id']}").status_code == 404


def test_a_region_the_pack_does_not_have_is_refused_in_italian(
    logged_in: TestClient,
) -> None:
    refused = logged_in.post(legal_entity_url(logged_in, "/sites"), json={"region": "04"})
    assert refused.status_code == 422, refused.text
    assert refused.json()["field"] == "region"


def test_a_site_with_staff_is_not_removed(logged_in: TestClient, api_session: Session) -> None:
    entity = _company(api_session)
    base = f"/api/legal-entities/{entity}/sites"
    site = logged_in.post(base, json={"region": "05"}).json()
    member = logged_in.post(
        "/api/staff",
        json={
            "kind": "employee",
            "legal_entity_id": entity,
            "name": "Luca Verdi",
            "start_date": "2026-01-01",
            "gross_annual_pay": "24000.00",
            "monthly_payments": 13,
            "site_id": site["id"],
        },
    )
    assert member.status_code == 201, member.text
    assert (member.json()["site_id"], member.json()["at_registered_office"]) == (site["id"], False)
    assert logged_in.get(base).json()[0]["staff_count"] == 1

    refused = logged_in.delete(f"{base}/{site['id']}")
    assert refused.status_code == 409, refused.text

    moved = logged_in.patch(
        f"/api/staff/{member.json()['id']}", json={"at_registered_office": True}
    )
    assert moved.status_code == 200, moved.text
    assert (moved.json()["site_id"], moved.json()["at_registered_office"]) == (None, True)
    assert logged_in.delete(f"{base}/{site['id']}").status_code == 204


def test_a_company_with_sites_and_staff_is_estimated_and_then_deactivated(
    logged_in: TestClient, api_session: Session
) -> None:
    entity = _company(api_session)
    base = f"/api/legal-entities/{entity}/sites"
    site = logged_in.post(base, json={"region": "05"}).json()
    for extra in ({"site_id": site["id"]}, {"at_registered_office": True}):
        made = logged_in.post(
            "/api/staff",
            json={
                "kind": "employee",
                "legal_entity_id": entity,
                "name": f"Persona {len(extra)}{next(iter(extra))}",
                "start_date": "2026-01-01",
                "gross_annual_pay": "24000.00",
                "monthly_payments": 13,
                **extra,
            },
        )
        assert made.status_code == 201, made.text
    estimate = logged_in.get(f"/api/analytics/fiscal?year=2026&legal_entity_id={entity}")
    assert estimate.status_code == 200, estimate.text
    assert logged_in.delete(f"/api/legal-entities/{entity}").status_code == 200
