"""The HTTP surface of `AnalyticsService`, thin by design: resolve the Actor, call the
service, serialise. Every assertion here is about the wire -- statuses, problem
documents, query-parameter spelling and Decimal-as-string -- never about the arithmetic,
which is proven in `packages/core/tests/test_deal_pnl.py`, `test_period_pnl.py`,
`test_budget_vs_actual.py` and `test_fiscal_estimate.py`.

Two things this module does assert that only exist at this layer: that `from`/`to` reach
the wire under the names spec §11 writes (the router's parameters cannot be called that,
`from` being a Python keyword), and that `to-invoice-draft` -- the one writer in the
whole surface -- is admin-only and refuses hours already on an *issued* invoice while
still rewriting a draft.
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from legal_entities_helpers import legal_entity_url
from sqlalchemy.orm import Session

from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.db.clock import today_local
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.work_units.models import WorkUnit

# Never a literal year: `check_issue_date` refuses a `issue_date` outside the
# current year, so a hard-coded 2026 would be a suite that starts failing on the first
# of January. The window below is the whole of the current year, which always contains
# both today's hours and today's issued invoices.
TODAY = today_local()
YEAR = TODAY.year
DA = TODAY.replace(month=1, day=1).isoformat()
A = TODAY.replace(month=12, day=31).isoformat()
PERIOD = {"from": DA, "to": A}

PASSWORD = "supersegreta1"


def _second_actor(admin_client: TestClient, role: str) -> TestClient:
    """A genuinely independent session, not `admin_client`'s own cookie jar. Copied from
    `test_invoices_api.py`'s helper of the same name and purpose -- see its docstring for
    why `logged_in` and `collaborator_client` cannot both be requested in one test."""
    email = f"{role}-analytics-{id(admin_client)}@pigro.it"
    created = admin_client.post(
        "/api/users",
        json={"email": email, "password": PASSWORD, "name": "Test", "role": role},
    )
    assert created.status_code == 201, created.text
    client = TestClient(admin_client.app, base_url="https://testserver")
    response = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return client


@pytest.fixture
def legal_entity(logged_in: TestClient) -> dict[str, Any]:
    """Required by `issue`, not by anything analytics does -- only the two tests that
    take an invoice all the way to `issued` need it."""
    response = logged_in.put(
        legal_entity_url(logged_in),
        json={
            "legal_name": "Studio Rossi",
            "vat_number": "01234567890",
            "tax_code": "HMCRFT00A01H501K",
            "address": "Via Vittorio Veneto 12",
            "postal_code": "20124",
            "city": "Milano",
            "province": "MI",
            "country": "IT",
            "email": "mario@example.com",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def fiscal_profile(logged_in: TestClient) -> dict[str, Any]:
    """The forfettario rates slice 1 §2.2 found hard-coded in `App.jsx`, now configured
    per installation. `get_fiscal_estimate` reads all three from here."""
    response = logged_in.put(
        legal_entity_url(logged_in, "/fiscal-profile"),
        json={
            "regime_code": "RF19",
            "profitability_coefficient": "67.00",
            "substitute_tax_rate": "5.00",
            "social_security_rate": "26.07",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _seed_deal_and_user(
    logged_in: TestClient,
    *,
    budgeted_hours: str | None = None,
    budgeted_value: str | None = None,
) -> tuple[str, str]:
    """A customer complete enough to be invoiced (the `to-invoice-draft` tests take one
    all the way to `issued`), one deal, one collaborator to log hours against."""
    assert logged_in.post("/api/pipeline-stages/seed").status_code == 200
    customer = logged_in.post(
        "/api/organizations",
        json={
            "legal_name": "Acme S.r.l.",
            "vat_number": "12345678901",
            "sdi_code": "ABCDEFG",
            "address": "Corso Italia 5",
            "postal_code": "00100",
            "city": "Roma",
            "province": "RM",
            "country": "IT",
        },
    )
    assert customer.status_code == 201, customer.text
    body: dict[str, Any] = {"name": "Progetto", "customer_id": customer.json()["id"]}
    if budgeted_hours is not None:
        body["budgeted_hours"] = budgeted_hours
    if budgeted_value is not None:
        body["budgeted_value"] = budgeted_value
    deal = logged_in.post("/api/deals", json=body)
    assert deal.status_code == 201, deal.text
    user = logged_in.post(
        "/api/users",
        json={
            "email": f"worker-analytics-{id(logged_in)}@pigro.it",
            "password": PASSWORD,
            "name": "Worker",
            "role": "collaborator",
        },
    )
    assert user.status_code == 201, user.text
    return deal.json()["id"], user.json()["id"]


def _log_hours(
    logged_in: TestClient,
    deal_id: str,
    user_id: str,
    *,
    hours: str = "4.00",
    rate: str = "100.000000",
    cost: str = "25.000000",
) -> str:
    response = logged_in.post(
        "/api/time-entries",
        json={
            "deal_id": deal_id,
            "user_id": user_id,
            "date": TODAY.isoformat(),
            "hours": hours,
            "description": "Sviluppo",
            "applied_rate": rate,
            "applied_cost": cost,
        },
    )
    assert response.status_code == 201, response.text
    entry_id: str = response.json()["id"]
    return entry_id


# --- the deal's own economics -------------------------------------------------------


def test_deal_pnl_serialises_decimals_as_strings_and_null_percentage(
    logged_in: TestClient,
) -> None:
    """Four hours at 100 EUR sold and 25 EUR of cost, and not one invoice: revenue is the
    invoice (§3, decision 2), so `revenue` is a real zero while `accrued_value` is 400 --
    two fields precisely because they are two different facts."""
    deal_id, user_id = _seed_deal_and_user(logged_in)
    _log_hours(logged_in, deal_id, user_id)

    response = logged_in.get(f"/api/deals/{deal_id}/pnl")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["revenue"] == "0.00"
    assert body["issued_invoices"] == 0
    # `null`, never `"0.00"`: zero per cent means "everything earned went out in costs",
    # a zero denominator means nothing has been earned. The wire keeps them apart.
    assert body["percentage_margin"] is None
    assert body["status"] == "in_progress"
    assert body["work_cost"] == "100.00"
    assert body["gross_margin"] == "-100.00"
    assert body["accrued_value"] == "400.00"
    assert body["accrued_value"] != body["revenue"]
    # Asserted on the raw text, because `response.json()` would already have parsed it:
    # a `Decimal` reaches the browser as a string, never as a binary float.
    assert '"work_cost":"100.00"' in response.text.replace(" ", "")


def test_a_readonly_actor_may_read_the_economics(logged_in: TestClient) -> None:
    """No `require_write` and no `require_admin` on any read in this surface, deliberately:
    a deal's P&L is not more sensitive than the deal, the hours and the invoices it is
    derived from, each of which a reader can already list."""
    deal_id, user_id = _seed_deal_and_user(logged_in)
    _log_hours(logged_in, deal_id, user_id)
    readonly = _second_actor(logged_in, "readonly")

    assert readonly.get(f"/api/deals/{deal_id}/pnl").status_code == 200
    assert readonly.get("/api/analytics/pnl", params=PERIOD).status_code == 200


def test_the_deal_budget_row_comes_from_the_same_method_as_the_list(
    logged_in: TestClient,
) -> None:
    """Served from `budget_vs_actual` rather than reimplemented per deal, so the detail
    page and the report can never show different variances."""
    deal_id, user_id = _seed_deal_and_user(
        logged_in, budgeted_hours="10.00", budgeted_value="1000.00"
    )
    _log_hours(logged_in, deal_id, user_id)

    response = logged_in.get(f"/api/deals/{deal_id}/budget", params=PERIOD)
    assert response.status_code == 200, response.text
    row = response.json()
    assert row["deal_id"] == deal_id
    assert row["actuals_hours"] == "4.00"
    assert row["hours_progress"] == "40.00"
    # The pro-rata budget, not the full one: at 40% of the hours, 400 is the figure the
    # revenue is judged against.
    assert row["budget_pro_rata"] == "400.00"
    assert row["value_variance"] == "-400.00"
    assert row["unbudgeted"] is False


def test_a_deal_with_no_activity_in_the_window_has_no_budget_row(
    logged_in: TestClient,
) -> None:
    """A 404 rather than a row of zeroes: `deals_in_range` returns deals with activity in
    the window, and a deal that had none did not "spend nothing" -- it was not in the
    report at all."""
    deal_id, _ = _seed_deal_and_user(logged_in)
    response = logged_in.get(f"/api/deals/{deal_id}/budget", params=PERIOD)
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


# --- the period reports -------------------------------------------------------------


def test_the_period_report_uses_the_spec_query_names(logged_in: TestClient) -> None:
    """`from` and `to` on the wire, as §11 writes them; `da`/`a` in the code, because
    `from` is a Python keyword. The alias is what keeps both true, and the second half of
    this test is what proves the alias is really there: without it the router would
    answer to `da`/`a` and the generated client would call a URL the spec does not
    describe."""
    deal_id, user_id = _seed_deal_and_user(logged_in)
    _log_hours(logged_in, deal_id, user_id)

    response = logged_in.get("/api/analytics/pnl", params=PERIOD)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) >= {
        "closed",
        "in_progress",
        "general_expenses",
        "closed_period",
        "written_entries_overdue",
    }
    # No combined total, deliberately: adding a finished job's margin to a half-done one
    # produces a figure that is neither (§7.4).
    assert "total" not in body
    assert body["in_progress"]["deal"] == 1
    assert body["in_progress"]["work_cost"] == "100.00"
    assert body["closed"]["deal"] == 0
    assert body["date_from"] == DA and body["date_to"] == A

    assert logged_in.get("/api/analytics/pnl", params={"da": DA, "a": A}).status_code == 422


def test_the_backlog_takes_no_period_and_serialises_its_decimals_as_strings(
    logged_in: TestClient,
) -> None:
    """§6.3's endpoint. `from`/`to` are absent on purpose -- "quanto ho da fatturare" is
    not a question about March -- so the route must answer with no query parameter at all,
    which is exactly the shape the mandatory-period test below proves `/pnl` refuses."""
    deal_id, user_id = _seed_deal_and_user(logged_in)
    _log_hours(logged_in, deal_id, user_id, hours="4.00", rate="100.000000")

    response = logged_in.get("/api/analytics/backlog")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {
        "billable_uninvoiced_hours": "4.00",
        "accrued_value": "400.00",
        "entries_without_rate": 0,
        "entries": 1,
    }


def test_the_backlog_is_readable_by_a_readonly_actor(logged_in: TestClient) -> None:
    """Every analytics read is; the one admin-only figure is the fiscal estimate."""
    deal_id, user_id = _seed_deal_and_user(logged_in)
    _log_hours(logged_in, deal_id, user_id)
    readonly = _second_actor(logged_in, "readonly")
    assert readonly.get("/api/analytics/backlog").status_code == 200


def test_the_period_filter_is_mandatory(logged_in: TestClient) -> None:
    """Residual B3: paginated with a mandatory period filter from the first commit,
    because a margins view is by nature a list of deals and unbounded growth stops being
    invisible here."""
    assert logged_in.get("/api/analytics/pnl").status_code == 422
    assert logged_in.get("/api/analytics/budget").status_code == 422


def test_an_inverted_window_is_a_422_naming_the_field(logged_in: TestClient) -> None:
    """The service's own guard, rendered as a problem document rather than a 500."""
    response = logged_in.get("/api/analytics/pnl", params={"from": A, "to": DA})
    assert response.status_code == 422
    assert response.json()["field"] == "to"


