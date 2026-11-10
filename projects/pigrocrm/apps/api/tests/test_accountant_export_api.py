"""The accountant's period bundle over REST (REB-1011): an admin downloads a quarter's ZIP with
the sales register, the invoices' files and the index; everyone else is refused. What the
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


def test_an_admin_downloads_the_quarter_as_a_zip_with_the_index(
    logged_in: TestClient, api_session: Session
) -> None:
    _invoice(api_session)

    response = logged_in.get("/api/accountant-export", params=QUARTER)

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"].startswith(
        "attachment; filename*=UTF-8''commercialista-"
    )
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert {"indice.csv", "vendite/registro-vendite.csv"} <= set(archive.namelist())
    assert "Registro delle vendite" in archive.read("vendite/registro-vendite.csv").decode()
    index = archive.read("indice.csv").decode()
    assert "Non disponibile" in index and "Registro degli acquisti" in index


def test_the_bundle_names_the_entity_or_every_entity(
    logged_in: TestClient, api_session: Session
) -> None:
    entity = LegalEntityRepository(api_session).default()
    assert entity is not None

    named = logged_in.get(
        "/api/accountant-export", params={**QUARTER, "legal_entity_id": str(entity.id)}
    )
    everything = logged_in.get("/api/accountant-export", params={**QUARTER, "all_entities": "true"})
    both = logged_in.get(
        "/api/accountant-export",
        params={**QUARTER, "all_entities": "true", "legal_entity_id": str(entity.id)},
    )

    assert named.status_code == 200, named.text
    assert "tutte-le-aziende" in everything.headers["content-disposition"]
    assert both.status_code == 422


def test_the_period_is_required_and_bounded(logged_in: TestClient) -> None:
    assert logged_in.get("/api/accountant-export").status_code == 422
    backwards = logged_in.get(
        "/api/accountant-export", params={"date_from": "2026-03-31", "date_to": "2026-01-01"}
    )
    assert backwards.status_code == 422
    assert backwards.json()["field"] == "date_from"


def test_a_collaborator_and_a_reader_are_refused(
    collaborator_client: TestClient, readonly_client: TestClient
) -> None:
    for client in (collaborator_client, readonly_client):
        refused = client.get("/api/accountant-export", params=QUARTER)
        assert refused.status_code == 403
        assert refused.json()["code"] == "permission_denied"


def test_a_visitor_with_no_session_is_refused(client: TestClient) -> None:
    assert client.get("/api/accountant-export", params=QUARTER).status_code == 401
