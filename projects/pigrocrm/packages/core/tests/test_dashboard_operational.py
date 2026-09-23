"""§6. "What do I have to do now" -- no new economic total, and no period.

The signals get the most tests because each one is a predicate that must not drift into a
stored flag, and because each one's count is what a human acts on. Each is therefore checked
three ways: the count is right on a corpus built to make every clause of the predicate load
bearing, **the card equals the rows its own link returns** (criterion 2, the same discipline
`test_dashboard_drillthrough.py` applies to the commercial page), and the near-miss rows are
each excluded for their own named reason.

The fourth signal of §6.2 is deliberately not here: "offerta accettata, deal non vinto"
lives on the commercial dashboard, with the automation it cross-checks.

The corpus lives in this file rather than in `conftest.py`. Three separate committed
fixtures would each have to seed pipeline stages of their own and would collide on them, and
the drill-through half needs all three states present at once to prove that each filter
returns *its* rows and not merely some rows. `test_dashboard_commercial.py` establishes the
shape: committed sessions, because `DashboardService` sets the isolation level and
`db_session` holds an outer transaction open, and a teardown that removes exactly what it
wrote.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import NamedTuple
from uuid import UUID

import pytest
from sqlalchemy import Engine, delete, func, select, text
from sqlalchemy.orm import Session

from pigrocrm.core.activities.models import Activity
from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.repository import AnalyticsRepository
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.auth.models import User
from pigrocrm.core.config import Settings
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.dashboard.schemas import OperationalDashboard
from pigrocrm.core.dashboard.service import DashboardService
from pigrocrm.core.db import Base, current_week, session_factory, today_local
from pigrocrm.core.db.base import uuid7
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.deals.repository import DealRepository
from pigrocrm.core.deals.schemas import DealListQuery
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.invoices.repository import InvoiceRepository
from pigrocrm.core.invoices.schemas import InvoiceListQuery
from pigrocrm.core.pipeline.models import PipelineStage
from pigrocrm.core.timetracking.models import TimeEntry

READONLY = Actor(id=uuid7(), type="user", role="readonly")

_PREFIX = "OPER"
# Comfortably above every cardinality this corpus produces, so `len(rows)` is the whole
# result set and never a page of it -- the same constant `test_dashboard_drillthrough.py`
# uses and for the same reason.
_ALL = 200


class Seeded(NamedTuple):
    engine: Engine
    stages: dict[str, UUID]


def _require_empty(session: Session) -> None:
    """The four signals are counts over the whole register, with no period and no scope,
    so the literals below are only true if this file's rows are the only committed ones.
    A loud precondition beats an off-by-N nobody can read."""
    for model in (Invoice, TimeEntry, Deal):
        leftovers = session.execute(select(func.count(model.id))).scalar_one()
        assert leftovers == 0, (
            f"{leftovers} committed {model.__tablename__} row(s) were already present; the "
            "signal counts on this dashboard have no scope, so leftovers change them."
        )


@pytest.fixture
def seeded(db_engine: Engine) -> Iterator[Seeded]:
    """One corpus carrying all four signals plus the rows each of them must exclude.

    Every near miss differs from a wanted row in **exactly one** clause, which is what makes
    the negative tests below able to fail one at a time:

      * `fatturato non vinto` -- an invoiced deal on an *open* stage. The invoiced deal on a
        `perso` stage is the row that separates `tipo == "open"` from `tipo != "won"`, and
        the drill-through must not absorb it: a lost deal that was invoiced is a different
        problem with a different remedy.
      * `vinto da fatturare` -- a won deal with billable, unbilled hours. Beside it: a won
        deal whose hours are already on an invoice line, one whose hours are not billable,
        one whose entry is soft-deleted, and an *open* deal with unbilled billable hours.
      * `scaduto non incassato` -- an issued, unpaid invoice past its due date. Beside it:
        one due *today* (the row that separates `<` from `<=`), one with no due date at
        all, one already collected, and one annulled.
    """
    factory = session_factory(db_engine)
    oggi = today_local()
    with factory() as session:
        _require_empty(session)
        stages = {
            code: PipelineStage(
                nome=f"{_PREFIX} {code}",
                posizione=index,
                probabilita_default=probabilita,
                tipo=tipo,
            )
            for index, (code, tipo, probabilita) in enumerate(
                (("aperto", "open", 20), ("vinto", "won", 100), ("perso", "lost", 0))
            )
        }
        customer = Customer(ragione_sociale=f"{_PREFIX} Cliente", nazione="IT", custom_fields={})
        user = User(
            email=f"{_PREFIX.lower()}-{uuid7()}@example.test",
            password_hash="x",
            nome="Operatore",
            ruolo="collaboratore",
        )
        session.add_all([*stages.values(), customer, user])
        session.flush()

        def _deal(nome: str, stage: str) -> Deal:
            deal = Deal(
                nome=f"{_PREFIX} {nome}",
                customer_id=customer.id,
                pipeline_stage_id=stages[stage].id,
                probabilita=stages[stage].probabilita_default,
                custom_fields={},
            )
            session.add(deal)
            session.flush()
            return deal

        def _invoice(
            deal: Deal | None,
            *,
            stato: str = "emessa",
            stato_pagamento: str = "da_incassare",
            data_scadenza: date | None = None,
        ) -> Invoice:
            invoice = Invoice(
                customer_id=customer.id,
                deal_id=None if deal is None else deal.id,
                tipo="fattura",
                stato=stato,
                stato_pagamento=stato_pagamento,
                imponibile=Decimal("100.00"),
                imposta=Decimal("0.00"),
                bollo=Decimal("0.00"),
                totale=Decimal("100.00"),
                data_emissione=date(2026, 3, 1),
                data_scadenza=data_scadenza,
                tipo_documento="TD01",
                divisa="EUR",
                custom_fields={},
            )
            session.add(invoice)
            session.flush()
            return invoice

        def _entry(
            deal: Deal,
            *,
            ore: str = "8.00",
            fatturabile: bool = True,
            invoice_line_id: UUID | None = None,
            deleted: bool = False,
        ) -> TimeEntry:
            entry = TimeEntry(
                deal_id=deal.id,
                user_id=user.id,
                # Inside the current week, so the same rows also give the week card
                # something to show: a signal corpus that left the week empty would leave
                # half this dashboard untested.
                data=oggi,
                ore=Decimal(ore),
                descrizione="Lavorazione",
                fatturabile=fatturabile,
                tariffa_applicata=Decimal("50.000000"),
                tariffa_origine="manuale",
                costo_applicato=None,
                costo_origine="assente",
                invoice_line_id=invoice_line_id,
                deleted_at=datetime.now(UTC) if deleted else None,
                custom_fields={},
            )
            session.add(entry)
            session.flush()
            return entry

        # -- fatturato ma non vinto ------------------------------------------------
        fatturato_aperto = _deal("fatturato aperto", "aperto")
        _invoice(fatturato_aperto, data_scadenza=date(2026, 4, 1))  # also the overdue one
        # A second invoice on the same deal: the signal counts *deals*, so this must not
        # make it two.
        _invoice(fatturato_aperto, stato_pagamento="incassata")
        fatturato_perso = _deal("fatturato perso", "perso")
        _invoice(fatturato_perso, stato_pagamento="incassata")
        # An invoiced deal already won: the state the signal exists to say is missing.
        fatturato_vinto = _deal("fatturato vinto", "vinto")
        _invoice(fatturato_vinto, stato_pagamento="incassata")

        # -- vinto ma da fatturare -------------------------------------------------
        vinto_arretrato = _deal("vinto arretrato", "vinto")
        # Two entries on one deal, for the same reason: one signal, not two.
        _entry(vinto_arretrato)
        _entry(vinto_arretrato, ore="2.00")
        vinto_gia_fatturato = _deal("vinto gia fatturato", "vinto")
        fattura_righe = _invoice(vinto_gia_fatturato, stato_pagamento="incassata")
        line = InvoiceLine(
            invoice_id=fattura_righe.id,
            numero_linea=1,
            descrizione="Ore",
            quantita=Decimal("8.000000"),
            prezzo_unitario=Decimal("50.000000"),
            prezzo_totale=Decimal("400.00"),
            aliquota_iva=Decimal("22.00"),
        )
        session.add(line)
        session.flush()
        _entry(vinto_gia_fatturato, invoice_line_id=line.id)
        _entry(_deal("vinto non fatturabile", "vinto"), fatturabile=False)
        _entry(_deal("vinto cancellato", "vinto"), deleted=True)
        _entry(_deal("aperto arretrato", "aperto"))

        # -- scaduto e non incassato -----------------------------------------------
        # The three near misses of the overdue predicate, each differing in one clause.
        _invoice(None, data_scadenza=oggi)  # due today is not overdue: `<`, not `<=`
        _invoice(None, data_scadenza=None)  # a null due date is never overdue
        _invoice(None, stato_pagamento="incassata", data_scadenza=date(2026, 1, 1))
        _invoice(None, stato="annullata", data_scadenza=date(2026, 1, 1))

        # -- concentrazione sopra soglia (REB-371) ------------------------------
        # Two customers, both outside the single-customer register the other three
        # signals share. `anno` is set explicitly here (§7.1's fiscal register value,
        # never derived from `data_emissione`) because `revenue_by_customer` filters
        # by it, and every invoice above leaves it `None` on purpose -- the value one
        # never issued through `InvoiceService` carries. 900.00 against 100.00 puts
        # one customer's own share at 0.90 (over the default 0.30 threshold) and the
        # other's at 0.10 (under it), clear of the boundary so the ordinary case needs
        # no exact-float care.
        concentrato = Customer(
            ragione_sociale=f"{_PREFIX} Concentrato", nazione="IT", custom_fields={}
        )
        diluito = Customer(ragione_sociale=f"{_PREFIX} Diluito", nazione="IT", custom_fields={})
        session.add_all([concentrato, diluito])
        session.flush()
        session.add_all(
            [
                Invoice(
                    customer_id=concentrato.id,
                    tipo="fattura",
                    stato="emessa",
                    anno=oggi.year,
                    numero=9001,
                    stato_pagamento="incassata",
                    imponibile=Decimal("900.00"),
                    imposta=Decimal("0.00"),
                    bollo=Decimal("0.00"),
                    totale=Decimal("900.00"),
                    data_emissione=oggi,
                    tipo_documento="TD01",
                    divisa="EUR",
                    custom_fields={},
                ),
                Invoice(
                    customer_id=diluito.id,
                    tipo="fattura",
                    stato="emessa",
                    anno=oggi.year,
                    numero=9002,
                    stato_pagamento="incassata",
                    imponibile=Decimal("100.00"),
                    imposta=Decimal("0.00"),
                    bollo=Decimal("0.00"),
                    totale=Decimal("100.00"),
                    data_emissione=oggi,
                    tipo_documento="TD01",
                    divisa="EUR",
                    custom_fields={},
                ),
            ]
        )
        session.flush()

        session.add(
            Activity(
                entity_type="deal",
                entity_id=vinto_arretrato.id,
                kind=f"{_PREFIX}.test",
                actor_id=None,
                actor_type="system",
                payload={},
                occurred_at=datetime.now(UTC),
            )
        )
        session.commit()
        stage_ids = {code: stage.id for code, stage in stages.items()}
    try:
        yield Seeded(db_engine, stage_ids)
    finally:
        with factory() as session:
            # Scoped to this file's own rows in every table, deepest first. A wholesale
            # `DELETE FROM invoices` would be shorter and would also destroy anything a
            # future suite commits -- `test_dashboard_commercial.py` empties
            # `pipeline_stages` wholesale only because it is the file that seeds the
            # defaults, which is not the case here.
            corpus_customers = select(Customer.id).where(
                Customer.ragione_sociale.like(f"{_PREFIX} %")
            )
            corpus_invoices = select(Invoice.id).where(Invoice.customer_id.in_(corpus_customers))
            session.execute(delete(Activity).where(Activity.kind == f"{_PREFIX}.test"))
            session.execute(
                delete(TimeEntry).where(
                    TimeEntry.deal_id.in_(select(Deal.id).where(Deal.nome.like(f"{_PREFIX} %")))
                )
            )
            session.execute(delete(InvoiceLine).where(InvoiceLine.invoice_id.in_(corpus_invoices)))
            session.execute(delete(Invoice).where(Invoice.customer_id.in_(corpus_customers)))
            session.execute(delete(Deal).where(Deal.nome.like(f"{_PREFIX} %")))
            session.execute(delete(Customer).where(Customer.ragione_sociale.like(f"{_PREFIX} %")))
            session.execute(delete(PipelineStage).where(PipelineStage.nome.like(f"{_PREFIX} %")))
            session.execute(delete(User).where(User.nome == "Operatore"))
            session.commit()


def _dashboard(engine: Engine) -> OperationalDashboard:
    with session_factory(engine)() as session:
        return DashboardService(session).get_operational_dashboard(READONLY)


def _signal(engine: Engine, codice: str) -> int:
    return next(s.conteggio for s in _dashboard(engine).segnali if s.codice == codice)


def _codice(result: OperationalDashboard, codice: str) -> int:
    return next(s.conteggio for s in result.segnali if s.codice == codice)


def _deals(engine: Engine, query: DealListQuery) -> list[Deal]:
    with session_factory(engine)() as session:
        return DealRepository(session).list(query)


def _invoices(engine: Engine, query: InvoiceListQuery) -> list[Invoice]:
    with session_factory(engine)() as session:
        return InvoiceRepository(session).list(query)


# --- the shape of the page ------------------------------------------------------------


def test_it_takes_no_period() -> None:
    """§6: the current week and a backlog are the two things that make no sense in the
    past, so there is no period parameter to get wrong."""
    signature = inspect.signature(DashboardService.get_operational_dashboard)
    assert list(signature.parameters) == ["self", "actor"]


def test_the_week_is_the_current_one(seeded: Seeded) -> None:
    result = _dashboard(seeded.engine)
    assert (result.settimana.da, result.settimana.a) == current_week()
    assert len(result.settimana.giorni) == 7
    # The corpus's own hours land in this week, so the card is not being checked empty --
    # and the days around them are still present at zero.
    assert result.settimana.ore_totali > Decimal("0.00")
    assert result.settimana.giorni_senza_ore != []


def test_the_backlog_comes_from_analytics_verbatim(seeded: Seeded) -> None:
    """§6.3: the backlog is `AnalyticsService`'s, reported field for field. Its euro value
    is a product of two columns, which `core/dashboard/` may not contain."""
    result = _dashboard(seeded.engine)
    with session_factory(seeded.engine)() as session:
        direct = AnalyticsService(session).unbilled_backlog(READONLY)
    assert result.arretrato == direct
    assert result.arretrato.ore_fatturabili_non_fatturate > Decimal("0.00")


def test_the_signals_are_present_in_order_with_their_links(seeded: Seeded) -> None:
    result = _dashboard(seeded.engine)
    assert [signal.codice for signal in result.segnali] == [
        "fatturato_non_vinto",
        "vinto_da_fatturare",
        "scaduto_non_incassato",
        "concentrazione_sopra_soglia",
    ]
    # Every signal has a drill-through, because a count with no way to see the rows behind
    # it is a number nobody can act on (§6.2, §7.2).
    assert all(signal.collegamento for signal in result.segnali)
    assert all(signal.etichetta for signal in result.segnali)


def test_the_commercial_signal_is_not_on_this_dashboard(seeded: Seeded) -> None:
    """§6.2 and §17: "offerta accettata, deal non vinto" is the automation's permanent
    cross-check and lives on the commercial dashboard, which needs no invoices -- so it
    shipped with the automation instead of a sub-plan later."""
    result = _dashboard(seeded.engine)
    assert "offerta_accettata_deal_non_vinto" not in [s.codice for s in result.segnali]


def test_no_signal_is_stored_anywhere() -> None:
    """§6.2's closing line: none of the four is memorised and none is a flag on a row. A
    stored signal is §1's second source of truth in disguise. Asserted structurally: no
    table in the metadata carries a column named after one."""
    columns = {column.name for table in Base.metadata.tables.values() for column in table.columns}
    for forbidden in (
        "fatturato_non_vinto",
        "vinto_da_fatturare",
        "scaduto_non_incassato",
        "concentrazione_sopra_soglia",
        "segnale",
        "segnali",
    ):
        assert forbidden not in columns, forbidden


def test_the_recent_feed_is_capped_at_fifty(seeded: Seeded) -> None:
    result = _dashboard(seeded.engine)
    assert len(result.attivita_recenti) <= 50
    assert f"{_PREFIX}.test" in [row.kind for row in result.attivita_recenti]


def test_no_new_economic_total_appears_on_this_page(seeded: Seeded) -> None:
    """§6's own claim, checked by field name: this page adds no economic total. The only
    money on it is `arretrato.valore_maturato`, which is `AnalyticsService`'s and is
    labelled as accrued value, not revenue."""
    fields = set(OperationalDashboard.model_fields)
    assert "ricavi" not in fields
    assert "margine_lordo" not in fields
    assert "fatturato" not in fields
    assert "da_incassare" not in fields


def test_a_readonly_actor_sees_it(seeded: Seeded) -> None:
    assert _dashboard(seeded.engine).segnali


def test_the_service_runs_in_repeatable_read(seeded: Seeded) -> None:
    with session_factory(seeded.engine)() as session:
        DashboardService(session).get_operational_dashboard(READONLY)
        level = session.execute(text("SHOW transaction_isolation")).scalar_one()
    assert level == "repeatable read"


# --- signal 1: fatturato ma non vinto --------------------------------------------------


def test_the_invoiced_but_not_won_signal_counts_deals(seeded: Seeded) -> None:
    """You do not invoice work you have not won: almost always the stage left behind.

    One deal, not two, although it carries two invoices -- the drill-through lists deals.
    """
    assert _signal(seeded.engine, "fatturato_non_vinto") == 1


def test_the_invoiced_but_not_won_card_equals_its_drill_through(seeded: Seeded) -> None:
    """Criterion 2: one predicate, two reads. Asserted as a set of names and not as a
    count, because two queries returning one row each can still return different rows."""
    count = _signal(seeded.engine, "fatturato_non_vinto")
    rows = _deals(seeded.engine, DealListQuery(fatturato_non_vinto=True, limit=_ALL))
    assert count == len(rows)
    assert {row.nome for row in rows} == {f"{_PREFIX} fatturato aperto"}


@pytest.mark.parametrize(
    ("escluso", "perche"),
    [
        ("fatturato vinto", "il deal è già vinto, che è lo stato che il segnale cerca"),
        ("fatturato perso", "un deal perso e fatturato è un problema diverso: tipo == 'open'"),
        ("vinto arretrato", "non ha nessuna fattura emessa"),
    ],
)
def test_the_invoiced_but_not_won_filter_excludes_each_row_it_must(
    seeded: Seeded, escluso: str, perche: str
) -> None:
    """The negative half, one clause at a time. Without it a filter that returned every
    deal would still pass the equality test whenever the count was wrong the same way."""
    nomi = {
        row.nome
        for row in _deals(seeded.engine, DealListQuery(fatturato_non_vinto=True, limit=_ALL))
    }
    assert f"{_PREFIX} {escluso}" not in nomi, perche


def test_the_invoiced_but_not_won_filter_composes_with_the_others(seeded: Seeded) -> None:
    """An additional predicate, never a replacement for the statement: a drill-through that
    silently dropped the caller's own filters would answer a different question."""
    rows = _deals(
        seeded.engine,
        DealListQuery(fatturato_non_vinto=True, stage_id=seeded.stages["vinto"], limit=_ALL),
    )
    assert rows == []


