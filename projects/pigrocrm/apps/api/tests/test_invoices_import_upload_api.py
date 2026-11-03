"""REST surface of the import from bytes (REB-758): `POST /api/invoices/import/upload/
review` and `/import/upload/confirm`. `test_role_matrix.py` owns the admin-only gate;
this file proves what the invoices page relies on, through the real HTTP surface: the
review reads and writes nothing, the confirm writes the register and files the XML, and
a year of XML in one ZIP leaves no gap to declare."""

import hashlib
import io
import json
import zipfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from legal_entities_helpers import legal_entity_url

FIXTURES = (
    Path(__file__).resolve().parents[3] / "packages" / "core" / "tests" / "fixtures" / "fatturapa"
)
CONSULTING = "fpr-12-march-consulting.xml"
LOTTO = "fpr-12-batch-due-invoices.xml"
SUPPLIER_VAT_NUMBER = "01234567890"
SUPPLIER_CF = "BNCCHR85M41H501Z"
CUSTOMER_VAT_NUMBER = "09876543210"

REVIEW = "/api/invoices/import/upload/review"
CONFIRM = "/api/invoices/import/upload/confirm"


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _files(*files: tuple[str, bytes]) -> list[tuple[str, tuple[str, bytes, str]]]:
    return [("file", (name, content, "application/octet-stream")) for name, content in files]


def _decisions(by_party: dict[str, dict[str, Any]]) -> dict[str, str]:
    return {"decisioni": json.dumps({"customers": by_party})}


def _year(count: int) -> bytes:
    base = _fixture(CONSULTING).decode()
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for number in range(1, count + 1):
            day = date(2026, 1, 1) + timedelta(days=number - 1)
            xml = base.replace(
                "<Numero>2026/6</Numero>", f"<Numero>2026/{number}</Numero>"
            ).replace("<Data>2026-03-15</Data>", f"<Data>{day.isoformat()}</Data>")
            archive.writestr(f"IT01234567890_{number:05d}.xml", xml)
    return out.getvalue()


