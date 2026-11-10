"""REB-984 over HTTP: what a person says about an item that waits, by `POST /api/inbox/items/{id}/`
`discard`, `duplicate` and `kind`: who may, who sees the item, what is idempotent, what is refused,
and that each writes an activity entry. The model is `ScriptedModel`, never a provider."""

import json
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from scripted_model import ScriptedModel
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_inbox_api import INVOICE, PASSWORD, PDF, _listed, _login_as, _upload, _zip
from test_inbox_classify_api import RECEIPT_ANSWER, _pdf, _switch_on

from pigrocrm.core.activities.models import Activity
from pigrocrm.core.legal_entities.models import LegalEntity

ITEMS = "/api/inbox/items"


def _discard(client: TestClient, item_id: str, reason: str = "Non è nostro") -> Any:
    return client.post(f"{ITEMS}/{item_id}/discard", json={"reason": reason})


def _duplicate(client: TestClient, item_id: str, original_id: str) -> Any:
    return client.post(f"{ITEMS}/{item_id}/duplicate", json={"original_id": original_id})


def _kind(client: TestClient, item_id: str, kind: str, **extra: Any) -> Any:
    return client.post(f"{ITEMS}/{item_id}/kind", json={"kind": kind, **extra})


def _entries(session: Session, item_id: str) -> list[str]:
    rows = session.execute(
        select(Activity.kind)
        .where(Activity.entity_type == "inbox_item", Activity.entity_id == UUID(item_id))
        .order_by(Activity.occurred_at)
    ).scalars()
    return list(rows)


# ---- discard ---------------------------------------------------------------------------


def test_a_discarded_item_stays_in_the_inbox_with_its_reason_and_its_file(
    logged_in: TestClient, api_session: Session
) -> None:
    item = _upload(logged_in, ("a.pdf", PDF))[0]

    response = _discard(logged_in, item["id"], "  Non è nostro  ")

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["state"], body["reason"], body["has_file"]) == ("discarded", "Non è nostro", True)
    assert [i["id"] for i in _listed(logged_in, state="discarded")] == [item["id"]]
    assert logged_in.get(f"{ITEMS}/{item['id']}/file").content == PDF
    assert _entries(api_session, item["id"]) == ["discarded"]


def test_discarding_needs_a_reason(logged_in: TestClient) -> None:
    item = _upload(logged_in, ("a.pdf", PDF))[0]
    assert _discard(logged_in, item["id"], "   ").status_code == 422
    assert logged_in.post(f"{ITEMS}/{item['id']}/discard", json={}).status_code == 422
    assert logged_in.get(f"{ITEMS}/{item['id']}").json()["state"] == "new"


def test_discarding_twice_is_the_same_answer_and_one_entry(
    logged_in: TestClient, api_session: Session
) -> None:
    item = _upload(logged_in, ("a.pdf", PDF))[0]
    first = _discard(logged_in, item["id"], "Primo motivo").json()
    second = _discard(logged_in, item["id"], "Altro motivo")
    assert second.status_code == 200
    assert second.json()["reason"] == first["reason"] == "Primo motivo"
    assert _entries(api_session, item["id"]) == ["discarded"]


def test_a_filed_item_and_a_duplicate_cannot_be_discarded(logged_in: TestClient) -> None:
    original = _upload(logged_in, ("a.pdf", PDF))[0]
    copy = _upload(logged_in, ("c.pdf", PDF))[0]
    assert (copy["state"], copy["duplicate_of_id"]) == ("duplicate", original["id"])
    refused = _discard(logged_in, copy["id"])
    assert refused.status_code == 409
    assert "doppione" in refused.json()["detail"]

    archive = _upload(logged_in, ("lotto.zip", _zip({"b.pdf": PDF + b"1"})))[0]
    assert archive["state"] == "filed"
    assert _discard(logged_in, archive["id"]).status_code == 409


