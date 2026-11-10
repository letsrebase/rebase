"""REB-972 over HTTP: an upload is read once, a PDF receipt becomes a proposal and nothing else,
a sales XML waits in the issued review, and the model is off until an administrator turns it on.
The model is `ScriptedModel` (conftest), never a provider."""

import json
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient
from scripted_model import ScriptedModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_inbox_api import INVOICE, _listed, _login_as, _upload

from pigrocrm.core.documents.models import Document
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.purchases.models import Purchase

SELLER_VAT = "01234567890"
RECEIPT_ANSWER = {
    "kind": "receipt",
    "confidence": 0.85,
    "confidence_reason": None,
    "excerpt": "Totale 48,00",
    "fields": {"issue_date": "2026-11-03", "total": 48, "issuer_name": "Trattoria"},
}


def _pdf(line: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({line}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)


def _switch_on(client: TestClient) -> None:
    response = client.put(
        "/api/settings/ai-model",
        json={
            "provider": "anthropic",
            "model": "claude-test",
            "api_key": "placeholder-key-0123456789",
            "daily_cap_eur": "5",
            "input_eur_per_mtok": "3",
            "output_eur_per_mtok": "15",
            "enabled": True,
            "privacy_acknowledged": True,
        },
    )
    assert response.status_code == 200, response.text


def _counts(session: Session) -> tuple[int, int, int]:
    return tuple(  # type: ignore[return-value]
        session.execute(select(func.count()).select_from(t)).scalar_one()
        for t in (Document, Purchase, Invoice)
    )


def test_the_model_is_off_by_default_and_a_pdf_waits_for_the_agent(
    logged_in: TestClient, model_call: ScriptedModel
) -> None:
    read = logged_in.get("/api/settings/ai-model")
    assert read.status_code == 200, read.text
    assert read.json()["enabled"] is False and read.json()["has_key"] is False

    item = _upload(logged_in, ("scontrino.pdf", _pdf("Totale 48,00")))[0]
    assert (item["state"], item["kind"], item["proposal_id"]) == ("new", None, None)
    detail = logged_in.get(f"/api/inbox/items/{item['id']}").json()
    assert "Totale 48,00" in detail["extracted_text"]
    assert [i["id"] for i in _listed(logged_in, classified="false")] == [item["id"]]
    assert model_call.requests == []


def test_a_pdf_receipt_makes_a_proposal_and_nothing_else(
    logged_in: TestClient, model_call: ScriptedModel, api_session: Session
) -> None:
    _switch_on(logged_in)
    model_call.say(json.dumps(RECEIPT_ANSWER))
    before = _counts(api_session)

    item = _upload(logged_in, ("scontrino.pdf", _pdf("Totale 48,00")))[0]

    assert (item["state"], item["kind"]) == ("proposed", "receipt")
    proposal = logged_in.get(f"/api/proposals/{item['proposal_id']}").json()
    assert proposal["inbox_item_id"] == item["id"] and proposal["document_id"] is None
    assert (proposal["target_type"], proposal["status"]) == ("receipt", "pending")
    assert _counts(api_session) == before
    # It waits in the proposals queue, where a person decides; nothing files it yet.
    queue = logged_in.get("/api/proposals", params={"status": "pending"}).json()["items"]
    assert [p["id"] for p in queue] == [proposal["id"]]
    refused = logged_in.post(f"/api/proposals/{proposal['id']}/accept", json={})
    assert refused.status_code == 409, refused.text
    assert _counts(api_session) == before


def test_a_sales_xml_is_routed_to_the_issued_review_and_waits(
    logged_in: TestClient, api_session: Session
) -> None:
    api_session.add(LegalEntity(legal_name="Chiara Bianchi", vat_number=SELLER_VAT, country="IT"))
    api_session.flush()
    before = _counts(api_session)

    item = _upload(logged_in, ("fattura.xml", INVOICE))[0]

    assert (item["state"], item["kind"]) == ("new", "sales_invoice")
    assert item["legal_entity_id"] is not None
    review = logged_in.get(f"/api/inbox/items/{item['id']}/issued-review")
    assert review.status_code == 200, review.text
    assert [row["outcome"] for row in review.json()["lines"]] == ["needs_customer_confirmation"]
    assert _counts(api_session) == before


def test_the_remaining_files_are_read_by_the_classify_route(
    logged_in: TestClient, model_call: ScriptedModel
) -> None:
    first = _upload(logged_in, ("a.pdf", _pdf("uno")))[0]  # model off: waits
    assert first["kind"] is None
    _switch_on(logged_in)
    model_call.say(json.dumps(RECEIPT_ANSWER))
    page = logged_in.post("/api/inbox/classify")
    assert page.status_code == 200, page.text
    assert [(i["id"], i["state"]) for i in page.json()["items"]] == [(first["id"], "proposed")]
    # Nothing is left to read, and an item already read is answered as it is.
    assert logged_in.post("/api/inbox/classify").json()["items"] == []
    again = logged_in.post(f"/api/inbox/items/{first['id']}/classify")
    assert again.status_code == 200 and again.json()["state"] == "proposed"


def test_read_false_stores_the_files_and_reads_none(
    logged_in: TestClient, model_call: ScriptedModel
) -> None:
    _switch_on(logged_in)  # the model is on and has no answer queued: reaching it fails the test

    response = logged_in.post(
        "/api/inbox/upload",
        params={"read": "false"},
        files=[
            ("file", ("scontrino.pdf", _pdf("Totale 48,00"), "application/pdf")),
            ("file", ("fattura.xml", INVOICE, "application/xml")),
        ],
    )

    assert response.status_code == 201, response.text
    receipt, xml = response.json()["items"]
    # Stored as the upload made them: nothing classified, nothing proposed, not even the
    # sales XML a default upload routes by itself.
    assert (receipt["state"], receipt["kind"], receipt["proposal_id"]) == ("new", None, None)
    assert (xml["state"], xml["kind"], xml["legal_entity_id"]) == ("new", None, None)
    assert model_call.requests == []
    assert logged_in.get(f"/api/inbox/items/{receipt['id']}/file").status_code == 200
    waiting = {i["id"] for i in _listed(logged_in, classified="false")}
    assert {receipt["id"], xml["id"]} <= waiting


def test_a_collaborator_who_did_not_upload_an_unclassified_item_does_not_see_it(
    logged_in: TestClient, api_session: Session
) -> None:
    item = _upload(logged_in, ("scontrino.pdf", _pdf("Totale 48,00")))[0]
    _login_as(logged_in, api_session, "collaboratore@pigro.it", "collaborator")
    assert logged_in.get(f"/api/inbox/items/{item['id']}").status_code == 404
    assert logged_in.post(f"/api/inbox/items/{item['id']}/classify").status_code == 404
    assert _listed(logged_in) == []


def test_the_model_settings_are_the_admins_and_never_return_the_key(
    logged_in: TestClient, api_session: Session
) -> None:
    _switch_on(logged_in)
    read = logged_in.get("/api/settings/ai-model").json()
    assert read["enabled"] is True and read["has_key"] is True
    assert "placeholder-key" not in json.dumps(read)
    assert "Anthropic" in read["privacy_notice"]
    # Switching it on needs the notice read.
    off = logged_in.put("/api/settings/ai-model", json={"enabled": False})
    assert off.status_code == 200 and off.json()["enabled"] is False
    refused = logged_in.put("/api/settings/ai-model", json={"enabled": True})
    assert refused.status_code == 422, refused.text
    assert "privacy" in refused.text
    # And nobody else reads or writes them.
    _login_as(logged_in, api_session, "collaboratore@pigro.it", "collaborator")
    assert logged_in.get("/api/settings/ai-model").status_code == 403
    assert logged_in.put("/api/settings/ai-model", json={"model": "x"}).status_code == 403
    assert logged_in.post("/api/settings/ai-model/test").status_code == 403


def test_the_test_button_makes_one_call_and_reports_what_it_cost(
    logged_in: TestClient, model_call: ScriptedModel
) -> None:
    _switch_on(logged_in)
    model_call.say("ok", input_tokens=12, output_tokens=2)
    result = logged_in.post("/api/settings/ai-model/test")
    assert result.status_code == 200, result.text
    body: dict[str, Any] = result.json()
    assert (body["ok"], body["input_tokens"], body["output_tokens"]) == (True, 12, 2)
    assert logged_in.get("/api/settings/ai-model").json()["spent_today_eur"] != "0.000000"
    assert len(model_call.requests) == 1


def test_the_list_carries_the_total_of_the_filter_and_each_amount_that_is_known(
    logged_in: TestClient, model_call: ScriptedModel, api_session: Session
) -> None:
    """REB-991: `total` counts what the filter matches whatever the page holds, and an item states
    its amount when a waiting proposal read one or a FatturaPA says it."""
    api_session.add(LegalEntity(legal_name="Chiara Bianchi", vat_number=SELLER_VAT, country="IT"))
    api_session.flush()
    _switch_on(logged_in)
    model_call.say(json.dumps(RECEIPT_ANSWER))
    receipt = _upload(logged_in, ("scontrino.pdf", _pdf("Totale 48,00")))[0]
    invoice = _upload(logged_in, ("fattura.xml", INVOICE))[0]
    plain = _upload(logged_in, ("estratto.csv", b"data;importo\n2026-10-01;10,00\n"))[0]

    page = logged_in.get("/api/inbox/items", params={"limit": 2}).json()
    assert (len(page["items"]), page["total"]) == (2, 3)
    assert (
        logged_in.get(
            "/api/inbox/items", params={"limit": 2, "cursor": page["next_cursor"]}
        ).json()["total"]
        == 3
    )
    assert logged_in.get("/api/inbox/items", params={"state": "proposed"}).json()["total"] == 1
    assert logged_in.get("/api/inbox/items", params={"kind": "receipt"}).json()["total"] == 1

    by_id = {i["id"]: i for i in logged_in.get("/api/inbox/items").json()["items"]}
    assert (Decimal(by_id[receipt["id"]]["amount"]), by_id[receipt["id"]]["currency"]) == (
        Decimal("48"),
        "EUR",
    )
    assert (Decimal(by_id[invoice["id"]]["amount"]), by_id[invoice["id"]]["currency"]) == (
        Decimal("1000.00"),
        "EUR",
    )
    assert by_id[plain["id"]]["amount"] is None
    detail = logged_in.get(f"/api/inbox/items/{invoice['id']}").json()
    assert Decimal(detail["amount"]) == Decimal("1000.00")