def test_the_budget_list_is_bounded(logged_in: TestClient) -> None:
    over = logged_in.get("/api/analytics/budget", params={**PERIOD, "limit": 500})
    assert over.status_code == 422
    at_the_ceiling = logged_in.get("/api/analytics/budget", params={**PERIOD, "limit": 200})
    assert at_the_ceiling.status_code == 200, at_the_ceiling.text


def test_an_unestimated_deal_is_excluded_from_the_budget_aggregates(
    logged_in: TestClient,
) -> None:
    """An absent estimate is not an estimate of zero: the row says so and the totals
    leave it out, or the aggregate would depend on how many deals nobody estimated."""
    deal_id, user_id = _seed_deal_and_user(logged_in)
    _log_hours(logged_in, deal_id, user_id)

    response = logged_in.get("/api/analytics/budget", params=PERIOD)
    assert response.status_code == 200, response.text
    page = response.json()
    assert [row["deal_id"] for row in page["items"]] == [deal_id]
    assert page["items"][0]["unbudgeted"] is True
    assert page["unbudgeted_deals"] == 1
    assert page["budgeted_deals"] == 0
    assert page["budgeted_total"] == "0.00"
    assert page["next_cursor"] is None


# --- ceiling headroom and the "would this fit?" simulator (REB-373) -----------------