@pytest.fixture
def fiscal_profile(logged_in: TestClient) -> dict[str, Any]:
    response = logged_in.put(
        legal_entity_url(logged_in, "/fiscal-profile"), json={"regime_code": "RF19"}
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def legal_entity(logged_in: TestClient) -> dict[str, Any]:
    response = logged_in.put(
        legal_entity_url(logged_in),
        json={
            "legal_name": "Chiara Bianchi",
            "vat_number": SUPPLIER_VAT_NUMBER,
            "tax_code": SUPPLIER_CF,
            "address": "Via delle Officine 10",
            "cap": "20121",
            "city": "Milano",
            "province": "MI",
            "country": "IT",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def customer(logged_in: TestClient) -> dict[str, Any]:
    response = logged_in.post(
        "/api/customers",
        json={
            "legal_name": "Esempio Servizi S.r.l.",
            "vat_number": CUSTOMER_VAT_NUMBER,
            "address": "Piazza dei Modelli 4",
            "cap": "00187",
            "city": "Roma",
            "province": "RM",
            "country": "IT",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_the_review_names_each_file_and_writes_nothing(
    logged_in: TestClient,
    fiscal_profile: dict[str, Any],
    legal_entity: dict[str, Any],
    customer: dict[str, Any],
) -> None:
    content = _fixture(CONSULTING)

    response = logged_in.post(
        REVIEW, files=_files(("fattura.xml", content), ("note.txt", b"nothing to see"))
    )

    assert response.status_code == 200, response.text
    ready, unclaimed = response.json()["lines"]
    assert ready["outcome"] == "ready"
    assert ready["file"] == "fattura.xml"
    assert ready["sha256"] == hashlib.sha256(content).hexdigest()
    assert ready["matched_customer_id"] == customer["id"]
    assert ready["invoice"]["number"] == "2026/6"
    assert (unclaimed["outcome"], unclaimed["file"]) == ("unclaimed", "note.txt")
    listed = logged_in.get("/api/invoices")
    assert listed.json()["items"] == []
    assert logged_in.get("/api/documents").json()["items"] == []


def test_the_confirm_writes_the_register_and_serves_the_file_back(
    logged_in: TestClient,
    fiscal_profile: dict[str, Any],
    legal_entity: dict[str, Any],
    customer: dict[str, Any],
) -> None:
    content = _fixture(CONSULTING)

    response = logged_in.post(CONFIRM, files=_files(("fattura.xml", content)))

    assert response.status_code == 200, response.text
    [row] = response.json()["lines"]
    assert row["outcome"] == "imported"
    invoice = row["invoice"]
    assert (invoice["number"], invoice["imported_from"]) == (6, "fatturapa")
    assert invoice["customer_id"] == customer["id"]
    assert invoice["xml_hash_sha256"] == hashlib.sha256(content).hexdigest()
    served = logged_in.get(f"/api/invoices/{invoice['id']}/xml")
    assert served.status_code == 200, served.text
    assert served.content == content
    [document] = logged_in.get("/api/documents").json()["items"]
    assert (document["customer_id"], document["type"]) == (customer["id"], "invoice_xml")


def test_a_counterparty_nobody_filed_is_created_only_on_the_decision_for_its_customer(
    logged_in: TestClient,
    fiscal_profile: dict[str, Any],
    legal_entity: dict[str, Any],
) -> None:
    content = _fixture(CONSULTING)
    review_row = logged_in.post(REVIEW, files=_files(("fattura.xml", content))).json()["lines"][0]
    assert review_row["party_key"] == CUSTOMER_VAT_NUMBER

    withheld = logged_in.post(CONFIRM, files=_files(("fattura.xml", content)))
    assert withheld.json()["lines"][0]["outcome"] == "needs_customer_confirmation"

    created = logged_in.post(
        CONFIRM,
        files=_files(("fattura.xml", content)),
        data=_decisions({review_row["party_key"]: {"create_customer": True}}),
    )
    assert created.status_code == 200, created.text
    [row] = created.json()["lines"]
    assert row["outcome"] == "imported"
    customers = logged_in.get("/api/customers").json()["items"]
    assert [c["vat_number"] for c in customers] == [CUSTOMER_VAT_NUMBER]


def test_to_customer_of_another_legal_entity_is_to_422_that_names_the_party_and_writes_nothing(
    logged_in: TestClient,
    fiscal_profile: dict[str, Any],
    legal_entity: dict[str, Any],
    customer: dict[str, Any],
) -> None:
    """REB-783: the dialog lists only the file's legal entity's customers, but the id is the
    caller's, so the confirmation itself refuses one from another legal entity, and one of
    the right legal entity still imports."""
    content = _fixture(CONSULTING)
    ltd = logged_in.post(
        "/api/legal-entities",
        json={
            "name": "acme ltd",
            "legal_name": "Acme Ltd",
            "vat_number": "GB123456789",
            "country": "GB",
            "address": "1 Poultry",
            "city": "London",
            "fiscal_profile": {"pack_id": "non-it", "default_vat_rate": "20.00"},
        },
    ).json()
    foreign = logged_in.post(
        "/api/customers",
        json={"legal_name": "Acme Customer Ltd", "country": "GB", "legal_entity_id": ltd["id"]},
    )
    assert foreign.status_code == 201, foreign.text
    party = CUSTOMER_VAT_NUMBER

    refused = logged_in.post(
        CONFIRM,
        files=_files(("fattura.xml", content)),
        data=_decisions({party: {"customer_id": foreign.json()["id"]}}),
    )

    assert refused.status_code == 422, refused.text
    assert "Esempio Servizi S.r.l." in refused.text
    assert logged_in.get("/api/invoices").json()["items"] == []
    assert logged_in.get("/api/documents").json()["items"] == []

    accepted = logged_in.post(
        CONFIRM,
        files=_files(("fattura.xml", content)),
        data=_decisions({party: {"customer_id": customer["id"]}}),
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["lines"][0]["outcome"] == "imported"


def test_a_year_of_xml_in_one_zip_imports_with_no_gap_to_declare(
    logged_in: TestClient,
    fiscal_profile: dict[str, Any],
    legal_entity: dict[str, Any],
) -> None:
    archive = ("fatture-2026.zip", _year(12))

    reviewed = logged_in.post(REVIEW, files=_files(archive))
    assert reviewed.status_code == 200, reviewed.text
    lines = reviewed.json()["lines"]
    assert len(lines) == 12
    assert {r["file"].split("/")[0] for r in lines} == {"fatture-2026.zip"}
    decisions = _decisions({r["party_key"]: {"create_customer": True} for r in lines})

    confirmed = logged_in.post(CONFIRM, files=_files(archive), data=decisions)

    assert confirmed.status_code == 200, confirmed.text
    assert [r["outcome"] for r in confirmed.json()["lines"]] == ["imported"] * 12
    gaps = logged_in.get("/api/invoices/register/2026/gaps")
    assert gaps.status_code == 200, gaps.text
    assert gaps.json() == []
    last = confirmed.json()["lines"][-1]
    assert last["undeclared_gaps"] == []


def test_a_zip_that_does_not_open_is_a_422_with_its_name(
    logged_in: TestClient,
    fiscal_profile: dict[str, Any],
    legal_entity: dict[str, Any],
) -> None:
    response = logged_in.post(REVIEW, files=_files(("rotto.zip", b"PK\x03\x04 broken")))

    assert response.status_code == 422, response.text
    assert "rotto.zip" in response.text


def test_decisions_that_are_not_json_are_a_422_not_a_crash(
    logged_in: TestClient,
    fiscal_profile: dict[str, Any],
    legal_entity: dict[str, Any],
) -> None:
    response = logged_in.post(
        CONFIRM, files=_files(("a.xml", b"<a/>")), data={"decisioni": "not json"}
    )

    assert response.status_code == 422, response.text


def test_to_file_that_looks_like_to_invoice_and_does_not_parse_is_named_and_does_not_sink_the_rest(
    logged_in: TestClient,
    fiscal_profile: dict[str, Any],
    legal_entity: dict[str, Any],
    customer: dict[str, Any],
) -> None:
    broken = _fixture(CONSULTING).replace(b"TD06", b"TD99")
    files = _files(("rotta.xml", broken), ("buona.xml", _fixture(CONSULTING)))

    reviewed = logged_in.post(REVIEW, files=files)
    assert reviewed.status_code == 200, reviewed.text
    bad, good = reviewed.json()["lines"]
    assert (bad["file"], bad["outcome"]) == ("rotta.xml", "unclaimed")
    assert "TD99" in bad["reason"]
    assert good["outcome"] == "ready"

    confirmed = logged_in.post(CONFIRM, files=files)
    assert confirmed.status_code == 200, confirmed.text
    bad, good = confirmed.json()["lines"]
    assert (bad["outcome"], good["outcome"]) == ("unclaimed", "imported")


def test_to_collaborator_is_refused_before_the_body_is_looked_at(
    collaborator_client: TestClient,
) -> None:
    response = collaborator_client.post(
        CONFIRM, files=_files(("a.xml", b"<a/>")), data={"decisioni": "not json"}
    )

    assert response.status_code == 403, response.text
