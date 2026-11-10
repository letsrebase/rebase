"""«Cassa» over HTTP (REB-1025, spec 2026-11-09, due items and the cash schedule, § 4): the
schedule, the payables mirror and the phone's money figures.

The service's rules are held in `packages/core/tests/test_cash_*.py`; this file holds what only
a request shows: the routes and their parameters, the money on the wire, the profile and the
module read from the space's own settings, the role of a person and of a token, and that the
schedule and the payables answer with the `cash` module off.
"""

import datetime as dt
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pigrocrm.core.bank.models import BankAccount
from pigrocrm.core.db import today_local
from pigrocrm.core.due_items.schemas import DueItemCreate
from pigrocrm.core.due_items.service import DueItemService
from pigrocrm.core.legal_entities.repository import LegalEntityRepository
from pigrocrm.core.organizations.models import Organization
from pigrocrm_api.deps import invalidate_space_settings

TODAY = today_local()
MONDAY = TODAY - dt.timedelta(days=TODAY.weekday())
NEXT_MONDAY = MONDAY + dt.timedelta(days=7)
_IBANS = iter(range(1, 1000))


@pytest.fixture(autouse=True)
def _settings_cache() -> None:
    invalidate_space_settings(None)


def _entity(session: Session) -> UUID:
    entity = LegalEntityRepository(session).default()
    assert entity is not None
    return entity.id


def _item(session: Session, amount: str, due: dt.date | None, **overrides: Any) -> UUID:
    """A due item written the way a source's service writes it: nothing over the wire lets a
    person type one."""
    entity = overrides.pop("entity", None) or _entity(session)
    customer = Organization(legal_name="ACME", legal_entity_id=entity)
    session.add(customer)
    session.flush()
    values: dict[str, Any] = {
        "legal_entity_id": entity,
        "direction": "in",
        "organization_id": customer.id,
        "amount": Decimal(amount),
        "source_kind": "sales_invoice",
        "source_id": uuid4(),
        "own_date": due,
    }
    if overrides.pop("forecast", False):
        values.update(state="forecast", source_rule="contract_recurring_fee", forecast_key=uuid4())
        values["source_kind"] = "contract_occurrence"
    if "tax" in overrides:
        values.update(counterparty_type="tax_office", organization_id=None)
    values.update(overrides)
    item = DueItemService(session).create(DueItemCreate(**values), check_organization=False)
    session.commit()
    return item.id


def _account(session: Session, balance: str, **overrides: Any) -> BankAccount:
    values: dict[str, Any] = {
        "legal_entity_id": _entity(session),
        "name": "Conto operativo",
        "bank": "Banco BPM",
        "iban": f"IT60X05428111{next(_IBANS):013d}",
        "opening_balance": Decimal(balance),
        "opening_balance_date": TODAY,
        "created_at": dt.datetime(2025, 1, 1, tzinfo=dt.UTC),
    }
    values.update(overrides)
    account = BankAccount(**values)
    session.add(account)
    session.commit()
    return account


def _agent(client: TestClient, *, full_access: bool) -> TestClient:
    """A bearer client for a token of `client`'s owner, on a space that opened the ceiling."""
    assert client.put("/api/settings/space", json={"mcp_full_access": True}).status_code == 200
    made = client.post("/api/tokens", json={"name": "agente", "full_access": full_access})
    assert made.status_code == 201, made.text
    agent = TestClient(client.app, base_url="https://testserver")
    agent.headers["Authorization"] = f"Bearer {made.json()['token']}"
    return agent


# --- the schedule -----------------------------------------------------------------------------


