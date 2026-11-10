"""Bank accounts, statements and movements over REST (REB-1025, cash design §§ 2 and 4): an
administrator keeps them, the upload reads a CAMT.053 or the Banco BPM export, the revert undoes
an import, and everything answers 404 while the `cash` module is off. The balance, the coverage
and the checks are proven in `packages/core/tests/test_bank_accounts.py`."""

from io import BytesIO
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.bank.accounts import BankAccountService
from pigrocrm.core.bank.schemas import BankAccountCreate
from pigrocrm.core.bank.statements import STATEMENT_SIZE_MAX, StatementImportService
from pigrocrm_api.deps import invalidate_space_settings

IBAN = "IT60X0542811101000000123456"
CAMT = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "core"
    / "tests"
    / "fixtures"
    / "camt053"
    / "statement_001_02.xml"
)
SYSTEM = Actor(id=None, type="system", role="admin")
ACCOUNT = {
    "name": "Main",
    "bank": "Banco BPM",
    "iban": IBAN,
    "opening_balance": "1000.00",
    "opening_balance_date": "2026-09-30",
}


def _account(client: TestClient, **extra: Any) -> dict[str, Any]:
    response = client.post("/api/bank-accounts", json={**ACCOUNT, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def _seed(session: Session) -> tuple[str, str]:
    """An account with one imported statement, written through the services: the role tests
    below hold one login each, so the data cannot be made over REST by the administrator."""
    account = BankAccountService(session).create_bank_account(
        BankAccountCreate.model_validate(ACCOUNT), SYSTEM
    )
    statement = (
        StatementImportService(session).import_file(account.id, CAMT.read_bytes(), SYSTEM).statement
    )
    return str(account.id), str(statement.id)


def _upload(
    client: TestClient,
    account_id: str,
    content: bytes,
    *,
    name: str = "estratto.xml",
    **fields: str,
):
    return client.post(
        f"/api/bank-accounts/{account_id}/statements",
        files={"file": (name, content, "application/octet-stream")},
        data=fields,
    )


def _xlsx() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "movimentiConto"
    sheet.append(
        [
            "Data Contabile",
            "Data Valuta",
            "Importo",
            "Divisa",
            "Causale / Descrizione",
            "Stato ",
            "Canale",
        ]
    )
    sheet.append(["09/11/2026", "09/11/2026", 300.5, "EUR", "BON.DA X", "Cont.", ""])
    out = BytesIO()
    workbook.save(out)
    return out.getvalue()


def test_an_account_is_created_listed_changed_and_archived(logged_in: TestClient) -> None:
    made = _account(logged_in, iban="it60 x054 2811 1010 0000 0123 456", cadence="quarterly")
    assert (made["iban"], made["balance"], made["balance_as_of"], made["is_stale"]) == (
        IBAN,
        "1000.00",
        "2026-09-30",
        False,  # a quarterly account is read once a quarter
    )
    assert made["below_zero"] is False
    assert made["archived_at"] is None and made["legal_entity_name"]
    assert [a["id"] for a in logged_in.get("/api/bank-accounts").json()] == [made["id"]]

    changed = logged_in.patch(f"/api/bank-accounts/{made['id']}", json={"name": "Operating"})
    assert changed.status_code == 200, changed.text
    assert (changed.json()["name"], changed.json()["cadence"]) == ("Operating", "quarterly")

    archived = logged_in.post(f"/api/bank-accounts/{made['id']}/archive")
    assert archived.status_code == 200 and archived.json()["archived_at"] is not None
    assert logged_in.get("/api/bank-accounts").json() == []
    shown = logged_in.get("/api/bank-accounts?include_archived=true").json()
    assert [a["id"] for a in shown] == [made["id"]]
    assert logged_in.post(f"/api/bank-accounts/{made['id']}/archive").status_code == 409


def test_a_bad_body_a_duplicate_iban_and_an_unknown_account_are_refused_in_words(
    logged_in: TestClient,
) -> None:
    _account(logged_in)
    assert logged_in.post("/api/bank-accounts", json={**ACCOUNT, "iban": "IT00"}).status_code == 422
    half = {k: v for k, v in ACCOUNT.items() if k != "opening_balance_date"}
    assert (
        logged_in.post(
            "/api/bank-accounts", json={**half, "iban": "DE89370400440532013000"}
        ).status_code
        == 422
    )
    duplicate = logged_in.post("/api/bank-accounts", json=ACCOUNT)
    assert duplicate.status_code == 409 and duplicate.json()["code"] == "conflict"
    missing = "00000000-0000-0000-0000-000000000000"
    assert logged_in.patch(f"/api/bank-accounts/{missing}", json={"name": "x"}).status_code == 404
    assert logged_in.get(f"/api/bank-accounts/{missing}/coverage").status_code == 404
    assert logged_in.get(f"/api/bank-accounts/{missing}/movements").status_code == 404


def test_the_upload_imports_a_camt_file_and_a_second_upload_adds_nothing(
    logged_in: TestClient,
) -> None:
    account = _account(logged_in)
    first = _upload(logged_in, account["id"], CAMT.read_bytes())
    assert first.status_code == 201, first.text
    body = first.json()
    assert (body["added"], body["duplicates"]) == (4, 0)
    statement = body["statement"]
    assert (statement["format"], statement["state"], statement["movement_count"]) == (
        "camt053",
        "imported",
        4,
    )
    assert statement["check"] == {"ok": True, "difference": "0.00", "kind": None}
    second = _upload(logged_in, account["id"], CAMT.read_bytes())
    assert (second.json()["added"], second.json()["duplicates"]) == (0, 4)

    refreshed = logged_in.get("/api/bank-accounts").json()[0]
    assert (refreshed["balance"], refreshed["balance_as_of"], refreshed["unmatched_count"]) == (
        "2170.00",
        "2026-10-31",
        4,
    )


def test_the_statements_the_movements_and_the_coverage_are_read_back(
    logged_in: TestClient,
) -> None:
    account = _account(logged_in)
    _upload(logged_in, account["id"], CAMT.read_bytes())
    statements = logged_in.get(f"/api/bank-accounts/{account['id']}/statements").json()
    assert statements["total_count"] == 1 and statements["next_cursor"] is None

    page = logged_in.get(f"/api/bank-accounts/{account['id']}/movements?limit=3").json()
    assert (len(page["items"]), page["total_count"], page["unmatched_count"]) == (3, 4, 4)
    assert page["next_cursor"] is not None
    rest = logged_in.get(
        f"/api/bank-accounts/{account['id']}/movements",
        params={"limit": 3, "cursor": page["next_cursor"]},
    ).json()
    assert len(rest["items"]) == 1 and rest["next_cursor"] is None
    movement = page["items"][0]
    assert movement["state"] == "unmatched" and movement["statement_ids"] == [
        statements["items"][0]["id"]
    ]
    by_statement = logged_in.get(
        f"/api/bank-accounts/{account['id']}/movements",
        params={"statement_id": statements["items"][0]["id"], "state": "unmatched"},
    ).json()
    assert by_statement["total_count"] == 4
    assert (
        logged_in.get(
            f"/api/bank-accounts/{account['id']}/movements", params={"limit": 0}
        ).status_code
        == 422
    )

    coverage = logged_in.get(f"/api/bank-accounts/{account['id']}/coverage").json()
    assert coverage["grace_days"] == 7 and len(coverage["months"]) == 12
    assert {m["state"] for m in coverage["months"]} <= {"covered", "missing", "pending"}


def test_an_xlsx_without_a_period_is_a_422_naming_the_field_and_with_one_is_imported(
    logged_in: TestClient,
) -> None:
    account = _account(logged_in)
    refused = _upload(logged_in, account["id"], _xlsx(), name="saldo.xlsx")
    assert refused.status_code == 422 and refused.json()["field"] == "period"
    done = _upload(
        logged_in,
        account["id"],
        _xlsx(),
        name="saldo.xlsx",
        period_from="2026-11-01",
        period_to="2026-11-30",
    )
    assert done.status_code == 201, done.text
    assert done.json()["statement"]["format"] == "banco_bpm_xlsx"
    half = _upload(logged_in, account["id"], _xlsx(), period_from="2026-11-01")
    assert half.status_code == 422
    bad_date = _upload(logged_in, account["id"], _xlsx(), period_from="x", period_to="y")
    assert bad_date.status_code == 422


def test_a_file_in_no_known_format_or_over_the_cap_is_a_422_and_nothing_is_written(
    logged_in: TestClient,
) -> None:
    account = _account(logged_in)
    unknown = _upload(logged_in, account["id"], b"hello")
    assert unknown.status_code == 422 and "formato non riconosciuto" in unknown.json()["detail"]
    big = _upload(logged_in, account["id"], b"<" + b"x" * STATEMENT_SIZE_MAX)
    assert big.status_code == 422
    assert (
        logged_in.get(f"/api/bank-accounts/{account['id']}/statements").json()["total_count"] == 0
    )
    no_file = logged_in.post(
        f"/api/bank-accounts/{account['id']}/statements", data={"period_from": "2026-11-01"}
    )
    assert no_file.status_code == 422


def test_the_revert_removes_the_movements_and_a_second_revert_is_a_409(
    logged_in: TestClient,
) -> None:
    account = _account(logged_in)
    statement = _upload(logged_in, account["id"], CAMT.read_bytes()).json()["statement"]
    reverted = logged_in.post(f"/api/bank-statements/{statement['id']}/revert")
    assert reverted.status_code == 200, reverted.text
    assert (reverted.json()["state"], reverted.json()["movement_count"]) == ("reverted", 0)
    assert logged_in.get(f"/api/bank-accounts/{account['id']}/movements").json()["total_count"] == 0
    assert logged_in.get("/api/bank-accounts").json()[0]["balance"] == "1000.00"
    assert logged_in.post(f"/api/bank-statements/{statement['id']}/revert").status_code == 409
    missing = "00000000-0000-0000-0000-000000000000"
    assert logged_in.post(f"/api/bank-statements/{missing}/revert").status_code == 404


def _every_route(account: str, statement: str) -> list[tuple[str, str]]:
    return [
        ("GET", "/api/bank-accounts"),
        ("POST", "/api/bank-accounts"),
        ("PATCH", f"/api/bank-accounts/{account}"),
        ("POST", f"/api/bank-accounts/{account}/archive"),
        ("GET", f"/api/bank-accounts/{account}/coverage"),
        ("GET", f"/api/bank-accounts/{account}/statements"),
        ("POST", f"/api/bank-accounts/{account}/statements"),
        ("GET", f"/api/bank-accounts/{account}/movements"),
        ("POST", f"/api/bank-statements/{statement}/revert"),
    ]


def _call(client: TestClient, method: str, path: str) -> Any:
    if method == "POST" and path.endswith("/statements"):
        return _upload(client, path.split("/")[3], CAMT.read_bytes())
    body = ACCOUNT if (method, path) == ("POST", "/api/bank-accounts") else {"name": "x"}
    return client.request(method, path, json=body if method in {"POST", "PATCH"} else None)


def _assert_refused_everywhere(client: TestClient, session: Session) -> None:
    account, statement = _seed(session)
    for method, path in _every_route(account, statement):
        response = _call(client, method, path)
        assert response.status_code == 403, (method, path, response.text)
        assert response.json()["code"] == "permission_denied", (method, path)


def test_a_collaborator_is_refused_every_route(
    collaborator_client: TestClient, api_session: Session
) -> None:
    _assert_refused_everywhere(collaborator_client, api_session)


def test_a_readonly_member_is_refused_every_route(
    readonly_client: TestClient, api_session: Session
) -> None:
    _assert_refused_everywhere(readonly_client, api_session)


def _bearer(client: TestClient, **body: Any) -> TestClient:
    made = client.post("/api/tokens", json={"name": "agente", **body})
    assert made.status_code == 201, made.text
    agent = TestClient(client.app, base_url="https://testserver")
    agent.headers["Authorization"] = f"Bearer {made.json()['token']}"
    return agent


def test_a_token_without_full_access_is_refused_every_route_and_one_with_it_is_not(
    logged_in: TestClient, api_session: Session
) -> None:
    invalidate_space_settings(None)
    assert logged_in.put("/api/settings/space", json={"mcp_full_access": True}).status_code == 200
    account, statement = _seed(api_session)
    plain = _bearer(logged_in)
    for method, path in _every_route(account, statement):
        response = _call(plain, method, path)
        assert response.status_code == 403, (method, path, response.text)
        assert response.json()["code"] == "agent_forbidden", (method, path)
    full = _bearer(logged_in, full_access=True)
    assert full.get("/api/bank-accounts").status_code == 200
    assert _call(full, "POST", f"/api/bank-accounts/{account}/statements").status_code == 201


def test_a_space_with_the_cash_module_off_answers_404_and_keeps_the_data(
    logged_in: TestClient,
) -> None:
    account = _account(logged_in)
    statement = _upload(logged_in, account["id"], CAMT.read_bytes()).json()["statement"]
    off = logged_in.put("/api/settings/modules", json={"off": ["cash"], "confirm_off": ["cash"]})
    assert off.status_code == 200, off.text
    for method, path in _every_route(account["id"], statement["id"]):
        response = _call(logged_in, method, path)
        assert response.status_code == 404, (method, path, response.text)
        assert response.json()["code"] == "module_off"
    assert logged_in.put("/api/settings/modules", json={"on": ["cash"]}).status_code == 200
    assert logged_in.get("/api/bank-accounts").json()[0]["balance"] == "2170.00"


def test_the_upload_is_checked_before_the_body_is_read(
    readonly_client: TestClient, api_session: Session
) -> None:
    response = readonly_client.post(
        "/api/bank-accounts/00000000-0000-0000-0000-000000000000/statements",
        content=b"not even multipart",
        headers={"content-type": "text/plain"},
    )
    assert response.status_code == 403