# --- signal 2: vinto ma da fatturare ---------------------------------------------------


def test_the_won_but_to_invoice_signal_counts_deals_with_unbilled_billable_hours(
    seeded: Seeded,
) -> None:
    """The `da fatturare` state slice 4 §7.3 already defines, counted here rather than
    redefined. One deal although it carries two unbilled entries."""
    assert _signal(seeded.engine, "vinto_da_fatturare") == 1


def test_the_won_but_to_invoice_card_equals_its_drill_through(seeded: Seeded) -> None:
    count = _signal(seeded.engine, "vinto_da_fatturare")
    rows = _deals(seeded.engine, DealListQuery(da_fatturare=True, limit=_ALL))
    assert count == len(rows)
    assert {row.nome for row in rows} == {f"{_PREFIX} vinto arretrato"}


@pytest.mark.parametrize(
    ("escluso", "perche"),
    [
        ("vinto gia fatturato", "le sue ore sono già su una riga di fattura"),
        ("vinto non fatturabile", "le ore non sono fatturabili"),
        ("vinto cancellato", "la voce di tempo è cancellata"),
        ("aperto arretrato", "il deal non è vinto"),
        ("fatturato vinto", "non ha nessuna ora registrata"),
    ],
)
def test_the_won_but_to_invoice_filter_excludes_each_row_it_must(
    seeded: Seeded, escluso: str, perche: str
) -> None:
    nomi = {row.nome for row in _deals(seeded.engine, DealListQuery(da_fatturare=True, limit=_ALL))}
    assert f"{_PREFIX} {escluso}" not in nomi, perche