def test_the_schedule_is_money_as_strings_by_the_week_with_its_own_constants(
    logged_in: TestClient, api_session: Session
) -> None:
    _item(api_session, "100.50", NEXT_MONDAY)
    _item(api_session, "40.00", NEXT_MONDAY, forecast=True)
    _item(api_session, "60.00", NEXT_MONDAY, direction="out", source_kind="purchase")
    _account(api_session, "1000")

    response = logged_in.get("/api/cash/schedule")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["horizon_weeks"] == 13 and body["horizon_options"] == [4, 13, 26]
    assert body["today"] == TODAY.isoformat() and body["currency"] == "EUR"
    assert body["legal_entity_id"] is None and body["calculated_at"]
    assert (body["cash_enabled"], body["show_outgoing"], body["show_balance"]) == (True, True, True)
    assert body["profile"] is None and body["taxes"] is None
    assert len(body["weeks"]) == 13
    first, second = body["weeks"][0], body["weeks"][1]
    assert (first["start"], first["is_current"]) == (MONDAY.isoformat(), True)
    assert second["start"] == NEXT_MONDAY.isoformat()
    assert (second["in_expected"], second["in_forecast"]) == ("100.50", "40.00")
    assert (second["out_expected"], second["out_forecast"], second["net"]) == (
        "60.00",
        "0.00",
        "80.50",
    )
    assert (first["balance_end"], second["balance_end"]) == ("1000.00", "1080.50")
    assert (second["item_count"], second["has_forecast"], second["below_zero"]) == (3, True, False)
    assert body["total_balance"] == {
        "amount": "1000.00",
        "currency": "EUR",
        "as_of": TODAY.isoformat(),
        "excluded_currencies": [],
    }
    assert [(a["name"], a["balance"], a["currency"]) for a in body["accounts"]] == [
        ("Conto operativo", "1000.00", "EUR")
    ]
    assert body["late_receivable"] == {"count": 0, "total": "0.00", "oldest_days": None}
    assert body["alerts"] == [] and body["has_open_items"] is True


def test_the_horizon_is_one_of_the_options_the_answer_offers(
    logged_in: TestClient,
) -> None:
    for weeks in (4, 13, 26):
        body = logged_in.get("/api/cash/schedule", params={"weeks": weeks}).json()
        assert (body["horizon_weeks"], len(body["weeks"])) == (weeks, weeks)
    refused = logged_in.get("/api/cash/schedule", params={"weeks": 7})
    assert refused.status_code == 422, refused.text
    assert refused.json()["field"] == "weeks"
    assert logged_in.get("/api/cash/schedule", params={"weeks": "many"}).status_code == 422


def test_the_legal_entity_filter_is_echoed_and_scopes_the_figures(
    logged_in: TestClient, api_session: Session
) -> None:
    from pigrocrm.core.legal_entities.models import LegalEntity

    other = LegalEntity(name="Seconda", legal_name="Seconda srl", country="IT")
    api_session.add(other)
    api_session.flush()
    _item(api_session, "100.00", NEXT_MONDAY)
    _item(api_session, "7.00", NEXT_MONDAY, entity=other.id)

    both = logged_in.get("/api/cash/schedule").json()
    one = logged_in.get("/api/cash/schedule", params={"legal_entity_id": str(other.id)}).json()

    assert both["weeks"][1]["in_expected"] == "107.00"
    assert one["weeks"][1]["in_expected"] == "7.00" and one["legal_entity_id"] == str(other.id)


def test_a_request_with_no_credential_is_a_401(client: TestClient) -> None:
    for path in ("schedule", "payables", "summary"):
        assert client.get(f"/api/cash/{path}").status_code == 401


def test_with_the_module_off_the_schedule_and_the_payables_still_answer(
    logged_in: TestClient, api_session: Session
) -> None:
    _account(api_session, "10")
    _item(
        api_session, "60.00", TODAY - dt.timedelta(days=3), direction="out", source_kind="purchase"
    )
    assert (
        logged_in.put(
            "/api/settings/modules", json={"off": ["cash"], "confirm_off": ["cash"]}
        ).status_code
        == 200
    )

    schedule = logged_in.get("/api/cash/schedule")
    payables = logged_in.get("/api/cash/payables")
    summary = logged_in.get("/api/cash/summary")

    assert schedule.status_code == payables.status_code == summary.status_code == 200
    body = schedule.json()
    assert body["cash_enabled"] is False
    assert (body["accounts"], body["total_balance"], body["show_balance"]) == (None, None, False)
    assert body["alerts"] == [] and body["late_payable"]["total"] == "60.00"
    assert payables.json()["total_overdue"] == 1
    assert summary.json()["cash_enabled"] is False and summary.json()["balance_today"] is None