def test_the_ceiling_headroom_route_serialises_decimals_as_strings(
    logged_in: TestClient, fiscal_profile: dict[str, Any]
) -> None:
    response = logged_in.get("/api/analytics/ceilings", params={"year": YEAR})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["year"] == YEAR
    assert body["pack_id"] == "it-flat-rate"
    revenue_threshold = next(s for s in body["thresholds"] if s["id"] == "revenue_ceiling")
    assert revenue_threshold["threshold"] == "85000.00"
    assert isinstance(revenue_threshold["revenue"], str)
    assert isinstance(revenue_threshold["remaining"], str)


def test_the_ceiling_headroom_route_is_readable_by_a_readonly_actor(
    logged_in: TestClient, fiscal_profile: dict[str, Any]
) -> None:
    readonly = _second_actor(logged_in, "readonly")
    assert readonly.get("/api/analytics/ceilings", params={"year": YEAR}).status_code == 200


def test_the_ceiling_headroom_route_is_a_404_without_a_fiscal_profile(
    logged_in: TestClient,
) -> None:
    response = logged_in.get("/api/analytics/ceilings", params={"year": YEAR})
    assert response.status_code == 404, response.text
    assert response.json()["code"] == "not_found"


def test_the_ceiling_headroom_route_requires_an_year(logged_in: TestClient) -> None:
    assert logged_in.get("/api/analytics/ceilings").status_code == 422