def test_the_won_but_to_invoice_filter_composes_with_the_others(seeded: Seeded) -> None:
    rows = _deals(
        seeded.engine,
        DealListQuery(da_fatturare=True, stage_id=seeded.stages["aperto"], limit=_ALL),
    )
    assert rows == []


# --- signal 3: scaduto e non incassato -------------------------------------------------


def test_the_overdue_signal_counts_and_sends_nothing(seeded: Seeded) -> None:
    """§6.2: it is the candidate list of slice 5 §7.1's reminders, counted. The count sends
    nothing, and nothing about a reminder is reachable from this response."""
    result = _dashboard(seeded.engine)
    signal = next(s for s in result.segnali if s.codice == "scaduto_non_incassato")
    assert signal.conteggio == 1
    assert "sollecito" not in result.model_dump_json()


def test_the_overdue_card_equals_its_drill_through(seeded: Seeded) -> None:
    count = _signal(seeded.engine, "scaduto_non_incassato")
    rows = _invoices(seeded.engine, InvoiceListQuery(scadute=True, limit=_ALL))
    assert count == len(rows)
    assert {row.data_scadenza for row in rows} == {date(2026, 4, 1)}


def test_the_overdue_filter_excludes_each_row_it_must(seeded: Seeded) -> None:
    """One row per clause of `_overdue_predicate`, each differing in exactly one: due today
    (`<`, not `<=`), no due date at all, already collected, and annulled."""
    rows = _invoices(seeded.engine, InvoiceListQuery(scadute=True, limit=_ALL))
    scadenze = {row.data_scadenza for row in rows}
    stati = {(row.stato, row.stato_pagamento) for row in rows}
    assert today_local() not in scadenze, "una fattura in scadenza oggi non è scaduta"
    assert None not in scadenze, "una scadenza assente non è mai scaduta"
    assert stati == {("emessa", "da_incassare")}