def test_a_solo_space_has_no_outgoing_column_and_a_table_of_its_own_taxes(
    logged_in: TestClient, api_session: Session
) -> None:
    _item(api_session, "100.00", NEXT_MONDAY)
    _item(api_session, "60.00", NEXT_MONDAY, direction="out", source_kind="purchase")
    tax = _item(
        api_session,
        "200.00",
        NEXT_MONDAY,
        direction="out",
        source_kind="iva_settlement",
        tax="vat",
    )
    _account(api_session, "1000")
    assert (
        logged_in.put(
            "/api/settings/modules/profile", json={"profile": "solo", "confirm": True}
        ).status_code
        == 200
    )

    body = logged_in.get("/api/cash/schedule").json()
    payables = logged_in.get("/api/cash/payables").json()
    summary = logged_in.get("/api/cash/summary").json()

    assert body["profile"] == "solo" and body["show_outgoing"] is False
    assert body["show_balance"] is False and body["late_payable"] is None
    assert body["weeks"][1]["in_expected"] == "100.00"
    assert all(
        week[key] is None
        for week in body["weeks"]
        for key in ("out_expected", "out_forecast", "net", "balance_end")
    )
    assert [(t["due_item_id"], t["state"], t["amount"], t["tax"]) for t in body["taxes"]] == [
        (str(tax), "expected", "200.00", "vat")
    ]
    assert body["total_balance"]["amount"] == "1000.00"
    assert payables["total"] == "0.00" and len(payables["bands"]) == 6
    assert (summary["show_outgoing"], summary["payable"]) == (False, None)
    assert summary["next_7_days"]["out_total"] is None


# --- who sees what ----------------------------------------------------------------------------


def test_a_collaborator_has_the_ordinary_kinds_and_no_balance(
    collaborator_client: TestClient, api_session: Session
) -> None:
    _account(api_session, "10")
    _item(api_session, "100.00", NEXT_MONDAY)
    _item(api_session, "60.00", NEXT_MONDAY, direction="out", source_kind="purchase")
    _item(api_session, "900.00", NEXT_MONDAY, direction="out", source_kind="payroll")
    _item(
        api_session,
        "40.00",
        TODAY - dt.timedelta(days=2),
        direction="out",
        source_kind="iva_settlement",
        tax="vat",
    )

    body = collaborator_client.get("/api/cash/schedule").json()
    payables = collaborator_client.get("/api/cash/payables").json()
    summary = collaborator_client.get("/api/cash/summary").json()

    assert body["cash_enabled"] is True
    assert (body["accounts"], body["total_balance"], body["show_balance"]) == (None, None, False)
    assert body["alerts"] == []
    assert all(week["balance_end"] is None for week in body["weeks"])
    assert body["weeks"][1]["out_expected"] == "60.00" and body["weeks"][1]["item_count"] == 2
    assert body["late_payable"] == {"count": 0, "total": "0.00", "oldest_days": None}
    assert payables["total"] == "60.00" and payables["total_overdue"] == 0
    assert (summary["balance_today"], summary["alert"]) == (None, None)
    assert summary["payable"]["total"] == "60.00"


def test_a_reader_reads_the_schedule_too(readonly_client: TestClient, api_session: Session) -> None:
    _item(api_session, "100.00", NEXT_MONDAY)
    body = readonly_client.get("/api/cash/schedule").json()
    assert body["weeks"][1]["in_expected"] == "100.00" and body["accounts"] is None


def test_a_token_reads_like_a_collaborator_and_a_full_access_one_like_the_administrator(
    logged_in: TestClient, api_session: Session
) -> None:
    _account(api_session, "10")
    _item(
        api_session,
        "40.00",
        NEXT_MONDAY,
        direction="out",
        source_kind="iva_settlement",
        tax="vat",
    )
    closed = _agent(logged_in, full_access=False)
    opened = _agent(logged_in, full_access=True)

    plain = closed.get("/api/cash/schedule")
    full = opened.get("/api/cash/schedule")

    assert plain.status_code == full.status_code == 200, (plain.text, full.text)
    assert plain.json()["accounts"] is None
    assert plain.json()["weeks"][1]["out_expected"] == "0.00"
    assert full.json()["accounts"] is not None
    assert full.json()["weeks"][1]["out_expected"] == "40.00"
    assert full.json()["total_balance"]["amount"] == "10.00"


