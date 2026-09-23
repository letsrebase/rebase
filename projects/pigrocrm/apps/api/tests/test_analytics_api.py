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

from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from pigrocrm.core.clock import oggi_in_italia

# Never a literal year: `_check_issue_date` refuses a `data_emissione` outside the
# current year, so a hard-coded 2026 would be a suite that starts failing on the first
# of January. The window below is the whole of the current year, which always contains
# both today's hours and today's issued invoices.
OGGI = oggi_in_italia()
ANNO = OGGI.year
DA = OGGI.replace(month=1, day=1).isoformat()
A = OGGI.replace(month=12, day=31).isoformat()
PERIODO = {"from": DA, "to": A}

PASSWORD = "supersegreta1"


def _second_actor(admin_client: TestClient, ruolo: str) -> TestClient:
    """A genuinely independent session, not `admin_client`'s own cookie jar. Copied from
    `test_invoices_api.py`'s helper of the same name and purpose -- see its docstring for
    why `logged_in` and `collaborator_client` cannot both be requested in one test."""
    email = f"{ruolo}-analytics-{id(admin_client)}@pigro.it"
    created = admin_client.post(
        "/api/users",
        json={"email": email, "password": PASSWORD, "nome": "Test", "ruolo": ruolo},
    )
    assert created.status_code == 201, created.text
    client = TestClient(admin_client.app, base_url="https://testserver")
    response = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return client


