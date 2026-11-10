"""The accountant's period export over REST (REB-1011, REB-1012): an admin builds a quarter, which
is stored, and downloads its ZIP with the sales register, the invoices' files and the index;
everyone else is refused. What the
parts hold is proven in `packages/core/tests/test_accountant_export.py`."""

import datetime as dt
import io
import zipfile
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.legal_entities.repository import LegalEntityRepository
from pigrocrm.core.organizations.models import Organization

QUARTER = {"date_from": "2026-01-01", "date_to": "2026-03-31"}


def _invoice(session: Session) -> None:
    customer = Organization(legal_name="Acme S.r.l.", country="IT")
    session.add(customer)
    session.flush()
    session.add(
        Invoice(
            customer_id=customer.id,
            type="invoice",
            status="issued",
            year=2026,
            number=1,
            issue_date=dt.date(2026, 2, 3),
            taxable_amount=Decimal("1200.50"),
            total=Decimal("1200.50"),
        )
    )
    session.commit()


def _download(client: TestClient, export: dict) -> bytes:
    response = client.get(export["parts"][0]["download_url"])
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"].startswith(
        "attachment; filename*=UTF-8''commercialista-"
    )
    return response.content


def test_an_admin_builds_the_quarter_and_downloads_the_zip_with_the_index(
    logged_in: TestClient, api_session: Session
) -> None:
    _invoice(api_session)

    response = logged_in.post("/api/accountant-export/exports", json=QUARTER)

    assert response.status_code == 201, response.text
    export = response.json()
    assert export["scope"] == "entity" and export["deleted_at"] is None
    assert len(export["parts"]) == 1
    archive = zipfile.ZipFile(io.BytesIO(_download(logged_in, export)))
    assert {"indice.csv", "vendite/registro-vendite.csv"} <= set(archive.namelist())
    assert "Registro delle vendite" in archive.read("vendite/registro-vendite.csv").decode()
    index = archive.read("indice.csv").decode()
    assert "Non disponibile" in index and "Registro degli acquisti" in index


def test_building_twice_keeps_both_and_lists_them(
    logged_in: TestClient, api_session: Session
) -> None:
    _invoice(api_session)

    first = logged_in.post("/api/accountant-export/exports", json=QUARTER).json()
    second = logged_in.post("/api/accountant-export/exports", json=QUARTER).json()

    listed = logged_in.get("/api/accountant-export/exports").json()
    assert {item["id"] for item in listed["items"]} == {first["id"], second["id"]}
    assert listed["total_count"] == 2
    assert (
        logged_in.get(f"/api/accountant-export/exports/{first['id']}").json()["id"] == first["id"]
    )
    assert _download(logged_in, first)
    assert _download(logged_in, second)


def test_the_list_pages_and_filters_by_year_and_says_how_many_there_are(
    logged_in: TestClient,
) -> None:
    built = [
        logged_in.post("/api/accountant-export/exports", json=QUARTER).json() for _ in range(3)
    ]

    page = logged_in.get("/api/accountant-export/exports", params={"limit": 2}).json()
    rest = logged_in.get("/api/accountant-export/exports", params={"limit": 2, "offset": 2}).json()
    other_year = logged_in.get("/api/accountant-export/exports", params={"year": 2025}).json()

    assert (len(page["items"]), page["total_count"]) == (2, 3)
    assert {item["id"] for item in page["items"] + rest["items"]} == {b["id"] for b in built}
    assert other_year == {"items": [], "total_count": 0}
    assert logged_in.get("/api/accountant-export/exports", params={"limit": 0}).status_code == 422


def test_the_parts_a_build_would_hold_are_listed_with_what_they_are_made_of(
    logged_in: TestClient, api_session: Session
) -> None:
    entity = LegalEntityRepository(api_session).default()
    assert entity is not None

    parts = logged_in.get("/api/accountant-export/parts").json()
    named = logged_in.get(
        "/api/accountant-export/parts", params={"legal_entity_id": str(entity.id)}
    ).json()
    everything = logged_in.get("/api/accountant-export/parts", params={"all_entities": True})

    assert parts == named and parts[0]["key"] == "sales_register"
    assert set(parts[0]) == {"key", "label", "date_rule", "contents"}
    assert everything.status_code == 200
    assert (
        logged_in.get(
            "/api/accountant-export/parts",
            params={"legal_entity_id": str(entity.id), "all_entities": True},
        ).status_code
        == 422
    )


def test_a_part_that_does_not_exist_is_not_found(logged_in: TestClient) -> None:
    export = logged_in.post("/api/accountant-export/exports", json=QUARTER).json()

    assert (
        logged_in.get(f"/api/accountant-export/exports/{export['id']}/parts/2").status_code == 404
    )
    unknown = "00000000-0000-7000-8000-00000000dead"
    assert logged_in.get(f"/api/accountant-export/exports/{unknown}").status_code == 404


def test_the_export_names_the_entity_or_every_entity(
    logged_in: TestClient, api_session: Session
) -> None:
    entity = LegalEntityRepository(api_session).default()
    assert entity is not None

    named = logged_in.post(
        "/api/accountant-export/exports", json={**QUARTER, "legal_entity_id": str(entity.id)}
    )
    everything = logged_in.post(
        "/api/accountant-export/exports", json={**QUARTER, "all_entities": True}
    )
    both = logged_in.post(
        "/api/accountant-export/exports",
        json={**QUARTER, "all_entities": True, "legal_entity_id": str(entity.id)},
    )

    assert named.status_code == 201, named.text
    assert everything.json()["scope"] == "all_entities"
    assert "tutte-le-aziende" in everything.json()["parts"][0]["filename"]
    assert both.status_code == 422


def test_the_period_is_required_and_bounded(logged_in: TestClient) -> None:
    assert logged_in.post("/api/accountant-export/exports", json={}).status_code == 422
    backwards = logged_in.post(
        "/api/accountant-export/exports",
        json={"date_from": "2026-03-31", "date_to": "2026-01-01"},
    )
    assert backwards.status_code == 422
    assert backwards.json()["field"] == "date_from"


def test_a_collaborator_and_a_reader_are_refused(
    collaborator_client: TestClient, readonly_client: TestClient
) -> None:
    for client in (collaborator_client, readonly_client):
        refused = client.post("/api/accountant-export/exports", json=QUARTER)
        assert refused.status_code == 403
        assert refused.json()["code"] == "permission_denied"
        assert client.get("/api/accountant-export/exports").status_code == 403
        assert client.get("/api/accountant-export/parts").status_code == 403


def test_a_visitor_with_no_session_is_refused(client: TestClient) -> None:
    assert client.post("/api/accountant-export/exports", json=QUARTER).status_code == 401
    assert client.get("/api/accountant-export/exports").status_code == 401
    assert client.get("/api/accountant-export/parts").status_code == 401