# --- the payables mirror and the summary -----------------------------------------------------


def test_the_payables_have_the_receivables_shape_over_money_going_out(
    logged_in: TestClient, api_session: Session
) -> None:
    _item(
        api_session,
        "50.00",
        TODAY - dt.timedelta(days=10),
        direction="out",
        source_kind="purchase",
        description="Affitto",
    )
    _item(
        api_session, "70.00", TODAY + dt.timedelta(days=10), direction="out", source_kind="purchase"
    )
    _item(api_session, "9.00", None, direction="out", source_kind="purchase")
    _item(
        api_session,
        "500.00",
        TODAY + dt.timedelta(days=10),
        direction="out",
        source_kind="purchase",
        forecast=True,
    )

    body = logged_in.get("/api/cash/payables").json()

    assert set(body) >= {"calculated_at", "today", "legal_entity_id", "total", "bands"}
    assert body["total"] == "129.00" and body["total_overdue"] == 1
    assert [(b["code"], b["label"], b["amount"], b["number"]) for b in body["bands"]] == [
        ("expired", "Scaduto", "50.00", 1),
        ("entro_30", "Entro 30 giorni", "70.00", 1),
        ("da_31_a_60", "Da 31 a 60 giorni", "0.00", 0),
        ("da_61_a_90", "Da 61 a 90 giorni", "0.00", 0),
        ("oltre_90", "Oltre 90 giorni", "0.00", 0),
        ("without_due_date", "Senza scadenza", "9.00", 1),
    ]
    assert [(b["code"], b["date_from"], b["date_to"]) for b in body["bands"]][1:5] == [
        ("entro_30", TODAY.isoformat(), (TODAY + dt.timedelta(days=30)).isoformat()),
        (
            "da_31_a_60",
            (TODAY + dt.timedelta(days=31)).isoformat(),
            (TODAY + dt.timedelta(days=60)).isoformat(),
        ),
        (
            "da_61_a_90",
            (TODAY + dt.timedelta(days=61)).isoformat(),
            (TODAY + dt.timedelta(days=90)).isoformat(),
        ),
        ("oltre_90", (TODAY + dt.timedelta(days=91)).isoformat(), None),
    ]
    assert body["overdue"] == [
        {
            "due_item_id": body["overdue"][0]["due_item_id"],
            "counterparty": "ACME",
            "description": "Affitto",
            "due_date": (TODAY - dt.timedelta(days=10)).isoformat(),
            "days_of_delay": 10,
            "amount": "50.00",
        }
    ]


def test_the_summary_is_the_phones_figures(logged_in: TestClient, api_session: Session) -> None:
    _account(api_session, "300")
    _item(api_session, "100.00", TODAY - dt.timedelta(days=3))
    _item(api_session, "25.00", TODAY + dt.timedelta(days=2))
    _item(
        api_session, "10.00", TODAY + dt.timedelta(days=3), direction="out", source_kind="purchase"
    )

    body = logged_in.get("/api/cash/summary").json()

    assert body["today"] == TODAY.isoformat() and body["cash_enabled"] is True
    assert (body["balance_today"], body["balance_as_of"]) == ("300.00", TODAY.isoformat())
    assert body["receivable"] == {"total": "125.00", "late_total": "100.00", "late_count": 1}
    assert body["payable"] == {"total": "10.00", "late_total": "0.00", "late_count": 0}
    assert body["next_7_days"] == {"in_total": "25.00", "out_total": "10.00"}
    assert body["alert"] is None
    assert body["show_outgoing"] is True


def test_the_three_routes_are_in_the_openapi_document(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    for path in ("schedule", "payables", "summary"):
        assert "get" in paths[f"/api/cash/{path}"]
