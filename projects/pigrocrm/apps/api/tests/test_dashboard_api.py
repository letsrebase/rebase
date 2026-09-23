"""The HTTP surface of §4, plus the configuration endpoints of §9.6.

**Why this file builds its own sessions.** A dashboard is one transaction in
`REPEATABLE READ`, and Postgres refuses to change the isolation level once a transaction
has begun -- so `DashboardService` needs a session nothing has touched. The suite's
`api_session` is the opposite of that: it is bound to a connection inside an outer
transaction that is rolled back at teardown, and `ActorDep` has already read `users` on
it by the time any route body runs. The route therefore takes `SnapshotSessionDep`, a
second session resolved per request, and this file overrides that dependency with real
sessions on `api_engine` and commits the corpus they can see. The rows are removed in the
teardown, `pipeline_stages` included: `api_engine` is session-scoped, and six stages left
behind would be six stages every other API test did not create.

`test_authentication_does_not_poison_the_snapshot` is the one that pins the reason. Point
the route at the ordinary `SessionDep` and it is the test that fails, with the service's
own `RuntimeError` rather than a wrong number -- which is the whole design of
`_open_snapshot`.
"""

from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal
from urllib.parse import parse_qsl, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, select

from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.dashboard.schemas import PeriodoQuery
from pigrocrm.core.dashboard.service import DashboardService
from pigrocrm.core.db import session_factory, today_local
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.documents.models import Document
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.pipeline.models import PipelineStage
from pigrocrm.core.pipeline.service import PipelineService
from pigrocrm_api.deps import get_snapshot_session

SEED = Actor(id=None, type="system", role="admin")
_PREFIX = "APIDASH"

EXPECTED_KEYS = {
    "periodo",
    "calcolato_alle",
    "pipeline",
    "chiusure",
    "offerte_in_attesa",
    "offerte_in_attesa_totale",
    "chiusure_previste_30_giorni",
    "chiusure_non_attribuibili",
    "offerte_accettate_deal_non_vinto",
}

ECONOMIC_KEYS = {"periodo", "calcolato_alle", "pnl", "da_incassare", "scaduto", "fatture_emesse"}

OPERATIONAL_KEYS = {"calcolato_alle", "settimana", "arretrato", "segnali", "attivita_recenti"}

RECEIVABLES_KEYS = {
    "calcolato_alle",
    "oggi",
    "totale",
    "fasce",
    "per_mese",
    "per_cliente",
    "scadute",
    "scadute_totale",
}

# Every drill-through this slice ships, as the pair criterion 2 actually cares about: the
# link a card carries, and the REST list it has to resolve to. A signal whose link named a
# filter the API does not declare is a card whose list can never equal it -- criterion 2's
# failure in its purest form, and one no core-side test can see, because the link is only a
# string until an adapter has to honour it.
SIGNAL_LINK_TARGETS = {
    "/app/deal/list": "/api/deals",
    "/app/invoices": "/api/invoices",
}