def test_a_readonly_user_cannot_discard_and_another_collaborator_does_not_see_the_item(
    client: TestClient, api_session: Session, admin_user: Any
) -> None:
    _login_as(client, api_session, "uno@pigro.it", "collaborator")
    mine = _upload(client, ("mio.pdf", PDF))[0]

    _login_as(client, api_session, "due@pigro.it", "collaborator")
    assert _discard(client, mine["id"]).status_code == 404
    assert _duplicate(client, mine["id"], mine["id"]).status_code == 404
    assert _kind(client, mine["id"], "receipt").status_code == 404

    _login_as(client, api_session, "lettore@pigro.it", "readonly")
    for response in (
        _discard(client, mine["id"]),
        _duplicate(client, mine["id"], mine["id"]),
        _kind(client, mine["id"], "receipt"),
    ):
        assert response.status_code == 403
        assert response.json()["code"] == "permission_denied"

    client.post("/api/auth/login", json={"email": "uno@pigro.it", "password": PASSWORD})
    assert _discard(client, mine["id"]).status_code == 200


def test_discarding_an_item_with_a_proposal_rejects_the_proposal(
    logged_in: TestClient, model_call: ScriptedModel
) -> None:
    _switch_on(logged_in)
    model_call.say(json.dumps(RECEIPT_ANSWER))
    item = _upload(logged_in, ("scontrino.pdf", _pdf("Totale 48,00")))[0]
    assert item["state"] == "proposed"

    response = _discard(logged_in, item["id"], "Non serve")

    assert response.status_code == 200, response.text
    assert (response.json()["state"], response.json()["proposal_id"]) == ("discarded", None)
    proposal = logged_in.get(f"/api/proposals/{item['proposal_id']}").json()
    assert proposal["status"] == "rejected"


def test_the_same_file_uploaded_after_a_discard_stays_discarded(logged_in: TestClient) -> None:
    first = _upload(logged_in, ("a.pdf", PDF))[0]
    _discard(logged_in, first["id"], "Non è nostro")

    again = _upload(logged_in, ("copia.pdf", PDF))[0]

    assert (again["state"], again["duplicate_of_id"]) == ("duplicate", first["id"])
    assert "scartato" in (again["reason"] or "")
    assert logged_in.get(f"{ITEMS}/{first['id']}").json()["state"] == "discarded"


# ---- duplicate -------------------------------------------------------------------------


def test_an_item_is_marked_as_the_copy_of_another_and_stays_marked(
    logged_in: TestClient, api_session: Session
) -> None:
    original = _upload(logged_in, ("a.pdf", PDF))[0]
    other = _upload(logged_in, ("b.pdf", PDF + b"x"))[0]

    response = _duplicate(logged_in, other["id"], original["id"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["state"], body["duplicate_of_id"]) == ("duplicate", original["id"])
    assert "a.pdf" in body["reason"]
    assert [i["id"] for i in _listed(logged_in, state="duplicate")] == [other["id"]]
    again = _duplicate(logged_in, other["id"], original["id"])
    assert again.status_code == 200 and again.json() == body
    assert _entries(api_session, other["id"]) == ["marked_duplicate"]


def test_a_copy_cannot_point_at_itself_or_at_another_copy_or_be_moved_to_a_second_original(
    logged_in: TestClient,
) -> None:
    original = _upload(logged_in, ("a.pdf", PDF))[0]
    second = _upload(logged_in, ("b.pdf", PDF + b"1"))[0]
    copy = _upload(logged_in, ("c.pdf", PDF + b"2"))[0]
    assert _duplicate(logged_in, copy["id"], original["id"]).status_code == 200

    own = _duplicate(logged_in, second["id"], second["id"])
    assert own.status_code == 422
    chained = _duplicate(logged_in, second["id"], copy["id"])
    assert chained.status_code == 422
    assert str(original["id"]) in chained.text
    moved = _duplicate(logged_in, copy["id"], second["id"])
    assert moved.status_code == 409
    assert _duplicate(logged_in, second["id"], str(uuid4())).status_code == 404


def test_the_copies_of_an_item_follow_it_when_it_becomes_a_copy(logged_in: TestClient) -> None:
    original = _upload(logged_in, ("a.pdf", PDF))[0]
    middle = _upload(logged_in, ("b.pdf", PDF + b"1"))[0]
    copy_of_middle = _upload(logged_in, ("c.pdf", PDF + b"1"))[0]
    assert copy_of_middle["duplicate_of_id"] == middle["id"]

    assert _duplicate(logged_in, middle["id"], original["id"]).status_code == 200

    moved = logged_in.get(f"{ITEMS}/{copy_of_middle['id']}").json()
    assert moved["duplicate_of_id"] == original["id"]


def test_an_item_that_is_not_waiting_cannot_become_a_copy(logged_in: TestClient) -> None:
    original = _upload(logged_in, ("a.pdf", PDF))[0]
    other = _upload(logged_in, ("b.pdf", PDF + b"1"))[0]
    _discard(logged_in, other["id"])
    refused = _duplicate(logged_in, other["id"], original["id"])
    assert refused.status_code == 409


# ---- kind ------------------------------------------------------------------------------


def test_a_person_says_what_an_item_is_and_nothing_is_filed(
    logged_in: TestClient, api_session: Session
) -> None:
    item = _upload(logged_in, ("a.pdf", PDF))[0]
    assert item["kind"] is None

    response = _kind(logged_in, item["id"], "supplier_invoice")

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["kind"], body["state"]) == ("supplier_invoice", "new")
    assert "persona" in body["reason"]
    assert [i["id"] for i in _listed(logged_in, classified="false")] == []
    assert _kind(logged_in, item["id"], "supplier_invoice").json() == body
    assert _entries(api_session, item["id"]) == ["kind_set"]


