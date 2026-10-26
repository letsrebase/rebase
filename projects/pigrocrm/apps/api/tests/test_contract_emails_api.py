"""REB-753 at the wire: the templates of a contract, the preview of what would leave and
the draft that carries the invoice and the register. Sending is `/api/email-drafts`."""

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pigrocrm.core.work_units.models import WorkUnit
from test_contract_billing_api import contract, fiscal_setup  # noqa: F401  (fixtures)

BODY = "Gentile {{cliente}}, fattura {{numero_fattura}} di {{importo}}, registro {{periodo}}."


def _template(logged_in: TestClient, contract: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    payload = {
        "nome": "Fattura mensile",
        "oggetto": "Fattura {{numero_fattura}}",
        "corpo": BODY,
        "allegati": ["fattura_pdf", "registro_pdf"],
        **overrides,
    }
    response = logged_in.post(f"/api/contracts/{contract['id']}/email-templates", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _issued_invoice(logged_in: TestClient, api_session: Session, contract: dict[str, Any]) -> str:
    for day in (date(2026, 10, 7), date(2026, 10, 8)):
        api_session.add(
            WorkUnit(
                contract_id=UUID(contract["id"]),
                data=day,
                quantita=Decimal("1.00"),
                descrizione="Giornata",
                stato="lavorato",
            )
        )
    api_session.flush()
    draft = logged_in.post(f"/api/contracts/{contract['id']}/invoice-days", json={}).json()
    issued = logged_in.post(f"/api/invoices/{draft['id']}/issue", json={})
    assert issued.status_code == 200, issued.text
    return draft["id"]


def test_a_template_is_saved_and_an_unknown_variable_is_a_422(
    logged_in: TestClient, contract: dict[str, Any]
) -> None:
    template = _template(logged_in, contract)
    assert template["allegati"] == ["fattura_pdf", "registro_pdf"]
    listed = logged_in.get(f"/api/contracts/{contract['id']}/email-templates").json()
    assert [t["nome"] for t in listed] == ["Fattura mensile"]

    refused = logged_in.post(
        f"/api/contracts/{contract['id']}/email-templates",
        json={"nome": "Rotto", "oggetto": "x", "corpo": "{{inventata}}"},
    )
    assert refused.status_code == 422, refused.text
    duplicate = logged_in.post(
        f"/api/contracts/{contract['id']}/email-templates",
        json={"nome": "fattura mensile", "oggetto": "x", "corpo": "y"},
    )
    assert duplicate.status_code == 409, duplicate.text


def test_the_variables_a_template_may_name_are_listed(
    logged_in: TestClient, contract: dict[str, Any]
) -> None:
    names = [
        v["nome"]
        for v in logged_in.get(f"/api/contracts/{contract['id']}/email-template-variables").json()
    ]
    assert "numero_fattura" in names and "emittente.ragione_sociale" in names


def test_preview_then_draft_with_both_files_attached(
    logged_in: TestClient, api_session: Session, contract: dict[str, Any]
) -> None:
    template = _template(logged_in, contract)
    invoice_id = _issued_invoice(logged_in, api_session, contract)
    request = {"template_id": template["id"], "invoice_id": invoice_id}

    preview = logged_in.post(f"/api/contracts/{contract['id']}/email/preview", json=request)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["subject"].startswith("Fattura 20")
    assert "Acme S.r.l." in body["body"] and "07/10/2026 - 08/10/2026" in body["body"]
    assert [a["tipo"] for a in body["allegati"]] == ["fattura_pdf", "registro_pdf"]

    draft = logged_in.post(
        f"/api/contracts/{contract['id']}/email/draft",
        json={**request, "to_addresses": ["cliente@example.test"]},
    )
    assert draft.status_code == 201, draft.text
    assert draft.json()["send_state"] == "bozza"
    assert len(draft.json()["attachment_version_ids"]) == 2
    assert draft.json()["subject"] == body["subject"]


def test_a_draft_invoice_cannot_be_previewed(
    logged_in: TestClient, api_session: Session, contract: dict[str, Any]
) -> None:
    template = _template(logged_in, contract)
    api_session.add(
        WorkUnit(
            contract_id=UUID(contract["id"]),
            data=date(2026, 10, 7),
            quantita=Decimal("1.00"),
            descrizione="Giornata",
            stato="lavorato",
        )
    )
    api_session.flush()
    draft = logged_in.post(f"/api/contracts/{contract['id']}/invoice-days", json={}).json()
    response = logged_in.post(
        f"/api/contracts/{contract['id']}/email/preview",
        json={"template_id": template["id"], "invoice_id": draft["id"]},
    )
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("path", ["email-templates"])
def test_a_reader_cannot_save_a_template(
    logged_in: TestClient, contract: dict[str, Any], path: str
) -> None:
    logged_in.post(
        "/api/users",
        json={
            "email": "lettore@pigro.it",
            "password": "supersegreta1",
            "nome": "L",
            "ruolo": "readonly",
        },
    )
    reader = TestClient(logged_in.app, base_url="https://testserver")
    assert (
        reader.post(
            "/api/auth/login", json={"email": "lettore@pigro.it", "password": "supersegreta1"}
        ).status_code
        == 200
    )
    response = reader.post(
        f"/api/contracts/{contract['id']}/{path}", json={"nome": "x", "oggetto": "x", "corpo": "y"}
    )
    assert response.status_code == 403, response.text