def test_the_overdue_filter_composes_with_the_others(seeded: Seeded) -> None:
    rows = _invoices(seeded.engine, InvoiceListQuery(scadute=True, stato="annullata", limit=_ALL))
    assert rows == []


# --- signal 4: concentrazione sopra soglia (REB-371) -----------------------------------


def test_the_concentration_signal_counts_customers_over_the_configured_share(
    seeded: Seeded,
) -> None:
    """`Concentrato` holds 0.90 of the year's two-customer register, well past the
    default 0.30 threshold `Settings.concentrazione_soglia_preferita` carries;
    `Diluito`'s 0.10 does not."""
    assert _signal(seeded.engine, "concentrazione_sopra_soglia") == 1


def test_the_concentration_signal_names_the_customer_that_crosses_it(seeded: Seeded) -> None:
    """Criterion 2, shaped for this signal: not a filtered list (there is none), but the
    same `AnalyticsRepository.revenue_by_customer` rows the economic tab's own
    concentration table would show, checked by name rather than only by count."""
    with session_factory(seeded.engine)() as session:
        rows = AnalyticsRepository(session).revenue_by_customer(today_local().year)
    sopra = {row.ragione_sociale for row in rows if row.quota > 0.30}
    assert sopra == {f"{_PREFIX} Concentrato"}