def test_the_simulator_adds_the_typed_value_and_answers_would_this_fit(
    logged_in: TestClient, fiscal_profile: dict[str, Any]
) -> None:
    response = logged_in.get(
        "/api/analytics/ceilings/simulate",
        params={"year": YEAR, "budgeted_value": "20000.00"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["brief_addition"] == "20000.00"
    revenue_threshold = next(s for s in body["thresholds"] if s["id"] == "revenue_ceiling")
    assert revenue_threshold["simulated_revenue"] == "20000.00"
    assert revenue_threshold["falls_within"] is True


def test_the_simulator_derives_the_addition_from_hours_and_rate(
    logged_in: TestClient, fiscal_profile: dict[str, Any]
) -> None:
    response = logged_in.get(
        "/api/analytics/ceilings/simulate",
        params={"year": YEAR, "budgeted_hours": "100.00", "hourly_rate": "250.000000"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["brief_addition"] == "25000.00"


def test_the_simulator_without_any_estimate_is_a_422_naming_the_field(
    logged_in: TestClient, fiscal_profile: dict[str, Any]
) -> None:
    response = logged_in.get("/api/analytics/ceilings/simulate", params={"year": YEAR})
    assert response.status_code == 422, response.text
    assert response.json()["field"] == "budgeted_value"


def test_the_simulator_is_readable_by_a_readonly_actor(
    logged_in: TestClient, fiscal_profile: dict[str, Any]
) -> None:
    readonly = _second_actor(logged_in, "readonly")
    response = readonly.get(
        "/api/analytics/ceilings/simulate",
        params={"year": YEAR, "budgeted_value": "1000.00"},
    )
    assert response.status_code == 200, response.text


def _deal_with_a_monthly_fee(logged_in: TestClient) -> str:
    """A saved deal with one open-ended fee of 1.000 a month, from 1 October of the year."""
    assert logged_in.post("/api/pipeline-stages/seed").status_code == 200
    customer = logged_in.post("/api/organizations", json={"legal_name": "Acme S.r.l."})
    assert customer.status_code == 201, customer.text
    deal = logged_in.post(
        "/api/deals",
        json={
            "name": "Manutenzione",
            "customer_id": customer.json()["id"],
            "value_lines": [
                {
                    "unit": "flat",
                    "delivery_period": "monthly",
                    "unit_price": "1000.00",
                    "start": f"{YEAR}-10-01",
                }
            ],
        },
    )
    assert deal.status_code == 201, deal.text
    return str(deal.json()["id"])


def test_the_simulator_reads_a_saved_deal_by_its_value_lines(
    logged_in: TestClient, fiscal_profile: dict[str, Any]
) -> None:
    """October, November and December: the fee's payments that fall in the year, not the
    12.000 the deal's total says."""
    deal_id = _deal_with_a_monthly_fee(logged_in)
    response = logged_in.get(
        "/api/analytics/ceilings/simulate", params={"year": YEAR, "deal_id": deal_id}
    )
    assert response.status_code == 200, response.text
    assert response.json()["brief_addition"] == "3000.00"


def test_the_simulator_refuses_a_deal_id_together_with_a_raw_estimate(
    logged_in: TestClient, fiscal_profile: dict[str, Any]
) -> None:
    deal_id = _deal_with_a_monthly_fee(logged_in)
    response = logged_in.get(
        "/api/analytics/ceilings/simulate",
        params={"year": YEAR, "deal_id": deal_id, "budgeted_value": "100.00"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["field"] == "deal_id"


# --- the fiscal estimate ------------------------------------------------------------


def test_the_fiscal_report_is_admin_only(logged_in: TestClient) -> None:
    collaborator = _second_actor(logged_in, "collaborator")
    response = collaborator.get("/api/analytics/fiscal", params={"year": YEAR})
    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"


def test_the_fiscal_report_is_labelled_an_estimate_in_its_own_payload(
    logged_in: TestClient,
    legal_entity: dict[str, Any],
    fiscal_profile: dict[str, Any],
) -> None:
    """400 EUR invoiced, at 67% / 5% / 26.07%: taxable base 268.00, tax 13.40,
    contributions 66.37, net 320.23 -- the chain checked end to end over HTTP, because a
    report that silently lost a rate would still answer 200 with plausible numbers."""
    deal_id, user_id = _seed_deal_and_user(logged_in)
    entry_id = _log_hours(logged_in, deal_id, user_id)
    draft = logged_in.post(
        f"/api/deals/{deal_id}/time-entries/to-invoice-draft", json={"entry_ids": [entry_id]}
    )
    assert draft.status_code == 200, draft.text
    issued = logged_in.post(f"/api/invoices/{draft.json()['id']}/issue", json={})
    assert issued.status_code == 200, issued.text
    assert issued.json()["taxable_amount"] == "400.00"

    response = logged_in.get("/api/analytics/fiscal", params={"year": YEAR})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["year"] == YEAR
    assert body["estimate"] is True
    assert "Stima indicativa" in body["warning"]
    assert body["revenue"] == "400.00"
    assert body["taxable_amount"] == "268.00"
    # The contributions are owed on the taxable amount and deducted from it, and the substitute
    # tax is owed on what is left (L. 190/2014 art. 1 c. 64): 268.00 * 0.2607 = 69.8676, then
    # (268.00 - 69.87) * 0.05 = 9.9065.
    assert body["contributions"] == "69.87"
    assert body["substitute_tax_base"] == "198.13"
    assert body["substitute_tax"] == "9.91"
    assert body["estimated_net_income"] == "320.22"
    assert body["coefficient_warning"] is None


# --- the one writer -----------------------------------------------------------------


def test_to_invoice_draft_is_admin_only(logged_in: TestClient) -> None:
    deal_id, _ = _seed_deal_and_user(logged_in)
    collaborator = _second_actor(logged_in, "collaborator")
    response = collaborator.post(
        f"/api/deals/{deal_id}/time-entries/to-invoice-draft",
        json={"entry_ids": ["11111111-1111-7111-8111-111111111111"]},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"


def test_the_draft_binds_the_hours_without_becoming_revenue(
    logged_in: TestClient, fiscal_profile: dict[str, Any]
) -> None:
    """The settled reading of "already invoiced" across all analytics is the invoice-state
    one: hours sitting on a *draft* are still billable-and-unbilled and the deal's revenue
    is still zero, because a draft is not revenue. The link-based reading would leave the
    work in neither figure -- priced, done and invisible until somebody pressed "issue"."""
    deal_id, user_id = _seed_deal_and_user(logged_in)
    entry_id = _log_hours(logged_in, deal_id, user_id)

    response = logged_in.post(
        f"/api/deals/{deal_id}/time-entries/to-invoice-draft", json={"entry_ids": [entry_id]}
    )
    assert response.status_code == 200, response.text
    invoice = response.json()
    assert invoice["status"] == "draft"
    assert invoice["deal_id"] == deal_id
    assert invoice["taxable_amount"] == "400.00"

    pnl = logged_in.get(f"/api/deals/{deal_id}/pnl").json()
    assert pnl["revenue"] == "0.00"
    assert pnl["billable_uninvoiced_hours"] == "4.00"


def test_a_draft_can_be_rewritten_but_an_issued_invoice_cannot(
    logged_in: TestClient,
    legal_entity: dict[str, Any],
    fiscal_profile: dict[str, Any],
) -> None:
    """The whole point of the invoice-state reading. Binding the same hours twice while
    the first draft is still a draft is allowed -- that draft may be a mistake somebody is
    redoing -- and becomes a 409 naming the document the moment one of them is issued."""
    deal_id, user_id = _seed_deal_and_user(logged_in)
    entry_id = _log_hours(logged_in, deal_id, user_id)
    url = f"/api/deals/{deal_id}/time-entries/to-invoice-draft"

    first = logged_in.post(url, json={"entry_ids": [entry_id]})
    assert first.status_code == 200, first.text
    second = logged_in.post(url, json={"entry_ids": [entry_id]})
    assert second.status_code == 200, second.text
    assert second.json()["id"] != first.json()["id"]

    issued = logged_in.post(f"/api/invoices/{second.json()['id']}/issue", json={})
    assert issued.status_code == 200, issued.text

    refused = logged_in.post(url, json={"entry_ids": [entry_id]})
    assert refused.status_code == 409
    problem = refused.json()
    assert problem["code"] == "conflict"
    assert problem["number"] == issued.json()["number"]
    assert problem["year"] == YEAR
    assert "due volte" in problem["detail"]


def test_an_entry_from_another_deal_is_a_422_naming_the_field(logged_in: TestClient) -> None:
    deal_id, user_id = _seed_deal_and_user(logged_in)
    other = logged_in.post(
        "/api/deals",
        json={
            "name": "Altro progetto",
            "customer_id": logged_in.get(f"/api/deals/{deal_id}").json()["customer_id"],
        },
    )
    assert other.status_code == 201, other.text
    entry_id = _log_hours(logged_in, other.json()["id"], user_id)

    response = logged_in.post(
        f"/api/deals/{deal_id}/time-entries/to-invoice-draft", json={"entry_ids": [entry_id]}
    )
    assert response.status_code == 422
    assert response.json()["field"] == "entry_ids"


def test_an_empty_selection_never_reaches_the_service(logged_in: TestClient) -> None:
    """`entry_ids` is `min_length=1`: "everything billable" is a commercial decision, and
    a request that selects nothing is a request that meant something else."""
    deal_id, _ = _seed_deal_and_user(logged_in)
    response = logged_in.post(
        f"/api/deals/{deal_id}/time-entries/to-invoice-draft", json={"entry_ids": []}
    )
    assert response.status_code == 422


# --- the generated client -----------------------------------------------------------


def test_the_openapi_document_describes_every_analytics_route(logged_in: TestClient) -> None:
    """The frontend client is generated from this document, so a route missing here is a
    route the UI cannot call with types."""
    paths: dict[str, Any] = logged_in.get("/openapi.json").json()["paths"]
    for path in (
        "/api/deals/{deal_id}/pnl",
        "/api/deals/{deal_id}/budget",
        "/api/deals/{deal_id}/time-entries/to-invoice-draft",
        "/api/analytics/pnl",
        "/api/analytics/budget",
        "/api/analytics/backlog",
        "/api/analytics/fiscal",
        "/api/analytics/ceilings",
        "/api/analytics/ceilings/simulate",
    ):
        assert path in paths, path
    names = {param["name"] for param in paths["/api/analytics/pnl"]["get"]["parameters"]}
    assert {"from", "to"} <= names
    assert "da" not in names


def test_the_economic_overview_answers_everyone_and_keeps_the_estimate_for_admins(
    logged_in: TestClient, collaborator_client: TestClient
) -> None:
    year = YEAR
    admin = logged_in.get("/api/analytics/overview", params={"year": year})
    assert admin.status_code == 200, admin.text
    body = admin.json()
    assert body["pension_fund"]["year"] == year
    assert len(body["pension_fund"]["months"]) == 12
    for key in ("paid", "unpaid", "drafts", "projected", "costs", "actual_gross"):
        assert key in body["pension_fund"]
    # Without a fiscal profile even an admin gets cash alone, never a 404.
    assert body["tax"] is None

    other = collaborator_client.get("/api/analytics/overview", params={"year": year})
    assert other.status_code == 200, other.text
    assert other.json()["tax"] is None


# --- the cash view reads by accrual period, or by the money (ORB-133) ------------------


def test_the_economic_overview_takes_a_cash_base_and_defaults_to_accrual(
    logged_in: TestClient,
) -> None:
    """`base=accrual|collection` beside `year`, competenza when omitted (Ivan's default),
    echoed under `pension_fund.base` so the page can label what it draws, and a 422 for any
    other word -- `issue` included, which is the P&L's base and not this view's. The
    arithmetic of the two readings is `packages/core/tests/test_analytics_cash.py`."""
    default = logged_in.get("/api/analytics/overview", params={"year": YEAR})
    assert default.status_code == 200, default.text
    assert default.json()["pension_fund"]["base"] == "accrual"

    collection = logged_in.get(
        "/api/analytics/overview", params={"year": YEAR, "base": "collection"}
    )
    assert collection.status_code == 200, collection.text
    assert collection.json()["pension_fund"]["base"] == "collection"

    for wrong in ("issue", "pension_fund"):
        refused = logged_in.get("/api/analytics/overview", params={"year": YEAR, "base": wrong})
        assert refused.status_code == 422, refused.text

    paths: dict[str, Any] = logged_in.get("/openapi.json").json()["paths"]
    params = {p["name"]: p for p in paths["/api/analytics/overview"]["get"]["parameters"]}
    assert params["base"]["required"] is False
    assert set(params["base"]["schema"]["enum"]) == {"accrual", "collection"}
    assert params["base"]["schema"]["default"] == "accrual"
    assert "accrual" in params["base"]["description"]


# --- the second reading of revenue, by accrual period (ORB-61) -----------------------


def _revenue(body: dict[str, Any]) -> str:
    """The one deal's revenue, whichever column `deal_summary` put it in."""
    column = body["closed"] if body["closed"]["deal"] else body["in_progress"]
    revenue: str = column["revenue"]
    return revenue


def test_the_period_report_takes_a_base_and_defaults_to_emission(
    logged_in: TestClient, legal_entity: dict[str, Any]
) -> None:
    """`base=accrual` is a query parameter beside `from`/`to`; anything else is a 422
    and an omitted one is the recorded default. The arithmetic of the two readings is
    proven in `packages/core/tests/test_period_pnl.py`; this asserts the wire, with one
    invoice issued today for last month's work so the two readings visibly differ."""
    assert (
        logged_in.put(
            legal_entity_url(logged_in, "/fiscal-profile"), json={"regime_code": "RF19"}
        ).status_code
        == 200
    )
    deal_id, _ = _seed_deal_and_user(logged_in)
    customer_id = logged_in.get(f"/api/deals/{deal_id}").json()["customer_id"]
    first = TODAY.replace(day=1)
    prev_month_end = first - timedelta(days=1)
    draft = logged_in.post(
        "/api/invoices",
        json={
            "customer_id": customer_id,
            "deal_id": deal_id,
            "accrual_from": prev_month_end.replace(day=1).isoformat(),
            "accrual_to": prev_month_end.isoformat(),
            "lines": [{"description": "Consulenza", "unit_price": "1000.00"}],
        },
    )
    assert draft.status_code == 201, draft.text
    issued = logged_in.post(f"/api/invoices/{draft.json()['id']}/issue", json={})
    assert issued.status_code == 200, issued.text

    this_month = {"from": first.isoformat(), "to": TODAY.isoformat()}
    by_emission = logged_in.get("/api/analytics/pnl", params=this_month)
    assert by_emission.status_code == 200, by_emission.text
    by_accrual = logged_in.get("/api/analytics/pnl", params={**this_month, "base": "accrual"})
    assert by_accrual.status_code == 200, by_accrual.text
    assert _revenue(by_emission.json()) == "1000.00"
    assert _revenue(by_accrual.json()) == "0.00"
    # The body echoes the reading, so the web client can label the figure it shows
    # without carrying the request around beside it.
    assert by_emission.json()["base"] == "issue"
    assert by_accrual.json()["base"] == "accrual"
    last_month = {
        "from": prev_month_end.replace(day=1).isoformat(),
        "to": prev_month_end.isoformat(),
        "base": "accrual",
    }
    assert _revenue(logged_in.get("/api/analytics/pnl", params=last_month).json()) == "1000.00"

    unknown = logged_in.get("/api/analytics/pnl", params={**this_month, "base": "pension_fund"})
    assert unknown.status_code == 422, unknown.text


def test_the_openapi_document_describes_the_base_parameter_in_italian(
    logged_in: TestClient,
) -> None:
    paths: dict[str, Any] = logged_in.get("/openapi.json").json()["paths"]
    params = {p["name"]: p for p in paths["/api/analytics/pnl"]["get"]["parameters"]}
    assert "base" in params
    assert params["base"]["required"] is False
    assert set(params["base"]["schema"]["enum"]) == {"issue", "accrual"}
    assert params["base"]["schema"]["default"] == "issue"
    assert "accrual" in params["base"]["description"]


# --- one legal entity at a time for the taxes, every legal entity for the cash (REB-631) ---------

LTD = {
    "name": "rebase ltd",
    "legal_name": "Rebase Ltd",
    "country": "GB",
    "fiscal_profile": {"pack_id": "non-it", "default_vat_rate": "20.00"},
}


def _paid_invoice(session: Session, legal_entity_id: str, number: int, amount: str) -> None:
    """One paid invoice of `amount` on its own customer, written by row on the test's
    session: enough to move every sum the routes below read, without the whole issue
    flow, which is not what these tests are about."""
    customer = Organization(legal_name=f"Cliente {legal_entity_id[:8]} {number}", country="IT")
    customer.legal_entity_id = legal_entity_id  # type: ignore[assignment]
    session.add(customer)
    session.flush()
    session.add(
        Invoice(
            customer_id=customer.id,
            legal_entity_id=legal_entity_id,
            type="invoice",
            status="issued",
            year=YEAR,
            number=number,
            payment_status="paid",
            issue_date=date(YEAR, 2, 1),
            payment_date=date(YEAR, 2, 10),
            taxable_amount=Decimal(amount),
            vat_amount=Decimal("0.00"),
            stamp_duty=Decimal("0.00"),
            total=Decimal(amount),
            custom_fields={},
        )
    )
    session.flush()


def test_the_fiscal_routes_resolve_alone_on_one_legal_entity_and_need_it_on_two(
    logged_in: TestClient, fiscal_profile: dict[str, Any]
) -> None:
    """Spec §1.9, §8 «Dashboard»: with one azienda the three routes answer as they always
    did; from the second on they refuse without `legal_entity_id`, naming it, and answer for
    the one named. The refusal is this project's own 422, the shape `fieldErrorFrom`
    reads, where the spec's prose wrote 400 (REB-630's note in §11)."""
    default = logged_in.get("/api/legal-entities").json()[0]["id"]
    alone = logged_in.get("/api/analytics/fiscal", params={"year": YEAR})
    assert alone.status_code == 200, alone.text
    assert alone.json()["legal_entity_id"] == default

    created = logged_in.post("/api/legal-entities", json=LTD)
    assert created.status_code == 201, created.text
    second = created.json()["id"]
    simulate = {"year": YEAR, "budgeted_value": "100.00"}
    for path, params in (
        ("/api/analytics/fiscal", {"year": YEAR}),
        ("/api/analytics/ceilings", {"year": YEAR}),
        ("/api/analytics/ceilings/simulate", simulate),
    ):
        refused = logged_in.get(path, params=params)
        assert refused.status_code == 422, (path, refused.text)
        assert refused.json()["field"] == "legal_entity_id", path
        named = logged_in.get(path, params={**params, "legal_entity_id": default})
        assert named.status_code == 200, (path, named.text)
        assert named.json()["legal_entity_id"] == default
    # The foreign legal entity has a profile but no ceilings, and no flat-rate regime estimate.
    foreign = logged_in.get(
        "/api/analytics/ceilings", params={"year": YEAR, "legal_entity_id": second}
    )
    assert foreign.status_code == 200, foreign.text
    assert (foreign.json()["pack_id"], foreign.json()["thresholds"]) == ("non-it", [])


def test_the_overview_in_all_sums_the_legal_entities_and_keeps_no_estimate(
    logged_in: TestClient, api_session: Session, fiscal_profile: dict[str, Any]
) -> None:
    default = logged_in.get("/api/legal-entities").json()[0]["id"]
    second = logged_in.post("/api/legal-entities", json=LTD).json()["id"]
    _paid_invoice(api_session, default, 1, "1000.00")
    _paid_invoice(api_session, second, 1, "5000.00")

    def overview(**params: Any) -> dict[str, Any]:
        response = logged_in.get("/api/analytics/overview", params={"year": YEAR, **params})
        assert response.status_code == 200, response.text
        return response.json()

    all_entities = overview()
    studio = overview(legal_entity_id=default)
    foreign = overview(legal_entity_id=second)
    assert (studio["pension_fund"]["paid"], foreign["pension_fund"]["paid"]) == (
        "1000.00",
        "5000.00",
    )
    assert all_entities["pension_fund"]["paid"] == "6000.00"
    assert (all_entities["legal_entity_id"], studio["legal_entity_id"]) == (None, default)
    # Two legal entities, no estimate in "all entities": the page asks `/fiscal` per
    # legal entity instead.
    assert all_entities["tax"] is None
    assert studio["tax"] is not None and studio["tax"]["revenue"] == "1000.00"
    assert {row["legal_entity_id"] for row in all_entities["customer_concentration"]} == {
        default,
        second,
    }
    assert all(row["share"] == 1.0 for row in all_entities["customer_concentration"])
    pnl = logged_in.get("/api/analytics/pnl", params={**PERIOD, "legal_entity_id": second})
    assert pnl.status_code == 200, pnl.text
    assert pnl.json()["legal_entity_id"] == second


# --- the year of one azienda, and its CSV for the accountant (REB-757) -----------------


def test_the_year_routes_answer_for_one_legal_entity_and_the_csv_is_to_spreadsheet_download(
    logged_in: TestClient, api_session: Session, fiscal_profile: dict[str, Any]
) -> None:
    """The wire only: the register of the default azienda as JSON, then the same year as a
    file whose headers an Italian-locale Excel opens (UTF-8 with BOM, an attachment with an
    ASCII name). What the figures mean is proven in `test_year_overview.py`."""
    default = logged_in.get("/api/legal-entities").json()[0]["id"]
    _paid_invoice(api_session, default, 1, "1200.50")

    year = logged_in.get("/api/analytics/year", params={"year": YEAR})
    assert year.status_code == 200, year.text
    body = year.json()
    assert body["legal_entity_id"] == default
    assert body["total"]["taxable_amount"] == "1200.50"
    assert [row["number"] for row in body["register"]] == [1]

    file = logged_in.get("/api/analytics/year/csv", params={"year": YEAR})
    assert file.status_code == 200, file.text
    assert file.headers["content-type"] == "text/csv; charset=utf-8"
    assert file.headers["content-disposition"].startswith(
        f"attachment; filename*=UTF-8''registro-{YEAR}-"
    )
    assert file.content.startswith(b"\xef\xbb\xbf")
    assert "1.200,50" in file.content.decode("utf-8")


def test_the_year_routes_need_the_legal_entity_on_two_and_to_year_in_range(
    logged_in: TestClient, fiscal_profile: dict[str, Any]
) -> None:
    default = logged_in.get("/api/legal-entities").json()[0]["id"]
    assert logged_in.post("/api/legal-entities", json=LTD).status_code == 201
    for path in ("/api/analytics/year", "/api/analytics/year/csv"):
        refused = logged_in.get(path, params={"year": YEAR})
        assert refused.status_code == 422, (path, refused.text)
        assert refused.json()["field"] == "legal_entity_id", path
        named = logged_in.get(path, params={"year": YEAR, "legal_entity_id": default})
        assert named.status_code == 200, (path, named.text)
        assert (
            logged_in.get(path, params={"year": 1999, "legal_entity_id": default}).status_code
            == 422
        )


def test_a_readonly_session_reads_the_year_like_it_reads_the_register(
    logged_in: TestClient, fiscal_profile: dict[str, Any]
) -> None:
    reader = _second_actor(logged_in, "readonly")
    assert reader.get("/api/analytics/year", params={"year": YEAR}).status_code == 200
    assert reader.get("/api/analytics/year/csv", params={"year": YEAR}).status_code == 200


# --- the days of a period (REB-756) ------------------------------------------------------


def test_the_days_route_answers_by_month_and_state_and_takes_one_legal_entity(
    logged_in: TestClient, api_session: Session
) -> None:
    """The wire only: a recorded day lands in its month and state, the period is both
    bounds or neither, and another azienda's filter leaves the figure out. What the
    figures mean is proven in `test_days_analysis.py`."""
    default = logged_in.get("/api/legal-entities").json()[0]["id"]
    customer = Organization(legal_name="Acme S.r.l.")
    api_session.add(customer)
    api_session.flush()
    contract = Contract(
        customer_id=customer.id,
        title="Consulenza",
        start=TODAY.replace(month=1, day=1),
        renewal_type="none",
        termination_notice_days=30,
        billing_cadence="monthly",
        expense_policy={"kind": "not_reimbursable"},
    )
    api_session.add(contract)
    api_session.flush()
    api_session.add(
        WorkUnit(
            contract_id=contract.id,
            date=TODAY,
            quantity=Decimal("1.00"),
            description="Giornata di consulenza",
            status="worked",
        )
    )
    api_session.commit()

    month = logged_in.get("/api/analytics/days")
    assert month.status_code == 200, month.text
    body = month.json()
    assert [m["month"] for m in body["months"]] == [TODAY.strftime("%Y-%m")]
    assert body["days_in_period"] == "1.00"
    worked = next(s for s in body["months"][0]["by_status"] if s["status"] == "worked")
    assert worked["days"] == "1.00"

    year = logged_in.get(
        "/api/analytics/days", params={"from": DA, "to": A, "legal_entity_id": default}
    )
    assert year.status_code == 200, year.text
    assert len(year.json()["months"]) == 12
    assert year.json()["legal_entity_id"] == default

    other = logged_in.post("/api/legal-entities", json=LTD)
    assert other.status_code == 201, other.text
    elsewhere = logged_in.get("/api/analytics/days", params={"legal_entity_id": other.json()["id"]})
    assert elsewhere.json()["days_in_period"] == "0.00"

    half = logged_in.get("/api/analytics/days", params={"from": DA})
    assert half.status_code == 422
    assert half.json()["field"] == "to"


def test_an_admin_types_a_declared_figure_and_the_fiscal_report_shows_it_beside_the_estimate(
    logged_in: TestClient, fiscal_profile: dict[str, Any]
) -> None:
    put = logged_in.put(
        "/api/analytics/fiscal/declared",
        json={"tax": "substitute_tax", "year": YEAR, "balance": "-120.50", "tax_due": "800"},
    )
    assert put.status_code == 200, put.text
    assert put.json()["reading"] == "declared"
    report = logged_in.get("/api/analytics/fiscal", params={"year": YEAR}).json()
    assert report["estimate"] is True
    assert report["reading"] == "declared"
    assert [(f["tax"], f["tax_due"], f["balance"], f["advances"]) for f in report["declared"]] == [
        ("substitute_tax", "800.00", "-120.50", None)
    ]


def test_the_fiscal_report_lists_the_pack_taxes_with_three_readings_and_the_server_s_totals(
    logged_in: TestClient, fiscal_profile: dict[str, Any]
) -> None:
    """REB-1026: the page of «Tasse» reads lines, the reading that counts and column totals
    from the report, and adds nothing itself."""
    before = logged_in.get("/api/analytics/fiscal", params={"year": YEAR}).json()["readings"]
    assert [
        (line["tax"], line["declared"], line["paid"], line["counts"]) for line in before["lines"]
    ] == [
        ("substitute_tax", None, None, "estimate"),
        ("inps", None, None, "estimate"),
    ]
    assert before["totals"]["declared"] is None
    assert before["totals"]["paid"] is None

    logged_in.put(
        "/api/analytics/fiscal/declared",
        json={"tax": "substitute_tax", "year": YEAR, "tax_due": "800"},
    )
    after = logged_in.get("/api/analytics/fiscal", params={"year": YEAR}).json()["readings"]
    first, second = after["lines"]
    assert (first["declared"], first["counts"], second["counts"]) == (
        "800.00",
        "declared",
        "estimate",
    )
    assert after["totals"]["declared"] == "800.00"
    assert after["totals"]["declared_left_out"] == 1


def test_the_tax_calendar_is_the_packs_dates_and_an_admin_s(
    logged_in: TestClient, fiscal_profile: dict[str, Any]
) -> None:
    response = logged_in.get("/api/analytics/fiscal/calendar", params={"year": 2026})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pack_id"] == "it-flat-rate"
    assert [(entry["nominal_date"], entry["tax"]) for entry in body["dates"]] == [
        ("2026-06-30", "substitute_tax"),
        ("2026-11-30", "substitute_tax"),
    ]
    # A year the pack has no entry for answers an empty list, never another year's dates.
    assert (
        logged_in.get("/api/analytics/fiscal/calendar", params={"year": 2025}).json()["dates"] == []
    )
    collaborator = _second_actor(logged_in, "collaborator")
    assert (
        collaborator.get("/api/analytics/fiscal/calendar", params={"year": 2026}).status_code == 403
    )


def test_a_declared_figure_needs_an_admin_and_one_amount(
    logged_in: TestClient, fiscal_profile: dict[str, Any]
) -> None:
    collaborator = _second_actor(logged_in, "collaborator")
    body = {"tax": "irpef", "year": YEAR, "balance": "10"}
    assert collaborator.put("/api/analytics/fiscal/declared", json=body).status_code == 403
    empty = logged_in.put("/api/analytics/fiscal/declared", json={"tax": "irpef", "year": YEAR})
    assert empty.status_code == 422