def test_the_kinds_are_the_designs_and_never_archive(logged_in: TestClient) -> None:
    item = _upload(logged_in, ("a.pdf", PDF))[0]
    assert _kind(logged_in, item["id"], "archive").status_code == 422
    assert _kind(logged_in, item["id"], "nonsense").status_code == 422
    for kind in ("receipt", "contract", "tax_document", "payslip", "bank_statement", "other"):
        assert _kind(logged_in, item["id"], kind).json()["kind"] == kind


def test_a_sales_invoice_is_only_an_xml_or_signed_xml_file(logged_in: TestClient) -> None:
    item = _upload(logged_in, ("a.pdf", PDF))[0]
    refused = _kind(logged_in, item["id"], "sales_invoice")
    assert refused.status_code == 422
    assert "XML" in refused.json()["detail"]


def test_a_kind_a_fatturapa_decided_by_itself_does_not_change_but_its_company_can_be_chosen(
    logged_in: TestClient, api_session: Session
) -> None:
    entity = LegalEntity(legal_name="Chiara Bianchi", vat_number="11111111119", country="IT")
    api_session.add(entity)
    api_session.flush()
    item = _upload(logged_in, ("fattura.xml", INVOICE))[0]
    assert (item["kind"], item["legal_entity_id"]) == ("supplier_invoice", None)

    refused = _kind(logged_in, item["id"], "receipt")
    assert refused.status_code == 409

    chosen = _kind(logged_in, item["id"], "supplier_invoice", legal_entity_id=str(entity.id))
    assert chosen.status_code == 200, chosen.text
    assert chosen.json()["legal_entity_id"] == str(entity.id)
    unknown = _kind(logged_in, item["id"], "supplier_invoice", legal_entity_id=str(uuid4()))
    assert unknown.status_code == 422


def test_changing_the_kind_of_a_proposed_item_rejects_its_proposal(
    logged_in: TestClient, model_call: ScriptedModel
) -> None:
    _switch_on(logged_in)
    model_call.say(json.dumps(RECEIPT_ANSWER))
    item = _upload(logged_in, ("scontrino.pdf", _pdf("Totale 48,00")))[0]
    assert item["state"] == "proposed"

    response = _kind(logged_in, item["id"], "supplier_invoice")

    assert response.status_code == 200, response.text
    assert (response.json()["kind"], response.json()["state"]) == ("supplier_invoice", "new")
    assert logged_in.get(f"/api/proposals/{item['proposal_id']}").json()["status"] == "rejected"