def test_the_concentration_signal_reflects_a_differently_configured_threshold(
    seeded: Seeded,
) -> None:
    """The threshold is `Settings.concentrazione_soglia_preferita`, read through the
    `DashboardService` the same way every other per-space setting reaches a service --
    not a constant this module could drift from without a test noticing."""
    with session_factory(seeded.engine)() as session:
        stringente = Settings(
            _env_file=None,
            concentrazione_soglia_preferita=0.95,  # type: ignore[call-arg]
        )
        alto = DashboardService(session, stringente).get_operational_dashboard(READONLY)
    assert _codice(alto, "concentrazione_sopra_soglia") == 0

    with session_factory(seeded.engine)() as session:
        permissivo = Settings(
            _env_file=None,
            concentrazione_soglia_preferita=0.05,  # type: ignore[call-arg]
        )
        basso = DashboardService(session, permissivo).get_operational_dashboard(READONLY)
    assert _codice(basso, "concentrazione_sopra_soglia") == 2


def test_the_concentration_signal_link_is_the_economic_tab(seeded: Seeded) -> None:
    """Unlike the other three, this link is not a filtered list: there is no "customers
    over the threshold" endpoint, only the concentration table itself, on the tab that
    already renders it."""
    signal = next(
        s for s in _dashboard(seeded.engine).segnali if s.codice == "concentrazione_sopra_soglia"
    )
    assert signal.collegamento == "/app/?tab=economica"
    assert signal.etichetta