@pytest.fixture
def dashboard_corpus(client: TestClient, api_engine: Engine) -> Iterator[Engine]:
    """Committed rows plus the per-request snapshot session that can see them.

    Committed, not flushed: the dashboard runs in its own transaction on its own
    connection, so uncommitted fixture data is invisible to it by construction. That is
    the property, not a limitation -- a dashboard that could read another transaction's
    uncommitted rows would not be one instant.
    """
    factory = session_factory(api_engine)
    with factory() as session:
        stages = {s.code: s for s in PipelineService(session).seed_defaults(SEED) if s.code}
        customer = Customer(ragione_sociale=f"{_PREFIX} Cliente", nazione="IT", custom_fields={})
        session.add(customer)
        session.flush()
        aperto = Deal(
            nome=f"{_PREFIX} aperto",
            customer_id=customer.id,
            pipeline_stage_id=stages["lead"].id,
            valore_previsto=Decimal("1000.00"),
            probabilita=50,
            custom_fields={},
        )
        session.add(aperto)
        vinto = Deal(
            nome=f"{_PREFIX} vinto",
            customer_id=customer.id,
            pipeline_stage_id=stages["vinto"].id,
            valore_previsto=Decimal("5000.00"),
            probabilita=100,
            chiuso_il=today_local(),
            custom_fields={},
        )
        session.add(vinto)
        session.add(
            Document(
                customer_id=customer.id,
                tipo="offerta",
                titolo=f"{_PREFIX} offerta",
                stato="inviata",
                stato_dal=today_local(),
                versione_corrente=1,
                custom_fields={},
            )
        )
        session.flush()
        # One issued, unpaid, past-due invoice attached to the *open* deal. It is what
        # stops the two new dashboards from being tested against an empty register, and it
        # is deliberately load-bearing on six figures at once: `pnl.in_corso` (its deal
        # sits on an open stage), `fatture_emesse`, `da_incassare`, `scaduto`, and two of
        # the three operational signals -- `fatturato_non_vinto` and
        # `scaduto_non_incassato`. Without it, the money-as-a-string test below would be
        # asserting the type of `"0.00"` on every path, which a dashboard wired to nothing
        # at all would pass just as well.
        #
        # `data_emissione` is today, so the row falls inside the default period (the
        # current month) whatever day the suite runs; `data_scadenza` is yesterday, so it
        # is overdue under `_overdue_predicate`'s strict `<`.
        session.add(
            Invoice(
                customer_id=customer.id,
                deal_id=aperto.id,
                tipo="fattura",
                stato="emessa",
                stato_pagamento="da_incassare",
                imponibile=Decimal("1000.00"),
                imposta=Decimal("0.00"),
                bollo=Decimal("0.00"),
                totale=Decimal("1000.00"),
                data_emissione=today_local(),
                data_scadenza=today_local() - timedelta(days=1),
                causale=f"{_PREFIX} fattura",
                tipo_documento="TD01",
                divisa="EUR",
                custom_fields={},
            )
        )
        # A second issued invoice that is **not** overdue, on the *won* deal. Without it
        # every issued invoice in the register would be overdue, and
        # `test_every_signal_link_names_a_filter_the_api_actually_declares` could not tell
        # `?scadute=true` from a filter that does nothing at all: the filtered list and the
        # unfiltered one would be the same length, which is precisely the shape of the
        # defect criterion 2 exists to catch. It also keeps `fatturato_non_vinto` at one
        # deal out of two rather than moving the count, because its stage is `won`.
        session.add(
            Invoice(
                customer_id=customer.id,
                deal_id=vinto.id,
                tipo="fattura",
                stato="emessa",
                stato_pagamento="da_incassare",
                imponibile=Decimal("500.00"),
                imposta=Decimal("0.00"),
                bollo=Decimal("0.00"),
                totale=Decimal("500.00"),
                data_emissione=today_local(),
                data_scadenza=today_local() + timedelta(days=30),
                causale=f"{_PREFIX} fattura corrente",
                tipo_documento="TD01",
                divisa="EUR",
                custom_fields={},
            )
        )
        session.commit()

    def provide() -> Iterator[object]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    client.app.dependency_overrides[get_snapshot_session] = provide
    try:
        yield api_engine
    finally:
        client.app.dependency_overrides.pop(get_snapshot_session, None)
        with factory() as session:
            session.execute(delete(Document).where(Document.titolo.like(f"{_PREFIX} %")))
            # Before the deals: the invoice carries a foreign key to one of them. Scoped
            # to this file's own customer rather than a wholesale `DELETE FROM invoices`,
            # which would also destroy whatever another API test has committed.
            session.execute(
                delete(Invoice).where(
                    Invoice.customer_id.in_(
                        select(Customer.id).where(Customer.ragione_sociale.like(f"{_PREFIX} %"))
                    )
                )
            )
            session.execute(delete(Deal).where(Deal.nome.like(f"{_PREFIX} %")))
            session.execute(delete(Customer).where(Customer.ragione_sociale.like(f"{_PREFIX} %")))
            session.execute(delete(PipelineStage))
            session.commit()