@pytest.fixture
def emitter(logged_in: TestClient) -> dict[str, Any]:
    """Required by `issue`, not by anything analytics does -- only the two tests that
    take an invoice all the way to `emessa` need it."""
    response = logged_in.put(
        "/api/emitter",
        json={
            "ragione_sociale": "Studio Rossi",
            "partita_iva": "01234567890",
            "codice_fiscale": "HMCRFT00A01H501K",
            "indirizzo": "Via Vittorio Veneto 12",
            "cap": "20124",
            "comune": "Milano",
            "provincia": "MI",
            "nazione": "IT",
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
        "/api/fiscal-profile",
        json={
            "codice_regime": "RF19",
            "coefficiente_redditivita": "67.00",
            "aliquota_imposta_sostitutiva": "5.00",
            "aliquota_inps": "26.07",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _seed_deal_and_user(
    logged_in: TestClient,
    *,
    ore_preventivate: str | None = None,
    valore_preventivato: str | None = None,
) -> tuple[str, str]:
    """A customer complete enough to be invoiced (the `to-invoice-draft` tests take one
    all the way to `emessa`), one deal, one collaborator to log hours against."""
    assert logged_in.post("/api/pipeline-stages/seed").status_code == 200
    customer = logged_in.post(
        "/api/customers",
        json={
            "ragione_sociale": "Acme S.r.l.",
            "partita_iva": "12345678901",
            "codice_sdi": "ABCDEFG",
            "indirizzo": "Corso Italia 5",
            "cap": "00100",
            "comune": "Roma",
            "provincia": "RM",
            "nazione": "IT",
        },
    )
    assert customer.status_code == 201, customer.text
    body: dict[str, Any] = {"nome": "Progetto", "customer_id": customer.json()["id"]}
    if ore_preventivate is not None:
        body["ore_preventivate"] = ore_preventivate
    if valore_preventivato is not None:
        body["valore_preventivato"] = valore_preventivato
    deal = logged_in.post("/api/deals", json=body)
    assert deal.status_code == 201, deal.text
    user = logged_in.post(
        "/api/users",
        json={
            "email": f"worker-analytics-{id(logged_in)}@pigro.it",
            "password": PASSWORD,
            "nome": "Worker",
            "ruolo": "collaboratore",
        },
    )
    assert user.status_code == 201, user.text
    return deal.json()["id"], user.json()["id"]


def _log_hours(
    logged_in: TestClient,
    deal_id: str,
    user_id: str,
    *,
    ore: str = "4.00",
    tariffa: str = "100.000000",
    costo: str = "25.000000",
) -> str:
    response = logged_in.post(
        "/api/time-entries",
        json={
            "deal_id": deal_id,
            "user_id": user_id,
            "data": OGGI.isoformat(),
            "ore": ore,
            "descrizione": "Sviluppo",
            "tariffa_applicata": tariffa,
            "costo_applicato": costo,
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
    invoice (§3, decision 2), so `ricavi` is a real zero while `valore_maturato` is 400 --
    two fields precisely because they are two different facts."""
    deal_id, user_id = _seed_deal_and_user(logged_in)
    _log_hours(logged_in, deal_id, user_id)

    response = logged_in.get(f"/api/deals/{deal_id}/pnl")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ricavi"] == "0.00"
    assert body["fatture_emesse"] == 0
    # `null`, never `"0.00"`: zero per cent means "everything earned went out in costs",
    # a zero denominator means nothing has been earned. The wire keeps them apart.
    assert body["margine_percentuale"] is None
    assert body["stato"] == "in corso"
    assert body["costo_lavoro"] == "100.00"
    assert body["margine_lordo"] == "-100.00"
    assert body["valore_maturato"] == "400.00"
    assert body["valore_maturato"] != body["ricavi"]
    # Asserted on the raw text, because `response.json()` would already have parsed it:
    # a `Decimal` reaches the browser as a string, never as a binary float.
    assert '"costo_lavoro":"100.00"' in response.text.replace(" ", "")


def test_a_readonly_actor_may_read_the_economics(logged_in: TestClient) -> None:
    """No `require_write` and no `require_admin` on any read in this surface, deliberately:
    a deal's P&L is not more sensitive than the deal, the hours and the invoices it is
    derived from, each of which a reader can already list."""
    deal_id, user_id = _seed_deal_and_user(logged_in)
    _log_hours(logged_in, deal_id, user_id)
    readonly = _second_actor(logged_in, "readonly")

    assert readonly.get(f"/api/deals/{deal_id}/pnl").status_code == 200
    assert readonly.get("/api/analytics/pnl", params=PERIODO).status_code == 200


def test_the_deal_budget_row_comes_from_the_same_method_as_the_list(
    logged_in: TestClient,
) -> None:
    """Served from `budget_vs_actual` rather than reimplemented per deal, so the detail
    page and the report can never show different variances."""
    deal_id, user_id = _seed_deal_and_user(
        logged_in, ore_preventivate="10.00", valore_preventivato="1000.00"
    )
    _log_hours(logged_in, deal_id, user_id)

    response = logged_in.get(f"/api/deals/{deal_id}/budget", params=PERIODO)
    assert response.status_code == 200, response.text
    row = response.json()
    assert row["deal_id"] == deal_id
    assert row["ore_consuntivate"] == "4.00"
    assert row["avanzamento_ore"] == "40.00"
    # The pro-rata budget, not the full one: at 40% of the hours, 400 is the figure the
    # revenue is judged against.
    assert row["budget_pro_rata"] == "400.00"
    assert row["scostamento_valore"] == "-400.00"
    assert row["non_preventivato"] is False


def test_a_deal_with_no_activity_in_the_window_has_no_budget_row(
    logged_in: TestClient,
) -> None:
    """A 404 rather than a row of zeroes: `deals_in_range` returns deals with activity in
    the window, and a deal that had none did not "spend nothing" -- it was not in the
    report at all."""
    deal_id, _ = _seed_deal_and_user(logged_in)
    response = logged_in.get(f"/api/deals/{deal_id}/budget", params=PERIODO)
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

    response = logged_in.get("/api/analytics/pnl", params=PERIODO)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) >= {
        "chiusi",
        "in_corso",
        "spese_generali",
        "periodo_chiuso",
        "voci_scritte_in_ritardo",
    }
    # No combined total, deliberately: adding a finished job's margin to a half-done one
    # produces a figure that is neither (§7.4).
    assert "totale" not in body
    assert body["in_corso"]["deal"] == 1
    assert body["in_corso"]["costo_lavoro"] == "100.00"
    assert body["chiusi"]["deal"] == 0
    assert body["da"] == DA and body["a"] == A

    assert logged_in.get("/api/analytics/pnl", params={"da": DA, "a": A}).status_code == 422


def test_the_backlog_takes_no_period_and_serialises_its_decimals_as_strings(
    logged_in: TestClient,
) -> None:
    """§6.3's endpoint. `from`/`to` are absent on purpose -- "quanto ho da fatturare" is
    not a question about March -- so the route must answer with no query parameter at all,
    which is exactly the shape the mandatory-period test below proves `/pnl` refuses."""
    deal_id, user_id = _seed_deal_and_user(logged_in)
    _log_hours(logged_in, deal_id, user_id, ore="4.00", tariffa="100.000000")

    response = logged_in.get("/api/analytics/backlog")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {
        "ore_fatturabili_non_fatturate": "4.00",
        "valore_maturato": "400.00",
        "voci_senza_tariffa": 0,
        "voci": 1,
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
    assert response.json()["field"] == "a"


def test_the_budget_list_is_bounded(logged_in: TestClient) -> None:
    over = logged_in.get("/api/analytics/budget", params={**PERIODO, "limit": 500})
    assert over.status_code == 422
    at_the_ceiling = logged_in.get("/api/analytics/budget", params={**PERIODO, "limit": 200})
    assert at_the_ceiling.status_code == 200, at_the_ceiling.text


def test_an_unestimated_deal_is_excluded_from_the_budget_aggregates(
    logged_in: TestClient,
) -> None:
    """An absent estimate is not an estimate of zero: the row says so and the totals
    leave it out, or the aggregate would depend on how many deals nobody estimated."""
    deal_id, user_id = _seed_deal_and_user(logged_in)
    _log_hours(logged_in, deal_id, user_id)

    response = logged_in.get("/api/analytics/budget", params=PERIODO)
    assert response.status_code == 200, response.text
    page = response.json()
    assert [row["deal_id"] for row in page["items"]] == [deal_id]
    assert page["items"][0]["non_preventivato"] is True
    assert page["deal_non_preventivati"] == 1
    assert page["deal_preventivati"] == 0
    assert page["totale_preventivato"] == "0.00"
    assert page["next_cursor"] is None


# --- ceiling headroom and the "would this fit?" simulator (REB-373) -----------------


def test_the_ceiling_headroom_route_serialises_decimals_as_strings(
    logged_in: TestClient, fiscal_profile: dict[str, Any]
) -> None:
    response = logged_in.get("/api/analytics/ceilings", params={"anno": ANNO})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["anno"] == ANNO
    assert body["pack_id"] == "it-flat-rate"
    ricavi_soglia = next(s for s in body["soglie"] if s["id"] == "soglia_ricavi")
    assert ricavi_soglia["soglia"] == "85000.00"
    assert isinstance(ricavi_soglia["ricavi"], str)
    assert isinstance(ricavi_soglia["residuo"], str)


def test_the_ceiling_headroom_route_is_readable_by_a_readonly_actor(
    logged_in: TestClient, fiscal_profile: dict[str, Any]
) -> None:
    readonly = _second_actor(logged_in, "readonly")
    assert readonly.get("/api/analytics/ceilings", params={"anno": ANNO}).status_code == 200


def test_the_ceiling_headroom_route_is_a_404_without_a_fiscal_profile(
    logged_in: TestClient,
) -> None:
    response = logged_in.get("/api/analytics/ceilings", params={"anno": ANNO})
    assert response.status_code == 404, response.text
    assert response.json()["code"] == "not_found"


def test_the_ceiling_headroom_route_requires_an_anno(logged_in: TestClient) -> None:
    assert logged_in.get("/api/analytics/ceilings").status_code == 422


def test_the_simulator_adds_the_typed_value_and_answers_would_this_fit(
    logged_in: TestClient, fiscal_profile: dict[str, Any]
) -> None:
    response = logged_in.get(
        "/api/analytics/ceilings/simulate",
        params={"anno": ANNO, "valore_preventivato": "20000.00"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["aggiunta_sintetica"] == "20000.00"
    ricavi_soglia = next(s for s in body["soglie"] if s["id"] == "soglia_ricavi")
    assert ricavi_soglia["ricavi_simulati"] == "20000.00"
    assert ricavi_soglia["rientra"] is True


def test_the_simulator_derives_the_addition_from_hours_and_rate(
    logged_in: TestClient, fiscal_profile: dict[str, Any]
) -> None:
    response = logged_in.get(
        "/api/analytics/ceilings/simulate",
        params={"anno": ANNO, "ore_preventivate": "100.00", "tariffa_oraria": "250.000000"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["aggiunta_sintetica"] == "25000.00"


def test_the_simulator_without_any_estimate_is_a_422_naming_the_field(
    logged_in: TestClient, fiscal_profile: dict[str, Any]
) -> None:
    response = logged_in.get("/api/analytics/ceilings/simulate", params={"anno": ANNO})
    assert response.status_code == 422, response.text
    assert response.json()["field"] == "valore_preventivato"


def test_the_simulator_is_readable_by_a_readonly_actor(
    logged_in: TestClient, fiscal_profile: dict[str, Any]
) -> None:
    readonly = _second_actor(logged_in, "readonly")
    response = readonly.get(
        "/api/analytics/ceilings/simulate",
        params={"anno": ANNO, "valore_preventivato": "1000.00"},
    )
    assert response.status_code == 200, response.text


# --- the fiscal estimate ------------------------------------------------------------


def test_the_fiscal_report_is_admin_only(logged_in: TestClient) -> None:
    collaboratore = _second_actor(logged_in, "collaboratore")
    response = collaboratore.get("/api/analytics/fiscal", params={"anno": ANNO})
    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"


def test_the_fiscal_report_is_labelled_an_estimate_in_its_own_payload(
    logged_in: TestClient,
    emitter: dict[str, Any],
    fiscal_profile: dict[str, Any],
) -> None:
    """400 EUR invoiced, at 67% / 5% / 26.07%: imponibile 268.00, imposta 13.40,
    contributi 66.37, netto 320.23 -- the chain checked end to end over HTTP, because a
    report that silently lost a rate would still answer 200 with plausible numbers."""
    deal_id, user_id = _seed_deal_and_user(logged_in)
    entry_id = _log_hours(logged_in, deal_id, user_id)
    draft = logged_in.post(
        f"/api/deals/{deal_id}/time-entries/to-invoice-draft", json={"entry_ids": [entry_id]}
    )
    assert draft.status_code == 200, draft.text
    issued = logged_in.post(f"/api/invoices/{draft.json()['id']}/issue", json={})
    assert issued.status_code == 200, issued.text
    assert issued.json()["imponibile"] == "400.00"

    response = logged_in.get("/api/analytics/fiscal", params={"anno": ANNO})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["anno"] == ANNO
    assert body["stima"] is True
    assert "Stima indicativa" in body["avvertenza"]
    assert body["ricavi"] == "400.00"
    assert body["imponibile"] == "268.00"
    assert body["imposta_sostitutiva"] == "13.40"
    assert body["contributi"] == "66.37"
    assert body["reddito_netto_stimato"] == "320.23"


# --- the one writer -----------------------------------------------------------------


def test_to_invoice_draft_is_admin_only(logged_in: TestClient) -> None:
    deal_id, _ = _seed_deal_and_user(logged_in)
    collaboratore = _second_actor(logged_in, "collaboratore")
    response = collaboratore.post(
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
    assert invoice["stato"] == "bozza"
    assert invoice["deal_id"] == deal_id
    assert invoice["imponibile"] == "400.00"

    pnl = logged_in.get(f"/api/deals/{deal_id}/pnl").json()
    assert pnl["ricavi"] == "0.00"
    assert pnl["ore_fatturabili_non_fatturate"] == "4.00"


def test_a_draft_can_be_rewritten_but_an_issued_invoice_cannot(
    logged_in: TestClient,
    emitter: dict[str, Any],
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
    assert problem["numero"] == issued.json()["numero"]
    assert problem["anno"] == ANNO
    assert "due volte" in problem["detail"]


def test_an_entry_from_another_deal_is_a_422_naming_the_field(logged_in: TestClient) -> None:
    deal_id, user_id = _seed_deal_and_user(logged_in)
    other = logged_in.post(
        "/api/deals",
        json={
            "nome": "Altro progetto",
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
    anno = ANNO
    admin = logged_in.get("/api/analytics/overview", params={"anno": anno})
    assert admin.status_code == 200, admin.text
    body = admin.json()
    assert body["cassa"]["anno"] == anno
    assert len(body["cassa"]["mesi"]) == 12
    for key in ("incassato", "da_incassare", "bozze", "proiettato", "costi", "lordo_effettivo"):
        assert key in body["cassa"]
    # Without a fiscal profile even an admin gets cash alone, never a 404.
    assert body["fiscale"] is None

    other = collaborator_client.get("/api/analytics/overview", params={"anno": anno})
    assert other.status_code == 200, other.text
    assert other.json()["fiscale"] is None


# --- the cash view reads by accrual period, or by the money (ORB-133) ------------------


def test_the_economic_overview_takes_a_cash_base_and_defaults_to_accrual(
    logged_in: TestClient,
) -> None:
    """`base=competenza|incasso` beside `anno`, competenza when omitted (Ivan's default),
    echoed under `cassa.base` so the page can label what it draws, and a 422 for any
    other word -- `emissione` included, which is the P&L's base and not this view's. The
    arithmetic of the two readings is `packages/core/tests/test_analytics_cash.py`."""
    default = logged_in.get("/api/analytics/overview", params={"anno": ANNO})
    assert default.status_code == 200, default.text
    assert default.json()["cassa"]["base"] == "competenza"

    incasso = logged_in.get("/api/analytics/overview", params={"anno": ANNO, "base": "incasso"})
    assert incasso.status_code == 200, incasso.text
    assert incasso.json()["cassa"]["base"] == "incasso"

    for wrong in ("emissione", "cassa"):
        refused = logged_in.get("/api/analytics/overview", params={"anno": ANNO, "base": wrong})
        assert refused.status_code == 422, refused.text

    paths: dict[str, Any] = logged_in.get("/openapi.json").json()["paths"]
    params = {p["name"]: p for p in paths["/api/analytics/overview"]["get"]["parameters"]}
    assert params["base"]["required"] is False
    assert set(params["base"]["schema"]["enum"]) == {"competenza", "incasso"}
    assert params["base"]["schema"]["default"] == "competenza"
    assert "competenza" in params["base"]["description"]


# --- the second reading of revenue, by accrual period (ORB-61) -----------------------


def _ricavi(body: dict[str, Any]) -> str:
    """The one deal's revenue, whichever column `deal_summary` put it in."""
    column = body["chiusi"] if body["chiusi"]["deal"] else body["in_corso"]
    ricavi: str = column["ricavi"]
    return ricavi


def test_the_period_report_takes_a_base_and_defaults_to_emission(
    logged_in: TestClient, emitter: dict[str, Any]
) -> None:
    """`base=competenza` is a query parameter beside `from`/`to`; anything else is a 422
    and an omitted one is the recorded default. The arithmetic of the two readings is
    proven in `packages/core/tests/test_period_pnl.py`; this asserts the wire, with one
    invoice issued today for last month's work so the two readings visibly differ."""
    assert logged_in.put("/api/fiscal-profile", json={"codice_regime": "RF19"}).status_code == 200
    deal_id, _ = _seed_deal_and_user(logged_in)
    customer_id = logged_in.get(f"/api/deals/{deal_id}").json()["customer_id"]
    primo = OGGI.replace(day=1)
    fine_mese_prima = primo - timedelta(days=1)
    draft = logged_in.post(
        "/api/invoices",
        json={
            "customer_id": customer_id,
            "deal_id": deal_id,
            "competenza_da": fine_mese_prima.replace(day=1).isoformat(),
            "competenza_a": fine_mese_prima.isoformat(),
            "righe": [{"descrizione": "Consulenza", "prezzo_unitario": "1000.00"}],
        },
    )
    assert draft.status_code == 201, draft.text
    issued = logged_in.post(f"/api/invoices/{draft.json()['id']}/issue", json={})
    assert issued.status_code == 200, issued.text

    this_month = {"from": primo.isoformat(), "to": OGGI.isoformat()}
    by_emission = logged_in.get("/api/analytics/pnl", params=this_month)
    assert by_emission.status_code == 200, by_emission.text
    by_accrual = logged_in.get("/api/analytics/pnl", params={**this_month, "base": "competenza"})
    assert by_accrual.status_code == 200, by_accrual.text
    assert _ricavi(by_emission.json()) == "1000.00"
    assert _ricavi(by_accrual.json()) == "0.00"
    # The body echoes the reading, so the web client can label the figure it shows
    # without carrying the request around beside it.
    assert by_emission.json()["base"] == "emissione"
    assert by_accrual.json()["base"] == "competenza"
    last_month = {
        "from": fine_mese_prima.replace(day=1).isoformat(),
        "to": fine_mese_prima.isoformat(),
        "base": "competenza",
    }
    assert _ricavi(logged_in.get("/api/analytics/pnl", params=last_month).json()) == "1000.00"

    unknown = logged_in.get("/api/analytics/pnl", params={**this_month, "base": "cassa"})
    assert unknown.status_code == 422, unknown.text


def test_the_openapi_document_describes_the_base_parameter_in_italian(
    logged_in: TestClient,
) -> None:
    paths: dict[str, Any] = logged_in.get("/openapi.json").json()["paths"]
    params = {p["name"]: p for p in paths["/api/analytics/pnl"]["get"]["parameters"]}
    assert "base" in params
    assert params["base"]["required"] is False
    assert set(params["base"]["schema"]["enum"]) == {"emissione", "competenza"}
    assert params["base"]["schema"]["default"] == "emissione"
    assert "competenza" in params["base"]["description"]