# -- the commercial dashboard ----------------------------------------------------


def test_the_commercial_dashboard_is_one_request(
    logged_in: TestClient, dashboard_corpus: Engine
) -> None:
    """One endpoint, not one per card (§7.1). The key set is asserted whole: a card added
    to the schema without a place on the page, or one quietly dropped, both fail here."""
    response = logged_in.get("/api/dashboard/sales")
    assert response.status_code == 200, response.text
    assert set(response.json()) == EXPECTED_KEYS


def test_authentication_does_not_poison_the_snapshot(
    logged_in: TestClient, dashboard_corpus: Engine
) -> None:
    """The reason this route does not take `SessionDep`.

    `ActorDep` reads `users` to resolve the cookie, which autobegins a transaction on the
    session it was handed -- and `DashboardService._open_snapshot` refuses a session that
    is already in one, loudly, rather than running in `READ COMMITTED` and returning a
    total that was true at no instant. Sharing one session between the two would make
    every dashboard request a 500. So there are two sessions, and this is the test that
    says so: it fails the moment the route stops asking for its own.
    """
    response = logged_in.get("/api/dashboard/sales")
    assert response.status_code == 200, response.text
    assert response.json()["calcolato_alle"]


def test_the_endpoint_returns_what_the_service_returns(
    logged_in: TestClient, dashboard_corpus: Engine
) -> None:
    """The adapter is thin: no figure is computed, reshaped or renamed on the way out.

    Compared against the service reached directly on its own fresh session, over the same
    committed corpus -- not against numbers recomputed here, which would make this file a
    second source of truth for figures §3 says only one place may produce.
    """
    body = logged_in.get("/api/dashboard/sales").json()
    with session_factory(dashboard_corpus)() as session:
        direct = DashboardService(session).get_commercial_dashboard(PeriodoQuery(), SEED)
    expected = direct.model_dump(mode="json")
    assert body["pipeline"] == expected["pipeline"]
    assert body["chiusure"] == expected["chiusure"]
    assert body["offerte_in_attesa_totale"] == expected["offerte_in_attesa_totale"]
    assert next(row for row in body["pipeline"] if row["stage_code"] == "lead")["numero"] == 1


def test_the_period_round_trips_through_the_query_string(
    logged_in: TestClient, dashboard_corpus: Engine
) -> None:
    response = logged_in.get("/api/dashboard/sales", params={"da": "2026-03-01", "a": "2026-03-31"})
    assert response.json()["periodo"] == {"da": "2026-03-01", "a": "2026-03-31"}


def test_an_inverted_period_is_a_422_naming_the_field(
    logged_in: TestClient, dashboard_corpus: Engine
) -> None:
    response = logged_in.get("/api/dashboard/sales", params={"da": "2026-03-31", "a": "2026-03-01"})
    assert response.status_code == 422
    assert response.json()["field"] == "da"


@pytest.mark.parametrize(("da", "a"), [("2026-03-01", None), (None, "2026-03-31")])
def test_half_a_period_is_a_422_naming_the_missing_bound(
    logged_in: TestClient, dashboard_corpus: Engine, da: str | None, a: str | None
) -> None:
    """Both spellings, because a rule written as `if da is None` alone accepts the other
    one -- and `field` is what `fieldErrorFrom` in the web client reads."""
    params = {key: value for key, value in (("da", da), ("a", a)) if value is not None}
    response = logged_in.get("/api/dashboard/sales", params=params)
    assert response.status_code == 422
    assert response.json()["field"] == ("da" if da is None else "a")


def test_money_is_serialised_as_a_string(logged_in: TestClient, dashboard_corpus: Engine) -> None:
    """A JSON number is a float in every client that parses it, and a float total is the
    defect this whole slice is built to avoid.

    Asserted over a corpus with a real value in it, so the loop cannot pass by iterating
    over nothing -- and on the closure figures too, which are a different schema.
    """
    body = logged_in.get("/api/dashboard/sales").json()
    assert body["pipeline"], "the corpus produced no stage rows, so this test proved nothing"
    for row in body["pipeline"]:
        assert isinstance(row["valore_totale"], str), row
        assert isinstance(row["valore_ponderato"], str), row
    assert isinstance(body["chiusure"]["valore_vinto"], str)
    assert isinstance(body["chiusure"]["tasso_conversione"], str)


def test_a_readonly_actor_sees_the_dashboard(
    readonly_client: TestClient, dashboard_corpus: Engine
) -> None:
    """§13: this slice adds no role and no authorisation rule. Every figure here comes
    from a read every role already has."""
    assert readonly_client.get("/api/dashboard/sales").status_code == 200


def test_an_unauthenticated_request_is_a_401(client: TestClient, dashboard_corpus: Engine) -> None:
    assert client.get("/api/dashboard/sales").status_code == 401


# -- the economic dashboard ------------------------------------------------------


def test_the_economic_dashboard_is_one_request(
    logged_in: TestClient, dashboard_corpus: Engine
) -> None:
    """One endpoint, not one per card. The key set is asserted whole, and the P&L is
    asserted to be *embedded* rather than flattened: §5 adds no aggregate to this page, so
    a `ricavi` key at the top level would mean somebody had recombined the two columns on
    the way out."""
    response = logged_in.get(
        "/api/dashboard/economic", params={"da": "2026-03-01", "a": "2026-03-31"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == ECONOMIC_KEYS
    assert set(body["pnl"]) >= {
        "chiusi",
        "in_corso",
        "spese_generali",
        "periodo_chiuso",
        "voci_scritte_in_ritardo",
        "valore_maturato",
        "ore_fatturabili_non_fatturate",
        "ore_senza_tariffa",
    }


def test_the_economic_endpoint_returns_what_the_service_returns(
    logged_in: TestClient, dashboard_corpus: Engine
) -> None:
    """The adapter is thin on this route too. Compared against the service on its own
    fresh session over the same committed corpus, never against numbers recomputed here.

    `calcolato_alle` is excluded because the two calls are two transactions and two
    instants -- that it differs is the property, not a discrepancy.
    """
    body = logged_in.get("/api/dashboard/economic").json()
    with session_factory(dashboard_corpus)() as session:
        direct = DashboardService(session).get_economic_dashboard(PeriodoQuery(), SEED)
    expected = direct.model_dump(mode="json")
    for key in ECONOMIC_KEYS - {"calcolato_alle"}:
        assert body[key] == expected[key], key
    # The corpus commits exactly one issued invoice inside the default period, so this is
    # a figure with a known floor rather than an equality between two zeros.
    assert body["fatture_emesse"] >= 1
    assert body["da_incassare"] != "0.00"


def test_the_economic_dashboard_carries_no_fiscal_field(
    logged_in: TestClient, dashboard_corpus: Engine
) -> None:
    """§5.3: the fiscal estimate stays at `/app/analisi/fiscale`, admin-only. A dashboard
    is the screen most likely to end up in a screenshot or a screen share.

    Asserted on the raw response text rather than on the parsed keys, so a fiscal figure
    nested anywhere inside `pnl` -- where a future field on `PeriodPnl` would land without
    this file ever being edited -- fails here too.
    """
    body = logged_in.get("/api/dashboard/economic").text
    for forbidden in ("imponibile_fiscale", "imposta_sostitutiva", "contributi", "netto_stimato"):
        assert forbidden not in body, forbidden


def test_an_inverted_period_on_the_economic_endpoint_is_a_422_naming_the_field(
    logged_in: TestClient, dashboard_corpus: Engine
) -> None:
    """The same `PeriodoQuery.resolve`, so the same answer: the route adds no validation
    of its own and must not lose the one the service performs."""
    response = logged_in.get(
        "/api/dashboard/economic", params={"da": "2026-03-31", "a": "2026-03-01"}
    )
    assert response.status_code == 422
    assert response.json()["field"] == "da"


# -- the operational dashboard ---------------------------------------------------


def test_the_operational_dashboard_takes_no_period(
    logged_in: TestClient, dashboard_corpus: Engine
) -> None:
    """§6: the current week and a backlog are the two things that make no sense in the
    past, so the endpoint has no period parameter at all -- not an optional one.

    Asserted against the published schema, because that is what a client generates from:
    an optional `da`/`a` the service ignored would be a parameter the API advertises and
    does not honour, which is worse than not having one, since a caller would believe it
    worked.
    """
    schema = logged_in.get("/openapi.json").json()
    params = schema["paths"]["/api/dashboard/operational"]["get"].get("parameters", [])
    assert [p["name"] for p in params] == []

    response = logged_in.get("/api/dashboard/operational")
    assert response.status_code == 200, response.text
    assert set(response.json()) == OPERATIONAL_KEYS


def test_a_period_on_the_operational_endpoint_is_ignored_not_honoured(
    logged_in: TestClient, dashboard_corpus: Engine
) -> None:
    """A stray `?da=` must not silently produce a period-filtered answer from an endpoint
    that has no period. FastAPI ignores undeclared query parameters; this pins that the
    answer is byte-for-byte the one given without it, rather than merely a 200 -- a route
    that had quietly grown a period would still answer 200.

    `calcolato_alle` and `attivita_recenti` are excluded: the first is a different instant
    by construction, and the second is a global feed any other committed activity moves.
    """
    plain = logged_in.get("/api/dashboard/operational").json()
    with_period = logged_in.get(
        "/api/dashboard/operational", params={"da": "2020-01-01", "a": "2020-01-31"}
    )
    assert with_period.status_code == 200, with_period.text
    body = with_period.json()
    for key in ("settimana", "arretrato", "segnali"):
        assert body[key] == plain[key], key


def test_the_operational_endpoint_returns_what_the_service_returns(
    logged_in: TestClient, dashboard_corpus: Engine
) -> None:
    body = logged_in.get("/api/dashboard/operational").json()
    with session_factory(dashboard_corpus)() as session:
        direct = DashboardService(session).get_operational_dashboard(SEED)
    expected = direct.model_dump(mode="json")
    for key in ("settimana", "arretrato", "segnali"):
        assert body[key] == expected[key], key


def test_every_signal_carries_a_drill_through_link(
    logged_in: TestClient, dashboard_corpus: Engine
) -> None:
    """§6.2's three signals plus REB-371's concentration signal, in order, each with
    somewhere to go. A count with no way to see the rows behind it is a number nobody
    can act on."""
    body = logged_in.get("/api/dashboard/operational").json()
    assert [s["codice"] for s in body["segnali"]] == [
        "fatturato_non_vinto",
        "vinto_da_fatturare",
        "scaduto_non_incassato",
        "concentrazione_sopra_soglia",
    ]
    assert all(s["collegamento"] for s in body["segnali"])
    # The corpus's invoice makes two of the four non-zero, so the links below are being
    # checked against cards that have rows behind them. The fourth, concentration, needs
    # a fiscal-register `anno` no invoice here carries -- `dashboard_corpus`'s invoices
    # are never issued through `InvoiceService` -- and is its own dedicated test,
    # `test_the_concentration_signals_link_leads_to_the_same_rows_it_counted`, below.
    conteggi = {s["codice"]: s["conteggio"] for s in body["segnali"]}
    assert conteggi["fatturato_non_vinto"] >= 1
    assert conteggi["scaduto_non_incassato"] >= 1


def test_every_signal_link_names_a_filter_the_api_actually_declares(
    logged_in: TestClient, dashboard_corpus: Engine
) -> None:
    """**Criterion 2 at the adapter.** A card and its drill-through are the same predicate,
    and the core tests prove that for the predicate *functions*. What no core test can see
    is whether the link the card carries reaches them: `collegamento` is an opaque string
    until an HTTP client resolves it, and a signal pointing at `?da_fatturarE=true` would
    silently list every deal -- a list longer than the card, describing a different set.

    So each link is taken apart and its query parameter is looked up in the published
    OpenAPI schema of the list endpoint it corresponds to, then sent for real: declared,
    accepted, and narrowing. A parameter FastAPI does not declare is one it ignores.

    `concentrazione_sopra_soglia` is not in this loop, by design and not by omission: its
    own link is the concentration table itself, not a filtered list -- there is no
    "customers over the threshold" endpoint for a query parameter to narrow, so it would
    fail this loop's `items` assumption for a reason unrelated to the one the loop checks.
    `test_the_concentration_signals_link_leads_to_the_same_rows_it_counted`, immediately
    below, is its own criterion 2.
    """
    body = logged_in.get("/api/dashboard/operational").json()
    schema = logged_in.get("/openapi.json").json()
    assert body["segnali"], "no signals, so this test proved nothing"

    for signal in body["segnali"]:
        if signal["codice"] == "concentrazione_sopra_soglia":
            continue
        parts = urlsplit(signal["collegamento"])
        target = SIGNAL_LINK_TARGETS[parts.path]
        declared = {p["name"] for p in schema["paths"][target]["get"]["parameters"]}
        query = parse_qsl(parts.query)
        assert query, f"{signal['codice']} links nowhere in particular"
        for name, value in query:
            assert name in declared, f"{signal['codice']} -> {target} has no `{name}`"
            filtered = logged_in.get(target, params={name: value})
            assert filtered.status_code == 200, filtered.text
            # And it narrows: the filtered list must not be the unfiltered one. The corpus
            # has at least one row each filter excludes, so an inert filter shows up here
            # rather than in a count nobody compares.
            everything = logged_in.get(target).json()
            assert len(filtered.json()["items"]) < len(everything["items"]), name


def test_the_concentration_signals_link_leads_to_the_same_rows_it_counted(
    logged_in: TestClient, dashboard_corpus: Engine
) -> None:
    """The fourth signal's own criterion 2, shaped differently because its own link is
    (§3, §5 item 2 of
    `docs/superpowers/specs/2026-09-23-forecasting-and-analytics-from-mastro-design.md`):
    the economic tab's `concentrazione_clienti` table, not a filtered list. The card and
    the rows behind its link still must not disagree, checked here by recomputing, from
    the same threshold `/api/settings/space` reports as effective right now, exactly
    which customers the tab would show as over it -- and comparing the count to the
    signal's own.

    `dashboard_corpus`'s own invoices never carry `anno` (nothing else on this page reads
    it), so a customer with an actual fiscal-register entry is added here -- without one
    the signal would sit at zero and the equality below would prove nothing.
    """
    anno = today_local().year
    with session_factory(dashboard_corpus)() as session:
        cliente = Customer(ragione_sociale=f"{_PREFIX} Concentrato", nazione="IT", custom_fields={})
        session.add(cliente)
        session.flush()
        cliente_id = cliente.id
        session.add(
            Invoice(
                customer_id=cliente_id,
                tipo="fattura",
                stato="emessa",
                anno=anno,
                numero=8001,
                stato_pagamento="incassata",
                imponibile=Decimal("900.00"),
                imposta=Decimal("0.00"),
                bollo=Decimal("0.00"),
                totale=Decimal("900.00"),
                data_emissione=today_local(),
                tipo_documento="TD01",
                divisa="EUR",
                custom_fields={},
            )
        )
        session.commit()
    try:
        body = logged_in.get("/api/dashboard/operational").json()
        signal = next(s for s in body["segnali"] if s["codice"] == "concentrazione_sopra_soglia")
        assert signal["collegamento"] == "/app/?tab=economica"

        soglia = logged_in.get("/api/settings/space").json()["concentrazione_soglia_preferita"]
        overview = logged_in.get("/api/analytics/overview", params={"anno": anno}).json()
        sopra_soglia = [row for row in overview["concentrazione_clienti"] if row["quota"] > soglia]
        assert len(sopra_soglia) == signal["conteggio"]
        assert signal["conteggio"] >= 1, "the added customer alone should cross the default share"
    finally:
        with session_factory(dashboard_corpus)() as session:
            session.execute(delete(Invoice).where(Invoice.customer_id == cliente_id))
            session.execute(delete(Customer).where(Customer.id == cliente_id))
            session.commit()


# -- both new dashboards ---------------------------------------------------------


def test_money_is_serialised_as_a_string_on_both_new_dashboards(
    logged_in: TestClient, dashboard_corpus: Engine
) -> None:
    """A JSON number is a float in every client that parses it, and a float total is the
    defect this whole slice is built to avoid.

    The corpus puts a real invoice behind `da_incassare`, so this is not the type of a
    zero a route wired to nothing would also produce.
    """
    economic = logged_in.get("/api/dashboard/economic").json()
    assert isinstance(economic["da_incassare"], str)
    assert isinstance(economic["scaduto"], str)
    assert isinstance(economic["pnl"]["chiusi"]["ricavi"], str)
    assert isinstance(economic["pnl"]["in_corso"]["ricavi"], str)
    assert economic["da_incassare"] != "0.00"

    operational = logged_in.get("/api/dashboard/operational").json()
    assert isinstance(operational["arretrato"]["valore_maturato"], str)
    assert isinstance(operational["settimana"]["ore_totali"], str)


def test_the_receivables_dashboard_takes_no_period_and_adds_up(
    logged_in: TestClient, dashboard_corpus: Engine
) -> None:
    """Slice 8 part A (REB-329). No period, like `operational`, asserted on the published
    schema; and the six buckets, as strings, whose sum is the economic dashboard's own
    `da_incassare` -- the corpus has one overdue and one current invoice, so the two dated
    buckets are distinguishable from each other and from an empty register."""
    schema = logged_in.get("/openapi.json").json()
    params = schema["paths"]["/api/dashboard/receivables"]["get"].get("parameters", [])
    assert [p["name"] for p in params] == []

    response = logged_in.get("/api/dashboard/receivables")
    assert response.status_code == 200, response.text
    page = response.json()
    assert set(page) == RECEIVABLES_KEYS
    assert [f["codice"] for f in page["fasce"]] == [
        "scaduto",
        "entro_30",
        "da_31_a_60",
        "da_61_a_90",
        "oltre_90",
        "senza_scadenza",
    ]
    assert all(isinstance(f["importo"], str) for f in page["fasce"])
    assert isinstance(page["totale"], str)
    by_code = {f["codice"]: f for f in page["fasce"]}
    assert by_code["scaduto"]["importo"] == "1000.00"
    assert by_code["scaduto"]["collegamento"] == "/app/invoices?scadute=true"
    assert by_code["entro_30"]["importo"] == "500.00"
    assert page["totale"] == logged_in.get("/api/dashboard/economic").json()["da_incassare"]
    assert page["scadute_totale"] == 1
    assert page["scadute"][0]["solleciti_inviati"] == 0
    assert page["per_cliente"][0]["scaduto"] == "1000.00"


@pytest.mark.parametrize("path", ["sales", "economic", "operational", "receivables"])
def test_authentication_does_not_poison_the_snapshot_on_any_dashboard(
    logged_in: TestClient, dashboard_corpus: Engine, path: str
) -> None:
    """The reason all three routes take `SnapshotSessionDep` and not `SessionDep`.

    `ActorDep` resolves the cookie by reading `users`, which autobegins a transaction on
    the session it was handed, and `_open_snapshot` refuses a session already in one --
    loudly, rather than running in `READ COMMITTED` and returning a total that was true at
    no instant. Reaching for `SessionDep` out of habit makes *every* request a 500, and it
    is parametrised over all three so that the next route added by copy-paste is covered
    by the copy.
    """
    response = logged_in.get(f"/api/dashboard/{path}")
    assert response.status_code == 200, response.text
    assert response.json()["calcolato_alle"]


@pytest.mark.parametrize("path", ["sales", "economic", "operational"])
def test_a_readonly_actor_sees_all_three_dashboards(
    readonly_client: TestClient, dashboard_corpus: Engine, path: str
) -> None:
    """§13: no new role and no new authorisation rule. A readonly sees all three."""
    assert readonly_client.get(f"/api/dashboard/{path}").status_code == 200


@pytest.mark.parametrize("path", ["sales", "economic", "operational"])
def test_an_unauthenticated_request_to_any_dashboard_is_a_401(
    client: TestClient, dashboard_corpus: Engine, path: str
) -> None:
    assert client.get(f"/api/dashboard/{path}").status_code == 401


# -- the automation configuration ------------------------------------------------


def test_the_automation_config_is_readable_by_a_readonly_actor(
    readonly_client: TestClient,
) -> None:
    response = readonly_client.get("/api/automation-config")
    assert response.status_code == 200
    assert set(response.json()) == {
        "a1_offerta_accettata_vince_deal",
        "a2_offerta_inviata_avanza_deal",
    }


def test_only_an_admin_may_change_the_automation_config(
    readonly_client: TestClient,
) -> None:
    """403 and not 401: the caller is authenticated and simply may not do this. The check
    lives in the service, so it holds for every caller of it and not only for this route.

    Read back afterwards, because a refusal that had already written the row would answer
    403 and pass on the status code alone -- `require_admin` runs before anything is
    touched, and this is what says so.
    """
    forbidden = readonly_client.put(
        "/api/automation-config", json={"a1_offerta_accettata_vince_deal": False}
    )
    assert forbidden.status_code == 403
    assert (
        readonly_client.get("/api/automation-config").json()["a1_offerta_accettata_vince_deal"]
        is True
    )


def test_an_admin_switches_a_rule_off_and_it_stays_off(logged_in: TestClient) -> None:
    allowed = logged_in.put(
        "/api/automation-config", json={"a1_offerta_accettata_vince_deal": False}
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["a1_offerta_accettata_vince_deal"] is False
    # Read back through the other endpoint: a `PUT` that answered correctly without
    # persisting would pass on its own response alone.
    assert logged_in.get("/api/automation-config").json() == {
        "a1_offerta_accettata_vince_deal": False,
        "a2_offerta_inviata_avanza_deal": True,
    }


def test_an_unknown_field_on_the_config_is_refused(logged_in: TestClient) -> None:
    """`extra="forbid"`: a typo in a field name must not silently do nothing. The only
    thing a misspelled key can do on a settings form is report success for a change that
    was not made."""
    response = logged_in.put("/api/automation-config", json={"a3_qualcosa": True})
    assert response.status_code == 422


def test_the_description_names_both_rules_with_their_state(logged_in: TestClient) -> None:
    body = logged_in.get("/api/automations").json()
    assert [rule["codice"] for rule in body["regole"]] == ["A1", "A2"]
    assert all(rule["descrizione"] for rule in body["regole"])
    assert body["configurazione"]["a1_offerta_accettata_vince_deal"] is True


def test_the_runs_endpoint_returns_the_recent_activities(logged_in: TestClient) -> None:
    """§9.4: the run log is a read of `activities` by `kind`, never a new table. Changing
    the configuration is itself one of the three kinds, so it is the cheapest way to prove
    the endpoint reads the real timeline rather than an empty list."""
    logged_in.put("/api/automation-config", json={"a2_offerta_inviata_avanza_deal": False})
    response = logged_in.get("/api/automation-runs", params={"limit": 5})
    assert response.status_code == 200
    runs = response.json()
    assert "automazione.configurazione_modificata" in [run["kind"] for run in runs]
    # A configuration change is not about a deal, and a bare `entity_id` here would render
    # as a link to a deal that does not exist.
    assert all(run["deal_id"] is None for run in runs)


def test_the_runs_limit_is_bounded(logged_in: TestClient) -> None:
    """Bounded on the route, like every other list in the project: an unbounded `limit` is
    a full table scan requested from a query string."""
    assert logged_in.get("/api/automation-runs", params={"limit": 500}).status_code == 422
    assert logged_in.get("/api/automation-runs", params={"limit": 0}).status_code == 422
